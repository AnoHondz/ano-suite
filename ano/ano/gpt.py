"""OpenAI GPT as Ano's second brain, with the same tools and instructions as Claude, plus the brain switch."""
import base64
import json
import re

from .claude import ClaudeError, MAX_TOOL_STEPS, build_system, trim_history, needs_web, COMPLEX_HINT
from .tool_media import ToolImage
from .execution import RunBudget
from .tools import user_turn
from .context import compact_compatible_messages, fast_history
from .local_profile import compact_system, compact_tools, short_tools, stamp, fast_system, task_tools, task_context, find_tools, chat_system
from .model_policy import is_loopback_url
from .intent import action_request
from .local_limits import wait_allowance, turn_allowance
from .local_profile import word_creation_request, requested_slide_total
from .capability_context import integration_context

GPT_MODEL = 'gpt-5.5'
LOCAL_TIMEOUT = 900.0  # seconds; see reply()


LOCAL_URL = 'http://localhost:11434/v1'  # Ollama's OpenAI-compatible endpoint


class GPTClient:
    name = 'GPT-5.5'

    def __init__(self, api_key, toolbox=None, model=GPT_MODEL, connect=None, rules='', base_url=None, name=None,
                 output_tokens=16000, profile='full', vision_model=None, memory_context=None):
        self._api_key = api_key
        self.toolbox = toolbox
        self.model = model
        self.connect = connect
        self.rules = rules
        self.user_rules = rules  # the user's own standing instructions, without any agent rules added
        self.base_url = base_url  # set for a local/OpenAI-compatible server such as Ollama
        self.profile = profile  # 'compact' for a fine-tuned local model (see local_profile.py)
        self.vision_model = vision_model
        self.output_tokens = max(256, min(int(output_tokens), 16000))
        self.memory_context = memory_context
        if name:
            self.name = name

    def warm(self):
        """Have a local compact-profile model read the fixed prompt and tools once, before the first message.

        Ollama then reuses that cached reading, so the first reply skips about a minute of prompt reading.
        """
        if self.profile != 'compact' or not is_loopback_url(self.base_url or ''):
            return
        from openai import OpenAI
        api = self.connect() if self.connect else OpenAI(api_key=self._api_key, base_url=self.base_url,
                                                         timeout=LOCAL_TIMEOUT, max_retries=0)
        definitions = self.toolbox.definitions() if self.toolbox is not None else []
        api.chat.completions.create(
            model=self.model, max_tokens=1, temperature=0.2, reasoning_effort='none', tools=compact_tools(definitions),
            # The same system text a compact reply sends, integration note included, or the cache would not match.
            messages=[{'role': 'system', 'content': compact_system(self.user_rules) + integration_context(definitions)},
                      {'role': 'user', 'content': stamp('Hello')}])

    def reply(self, history, screenshot=None, activity_context=None, progress=None, partial=None):
        from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
        chat = self.profile == 'chat'
        paid = self.profile == 'paid'
        writer = self.profile == 'writer'
        fast = self.profile in ('fast', 'chat', 'writer', 'paid')
        compact = self.profile in ('compact', 'fast', 'chat', 'writer', 'paid')
        if compact:
            context = '\n'.join(item['body'] for item in history[-3:] if item['role'] == 'user')
            if paid:
                system = compact_system(self.user_rules) + (
                    '\nYou are the OpenAI brain, and every reply costs the user money, so finish tasks in as few turns as possible. '
                    'Carry out the latest request end to end with the tools: when details are missing, choose sensible defaults '
                    'and say which ones you chose instead of asking. Never ask the user to confirm something they already asked for. '
                    'Ask a question only when you truly cannot continue (for example a login, payment, or a choice only the user can make). '
                    'Use the earlier messages to understand short follow-ups such as "yes" or "do it". '
                    'Never repeat an instruction as an answer or claim unverified success; if a tool is blocked, say exactly what blocked it.')
            elif writer:
                system = ('Write the requested draft clearly, following its requested length and structure. '
                          'Use only supplied facts. Never claim to browse or perform app actions. '
                          'No tools are available. /no_think\n' + self.user_rules)
            else:
                system = chat_system(self.user_rules) if chat else fast_system(self.user_rules, context) if fast else compact_system(self.user_rules)
        else:
            system = build_system(self.toolbox, False, activity_context, self.rules)
        if self.memory_context is not None and not paid and is_loopback_url(self.base_url or ''):
            saved = self.memory_context(history[-1]['body'] if history else '')
            if saved:
                system += '\n\n' + saved
        messages = [{'role': 'system', 'content': system}]
        recent = trim_history(history)
        if fast:
            recent = fast_history(recent)
            if sum(len(item['content']) for item in recent) > 24000:
                raise ClaudeError('This conversation or source is too large for a quick local request. '
                                  'Start a new chat or split the source into smaller sections; no tool action ran.')
        messages += recent
        if compact and not (chat or writer) and messages[-1]['role'] == 'user' and isinstance(messages[-1]['content'], str):
            messages[-1]['content'] = stamp(messages[-1]['content'], context=activity_context)
        if screenshot is not None:
            if messages[-1]['role'] != 'user':
                raise ClaudeError('A screenshot requires a question.')
            image = 'data:image/jpeg;base64,' + base64.b64encode(screenshot).decode('ascii')
            messages[-1]['content'] = [{'type': 'text', 'text': messages[-1]['content']},
                                       {'type': 'image_url', 'image_url': {'url': image}}]
        definitions = self.toolbox.definitions() if self.toolbox is not None else []
        if chat or writer:
            tools = []
        elif paid:
            # A capable model picks tools itself, and an unchanging list lets OpenAI cache the prompt.
            tools = short_tools(definitions)
        elif fast:
            latest = task_context(history)
            tools = task_tools(definitions, latest, screenshot is not None)
        elif compact:
            tools = compact_tools(definitions)
        else:
            tools = [{'type': 'function', 'function': {'name': item['name'], 'description': item['description'],
                                                       'parameters': item['input_schema']}} for item in definitions]
        if not (chat or writer):
            messages[0]['content'] += integration_context(definitions)
        request = {'tools': tools} if tools else {}
        advertised_tools = {tool['function']['name'] for tool in tools}
        if paid:
            request['reasoning_effort'] = 'minimal'
            request['parallel_tool_calls'] = False
        elif compact:
            request['reasoning_effort'] = 'none'  # Ollama: answer directly, as the model was trained to
            # Ollama's OpenAI endpoint otherwise samples at 1.0, ignoring the Modelfile's 0.2: small models ramble.
            request['temperature'] = 0.2
        token_limit = {'max_tokens' if self.base_url and not paid else 'max_completion_tokens': self.output_tokens}
        notes = []
        message = None
        # A laptop CPU reads the prompt at ~50 tokens/s and writes ~3 tokens/s, so one local step can
        # take several minutes; cutting it off at 5 minutes discarded answers that were nearly done.
        timeout = LOCAL_TIMEOUT if is_loopback_url(self.base_url or '') else 300.0
        api = self.connect() if self.connect else OpenAI(api_key=self._api_key, base_url=self.base_url,
                                                         timeout=timeout, max_retries=0)
        local_fast = fast and not paid and is_loopback_url(self.base_url or '')
        initial_arguments = dict(messages=messages, **token_limit, **request)
        seconds = (turn_allowance(initial_arguments, conversation=chat or writer) if local_fast else
                   600 if paid else 120 if fast else None)
        budget = RunBudget(self.toolbox, MAX_TOOL_STEPS, max_seconds=seconds)
        if not paid and budget.roaming:
            # Free roam is explicitly selected for longer local work. Per-call
            # timeouts, permission revocation, Pause and failure bounds remain.
            # Paid tasks always retain their separate time/spend limits.
            budget.deadline = None
        reply_model = self.vision_model if screenshot is not None and self.vision_model else self.model
        label = 'The configured model' if self.base_url else 'GPT'
        try:
            nudges = 0
            action_recovery_used = False
            while budget.next():
                compact_compatible_messages(messages)
                if progress:
                    progress(f'{self.name} · Paid task · Budget limits active…' if paid else
                             'Reading the screen locally…' if reply_model == self.vision_model else 'Planning locally…')
                arguments = dict(model=reply_model, messages=messages, **token_limit, **request)
                if fast:
                    from .local_request import request as local_request
                    response = local_request(api, arguments, budget,
                        timeout=wait_allowance(arguments) if local_fast else 180,
                        progress=progress if local_fast else None,use_gate=local_fast)
                else:
                    response = api.chat.completions.create(**arguments)
                if not response.choices or response.choices[0].message is None:
                    raise ClaudeError(budget.failure_message(f'{label} returned an empty response.'))
                choice = response.choices[0]
                message = choice.message
                if not message.tool_calls:
                    pending = budget.continuation()
                    if pending and nudges < 2:
                        messages.append({'role': 'assistant', 'content': message.content or ''})
                        messages.append({'role': 'user', 'content': '[Ano task tracker]\n' + pending})
                        nudges += 1
                        continue
                    latest = history[-1]['body'] if history else ''
                    if (fast and not chat and not writer and not paid and not budget.tools_started
                            and not action_recovery_used and action_request(latest)
                            and advertised_tools - {'find_tool'}):
                        # No action has run, so one planning recovery cannot replay
                        # typing, clicks or submissions. Keep the original request
                        # and permission/budget checks; ask for actual state first.
                        messages.append({'role': 'assistant', 'content': message.content or ''})
                        messages.append({'role': 'user', 'content':
                            '[Ano task tracker]\nThe user requested an app action and no tool ran. '
                            'Use an available read-only discovery tool first to inspect the relevant app or connection. '
                            'Do not invent window or tab IDs. If an essential detail is still missing after inspection, '
                            'ask one specific question; otherwise continue the original task. '
                            'Do not repeat a generic refusal or claim completion.'})
                        action_recovery_used = True
                        if self.vision_model:
                            reply_model = self.vision_model
                        if progress:
                            progress('No action started; retrying the local planner once…')
                        continue
                    break
                messages.append({'role': 'assistant', 'content': message.content or '', 'tool_calls': [
                    {'id': call.id, 'type': 'function',
                     'function': {'name': call.function.name, 'arguments': call.function.arguments}}
                    for call in message.tool_calls]})
                images = []
                for call in message.tool_calls:
                    try:
                        arguments = json.loads(call.function.arguments or '{}')
                    except ValueError:
                        arguments = None
                    if not budget.can_act():
                        output, note, failed = budget.reason, '', True
                    elif fast and not chat and not writer and call.function.name == 'find_tool' and isinstance(arguments, dict):
                        query = arguments.get('query', '')
                        found = find_tools(definitions, query) if isinstance(query, str) else []
                        for tool in found:
                            if tool['function']['name'] not in advertised_tools:
                                tools.append(tool)
                                advertised_tools.add(tool['function']['name'])
                        output = ('Available tools: ' + ', '.join(tool['function']['name'] for tool in found)
                                  if found else 'No matching tools. Describe the capability or use a specific tool name.')
                        note, failed = '', not bool(found)
                    elif (word_creation_request(task_context(history)) and call.function.name in
                          ('write_document', 'append_document', 'replace_document_text', 'draft_document')):
                        output, note, failed = ('The user requested a Word .docx deliverable. This tool writes plain text or Markdown; '
                                                'nothing was dispatched. Use write_word_document for supplied content or '
                                                'draft_word_document if available. Do not claim a Word file exists without its receipt.', '', True)
                    elif compact and call.function.name not in advertised_tools:
                        output, note, failed = 'That tool is not available in this model profile. Use only the supplied tools.', '', True
                    elif isinstance(arguments, dict):
                        if call.function.name == 'create_presentation' and 'total_slides' not in arguments:
                            # Models often forget the title slide counts; the user's own words say the total.
                            total = requested_slide_total(task_context(history))
                            if total:
                                arguments['total_slides'] = total
                        if self.toolbox is None:
                            output, note, failed = 'No tools are available for this request.', '', True
                        else:
                            with user_turn(self.toolbox, history[-1]['body'] if history else ''):
                                if progress:
                                    labels = {'list_startup_apps': 'Checking startup apps…',
                                              'set_ano_autostart': 'Updating Ano’s startup setting…',
                                              'open_startup_settings': 'Opening Startup Apps…',
                                              'read_tab': 'Reading the browser page…',
                                              'list_tabs': 'Checking browser tabs…',
                                              'desktop_observe': 'Inspecting the screen…',
                                              'read_file': 'Reading the requested file…'}
                                    progress(labels.get(call.function.name, 'Using a local tool…'))
                                budget.tools_started = True
                                output, note, failed = self.toolbox.run(call.function.name, arguments)
                    else:
                        output, note, failed = 'The arguments were not valid JSON.', '', True
                    budget.record(call.function.name, arguments, failed, output)
                    if not failed and call.function.name in ('write_word_document', 'draft_word_document'):
                        for rejected in ('write_document', 'append_document', 'replace_document_text', 'draft_document'):
                            budget.recovery.pop(rejected, None)
                            budget.errors.pop(rejected, None)
                    if note:
                        notes.append(note)
                    # A pure inventory lookup already has its complete answer.
                    # Avoid a second generation that can omit or rewrite entries.
                    latest = history[-1]['body'] if history else ''
                    if (fast and call.function.name == 'list_startup_apps' and not failed
                            and len(message.tool_calls) == 1 and not budget.continuation()
                            and re.match(r'^(?:list|show|which|what)\b', latest.strip(), re.I)
                            and not re.search(r'\b(then|and|enable|disable|remove|turn|set)\b', latest, re.I)):
                        return str(output) + ('\n\n' + '\n'.join('· ' + item for item in notes) if notes else '')
                    if isinstance(output, ToolImage):
                        if self.vision_model:
                            reply_model = self.vision_model
                        images.append({'role': 'user', 'content': output.gpt()})
                        output = output.text
                    messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': output})
                messages.extend(images)  # All tool-call ids are answered before appending image observations.
        except APITimeoutError:
            raise ClaudeError(budget.failure_message(f'{label} timed out.')) from None
        except APIConnectionError:
            if self.base_url:
                raise ClaudeError(budget.failure_message(f'Could not reach the configured model server at {self.base_url}. '
                                  'Check that the server is running and the model is installed.')) from None
            raise ClaudeError(budget.failure_message('Could not reach OpenAI. Check your internet connection.')) from None
        except APIStatusError as error:
            if self.base_url:
                reasons = {400: 'The configured model server rejected the request. The context may be too large, or the model may not support these tools or images.',
                           401: 'The configured model server rejected its API key.',
                           404: f'The model server could not find {self.model}. Check the installed model name.',
                           429: 'The configured model server has reached a rate limit or quota. It is not unlimited.'}
                raise ClaudeError(budget.failure_message(reasons.get(error.status_code,
                    'The configured model server rejected the request. Check model support for tools and images.'))) from None
            reasons = {401: 'OpenAI rejected the API key. Replace it with python -m ano.configure openai.',
                       429: 'OpenAI rate limit or quota reached. Check your OpenAI billing.'}
            raise ClaudeError(budget.failure_message(reasons.get(error.status_code, 'GPT is unavailable. Please try again later.'))) from None
        if message is None:
            raise ClaudeError(budget.reason or 'Control stopped before the request started.')
        text = (message.content or '').strip()
        if budget.continuation():
            text = budget.unfinished()
        if message.tool_calls:
            text = (text + '\n\n' if text else '') + '[' + (budget.reason or 'Stopped before the task was completed.') + ']'
        if not text:
            raise ClaudeError(budget.failure_message(f'{label} returned no text.'))
        if fast and not chat and not writer and not paid and not budget.tools_started:
            latest = history[-1]['body'] if history else ''
            if action_request(latest):
                text = 'Not completed: no action tool ran.\n\nLocal model response:\n' + text
        if choice.finish_reason == 'length':
            text += '\n\n[Response length limit reached.]'
        if notes:
            text += '\n\n' + '\n'.join('· ' + note for note in notes)
        return text


class Brains:
    """Chooses who answers. Modes: 'economy' (free brain for simple asks, Claude for hard ones),
    'auto' (Claude, GPT takes over if Claude fails), 'claude', 'gpt', or 'local'."""

    def __init__(self, claude=None, gpt=None, local=None, mode='auto'):
        self.claude = claude
        self.gpt = gpt
        self.local = local
        self.modes = ((['economy'] if claude and local else [])
                      + (['auto'] if claude and gpt else [])
                      + (['claude'] if claude else [])
                      + (['gpt'] if gpt else [])
                      + (['local'] if local else []))
        self.mode = mode if mode in self.modes else self.modes[0]

    def _hard(self, history, kwargs):
        """A request that deserves the paid model: has an image, needs the web, or looks multi-step/control/form."""
        latest = history[-1]['body'] if history else ''
        return (kwargs.get('screenshot') is not None or needs_web(latest)
                or bool(COMPLEX_HINT.search(latest or '')))

    def reply(self, history, **kwargs):
        if self.mode == 'economy' and self.claude and self.local:
            if self._hard(history, kwargs):
                return self.claude.reply(history, **kwargs)
            try:
                toolbox = getattr(self.local, 'toolbox', None)
                before = getattr(toolbox, 'executions', None)
                return self.local.reply(history, **kwargs)  # free for everyday requests
            except ClaudeError as error:
                after = getattr(toolbox, 'executions', None)
                if isinstance(before, int) and isinstance(after, int) and after > before:
                    raise ClaudeError('The local model stopped after using tools. Paid fallback was skipped to avoid repeating actions.') from None
                # Free brain stumbled on a simple ask; Claude finishes and we note it.
                text = self.claude.reply(history, **kwargs)
                return f'{text}\n\n· Used Claude because the free brain failed: {error}'
        if self.mode == 'local' or (self.claude is None and self.gpt is None):
            return self.local.reply(history, **kwargs)
        if self.mode == 'gpt' or self.claude is None:
            return self.gpt.reply(history, **kwargs)
        toolbox = getattr(self.claude, 'toolbox', None)
        before = getattr(toolbox, 'executions', None)
        try:
            return self.claude.reply(history, **kwargs)
        except ClaudeError as error:
            if self.mode != 'auto':
                raise
            after = getattr(toolbox, 'executions', None)
            if isinstance(before, int) and isinstance(after, int) and after > before:
                raise ClaudeError('Claude stopped after using tools. Automatic fallback was skipped to avoid repeating actions. Review the result before retrying.') from None
            # Claude failed before answering; GPT answers instead and says so.
            text = self.gpt.reply(history, **kwargs)
            return f'{text}\n\n· Answered by {self.gpt.name} because Claude was unavailable: {error}'
