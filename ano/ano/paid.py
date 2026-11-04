"""Paid OpenAI brain: one-task opt-in, or the default when selected. Fail-closed,
persistent reservations protect small budgets.

Amounts are conservative estimates at published standard rates, not the account
balance. Other apps and future price changes are outside this ledger.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import sqlite3
from types import SimpleNamespace

from .claude import ClaudeError

MODEL = 'gpt-5-nano-2025-08-07'
# USD per million input/output tokens, and the date the model is blocked (None: no announced retirement).
PRICES = {'gpt-4.1': (2.00, 8.00, None),
          'gpt-4.1-mini': (.40, 1.60, None),
          'gpt-5-nano-2025-08-07': (.05, .40, '2026-12-11')}
CACHED_INPUT = {'gpt-4.1': .50, 'gpt-4.1-mini': .10, 'gpt-5-nano-2025-08-07': .005}
TASK_USD, DAILY_USD, TOTAL_USD = .50, 2.0, 20.0
OUTPUT_TOKENS, MAX_CALLS = 2048, 24
CONTEXT_BYTES = 160000  # every tool schema plus a working conversation; GPT-4.1 accepts far more
# Per-model request ceilings; the per-task, daily and total dollar caps still bound every call.
# GPT-5 nano costs 1/40th of GPT-4.1 per input token, so a tool-heavy task fits without exceeding those caps.
MODEL_CONTEXT_BYTES = {'gpt-4.1-mini': 400000, 'gpt-5-nano-2025-08-07': 600000}


class SpendLedger:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS charges (id INTEGER PRIMARY KEY, day TEXT NOT NULL, usd REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS message_routes (id INTEGER PRIMARY KEY, day TEXT NOT NULL, '
                       'route TEXT NOT NULL, status TEXT NOT NULL, message_chars INTEGER NOT NULL, '
                       'estimated_saved_usd REAL NOT NULL, baseline_input INTEGER NOT NULL, baseline_output INTEGER NOT NULL, model TEXT NOT NULL)')

    def record_route(self, route, status, chars, would_pay=False, model=MODEL):
        if route not in ('local_command', 'local_model', 'openai') or status not in ('completed', 'failed'):
            raise ValueError('Invalid message route')
        # Counterfactual estimate, not a measured API bill: 1,000 input / 250 output tokens.
        baseline_in, baseline_out = 1000, 250
        prices = PRICES.get(model, PRICES[MODEL])
        saved = ((baseline_in * prices[0] + baseline_out * prices[1]) / 1_000_000
                 if would_pay and route != 'openai' and status == 'completed' else 0)
        with self.connect() as db:
            db.execute('INSERT INTO message_routes(day,route,status,message_chars,estimated_saved_usd,baseline_input,baseline_output,model) '
                       'VALUES (?,?,?,?,?,?,?,?)', (datetime.now(timezone.utc).date().isoformat(), route, status,
                       max(0, int(chars)), saved, baseline_in, baseline_out, model))

    def savings_summary(self):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            count, saved = db.execute("SELECT count(*),coalesce(sum(estimated_saved_usd),0) FROM message_routes "
                                      "WHERE day=? AND route!='openai' AND status='completed'", (day,)).fetchone()
        return f'Today (UTC): {count} handled free · ~${saved:.2f} estimated saved'

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, amount):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            total, daily = db.execute('SELECT coalesce(sum(usd),0), coalesce(sum(CASE WHEN day=? THEN usd ELSE 0 END),0) FROM charges', (day,)).fetchone()
            if total + amount > TOTAL_USD or daily + amount > DAILY_USD:
                raise ClaudeError('Ano paid budget reached. Local AI remains available; no paid request sent.')
            return db.execute('INSERT INTO charges(day,usd) VALUES (?,?)', (day, amount)).lastrowid

    def settle(self, receipt, amount):
        with self.connect() as db:
            db.execute('UPDATE charges SET usd=? WHERE id=?', (amount, receipt))

    def totals(self):
        """(today, all time) in USD, as recorded by Ano: estimates, not the OpenAI account balance."""
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            return db.execute('SELECT coalesce(sum(CASE WHEN day=? THEN usd ELSE 0 END),0), coalesce(sum(usd),0) FROM charges',
                              (day,)).fetchone()

    def report(self):
        with self.connect() as db:
            total = db.execute('SELECT coalesce(sum(usd),0) FROM charges').fetchone()[0]
        return (f'Ano paid usage/reservations: ${total:.5f}. Limits: {TASK_USD * 100:.0f}¢/task, '
                f'${DAILY_USD:.2f}/UTC day, ${TOTAL_USD:.2f} total. '
                'This is an estimate for Ano only, not your OpenAI account balance.')


class GuardedAPI:
    def __init__(self, client, ledger, model=MODEL, partial=None, permitted=None):
        self.client, self.ledger, self.model = client, ledger, model
        self.calls, self.spent = 0, 0.0
        self.partial = partial
        self.permitted = permitted
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def close(self):
        self.client.close()

    def create(self, **arguments):
        input_usd, output_usd, retirement = PRICES[self.model]
        if retirement and datetime.now(timezone.utc).date().isoformat() >= retirement:
            raise ClaudeError('This budget model has retired. No automatic paid model upgrade is allowed.')
        if arguments.get('model') != self.model:
            raise ClaudeError('A different paid model is blocked by the budget policy.')
        if self.calls >= MAX_CALLS:
            raise ClaudeError(f'Stopped at {MAX_CALLS} paid API calls. Some work may be incomplete; inspect before retrying.')
        args = deepcopy(arguments)
        images = 0
        # Count encoded text bytes conservatively; image URLs are replaced by a
        # large image-token allowance. No hosted tools or arbitrary API options.
        for msg in args.get('messages', []):
            if isinstance(msg.get('content'), list):
                for part in msg['content']:
                    if part.get('type') == 'image_url':
                        images += 1
                        part['image_url'] = '[image]'
        size = len(json.dumps(args, ensure_ascii=False).encode('utf-8'))
        if size > MODEL_CONTEXT_BYTES.get(self.model, CONTEXT_BYTES) or images > 1:
            raise ClaudeError('Paid context is too large for the economy limit. Start a focused new chat; no request sent.')
        # Tokenized JSON and prose average about four bytes per token; three keeps the estimate high.
        bound = size // 3 + 2048 + images * 16384
        limit = min(int(arguments.get('max_completion_tokens', OUTPUT_TOKENS)), OUTPUT_TOKENS)
        reserve = (bound * input_usd + limit * output_usd) / 1_000_000
        if self.spent + reserve > TASK_USD:
            raise ClaudeError(f'Stopped at the {TASK_USD * 100:.0f}¢ task estimate limit. No further paid request sent.')
        receipt = self.ledger.reserve(reserve)
        self.calls += 1
        self.spent += reserve
        # Only reasoning models accept reasoning_effort; GPT-4.1 rejects it. 'minimal' left nano narrating
        # ("I'll check now") instead of calling its tools; 'low' plans the tool calls for a fraction of a cent.
        options = {'reasoning_effort': 'low'} if self.model.startswith('gpt-5') else {}
        if arguments.get('tools'):
            options['parallel_tool_calls'] = False
        arguments = {key: value for key, value in arguments.items() if key not in ('reasoning_effort', 'parallel_tool_calls')}
        # Failures/timeouts keep the reservation: never assume a failed network
        # response was free and never retry automatically.
        if self.partial is not None:
            options.update(stream=True, stream_options={'include_usage': True})
        response = self.client.chat.completions.create(**dict(arguments, model=self.model,
            max_completion_tokens=limit, **options))
        if self.partial is not None:
            from .streaming import collect_stream
            response = collect_stream(response, self.partial, self.permitted)
        usage = getattr(response, 'usage', None)
        if (usage and isinstance(usage.prompt_tokens, int) and isinstance(usage.completion_tokens, int)
                and usage.prompt_tokens >= 0 and usage.completion_tokens >= 0):
            # Cache discounts differ by model; never apply GPT-4.1's rate to GPT-5.
            cached = getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', 0)
            cached = cached if isinstance(cached, int) and 0 <= cached <= usage.prompt_tokens else 0
            actual = ((usage.prompt_tokens - cached) * input_usd + cached * CACHED_INPUT[self.model]
                      + usage.completion_tokens * output_usd) / 1_000_000
            self.ledger.settle(receipt, actual)
            self.spent += actual - reserve
        return response


class PaidTasks:
    """In 'local' mode a paid: prefix grants exactly one paid turn. In 'openai' mode every
    message is paid unless it starts with local:. Only the user's brain switch changes the mode."""
    modes = ['local', 'openai']
    supports_progress = True
    supports_partial = True

    def __init__(self, local, toolbox, ledger, rules='', key_loader=None, client_factory=None,
                 model=MODEL, mode='local'):
        self.local, self.toolbox, self.ledger, self.rules = local, toolbox, ledger, rules
        self.key_loader, self.client_factory = key_loader, client_factory
        self.model = model if model in PRICES else MODEL
        self.mode = mode if mode in self.modes else 'local'
        self.last_paid = False
        self.last_route = 'local_model'

    def reply(self, history, **kwargs):
        self.last_route = 'local_model'
        latest = history[-1]['body'] if history else ''
        try:
            result = self._reply(history, **kwargs)
        except Exception:
            if not self.last_paid and getattr(getattr(self.local, 'routines', None), 'last_plan_uses_model', False) is True:
                self.last_route = 'local_model'
            try:
                self.ledger.record_route('openai' if self.last_paid else self.last_route, 'failed', len(latest), False, self.model)
            except Exception:
                pass  # never mask a task error or replay an action because statistics failed
            raise
        route = 'openai' if self.last_paid else self.last_route
        self.last_route = route
        try:
            self.ledger.record_route(route, 'completed', len(latest), self.mode == 'openai', self.model)
        except Exception:
            if isinstance(result, str):
                result += '\n\nRoute statistics could not be saved. Do not repeat completed actions.'
        return result

    def _reply(self, history, **kwargs):
        self.last_paid = False
        latest = history[-1]['body'] if history else ''
        prefix = r'\s*local:\s*(\S[\s\S]*)' if self.mode == 'openai' else r'\s*paid:\s*(\S[\s\S]*)'
        match = re.fullmatch(prefix, latest, re.I)
        command_text = match[1] if match else latest
        if command_text.strip().lower() == 'paid usage':
            self.last_route = 'local_command'
            return self.ledger.report()
        routines = getattr(self.local, 'routines', None)
        if routines is not None and kwargs.get('screenshot') is None:
            from .routines import LocalRoutines
            # "local: keep working on …" / "local: in the background: …" keep using the free brain on later turns.
            routines.forced_local = (self.mode == 'local' and not match) or (bool(match) and self.mode == 'openai')
            self.last_route = 'local_command'
            text = (routines.route_reply(command_text, history[-1].get('source_chat_id'), history=history)
                    if isinstance(routines, LocalRoutines) else routines.reply(command_text))
            if isinstance(text, str):
                self.last_route = 'local_model' if getattr(routines, 'last_plan_uses_model', False) is True else 'local_command'
                return text
            self.last_route = 'local_model'
        if (self.mode == 'openai') == bool(match):
            if match:
                history = [dict(item) for item in history]
                history[-1]['body'] = match[1]
            try:
                result = self.local.reply(history, **kwargs)
            except Exception:
                route = getattr(self.local, 'last_route', 'local_model')
                self.last_route = route if route in ('local_command', 'local_model') else 'local_model'
                raise
            route = getattr(self.local, 'last_route', 'local_model')
            self.last_route = route if route in ('local_command', 'local_model') else 'local_model'
            return result
        self.last_paid = True
        from .credentials import load_openai_key
        from .gpt import GPTClient
        from .brain import AGENT_RULES
        from openai import OpenAI
        key = (self.key_loader or load_openai_key)()
        if not key:
            raise ClaudeError('No OpenAI key saved. Use python -m ano.configure openai. No paid request sent.')
        client = (self.client_factory or OpenAI)(api_key=key, base_url='https://api.openai.com/v1',
                                                timeout=45, max_retries=0)
        guarded = GuardedAPI(client, self.ledger, self.model, partial=kwargs.get('partial'), permitted=self.toolbox.allowed)
        brain = GPTClient(key, self.toolbox, model=self.model, profile='paid', output_tokens=OUTPUT_TOKENS,
                          connect=lambda: guarded, rules=AGENT_RULES + '\n' + self.rules, name=f'OpenAI {self.model}')
        focused = [dict(item) for item in history]
        if match:
            focused[-1]['body'] = match[1]
        next_turn = 'next message uses local AI' if self.mode == 'local' else 'start with local: for free local AI'
        try:
            result = brain.reply(focused, **kwargs)
            receipt = f'\n\n· OpenAI {self.model} · {guarded.calls} API call(s) · estimated ${guarded.spent:.5f} · {next_turn}'
            lines = result.rstrip().splitlines()
            if lines and re.fullmatch(r'STATUS:\s*(?:DONE|CONTINUE|NEED YOU)\b[^\n]*', lines[-1], re.I):
                return '\n'.join(lines[:-1])+receipt+'\n'+lines[-1]
            return result + receipt
        finally:
            guarded.close()
