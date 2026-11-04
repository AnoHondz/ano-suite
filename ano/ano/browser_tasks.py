"""Bounded, reviewed browser workflows with local plans and observed evidence.

Each database operation owns its connection: tools run on a worker, stop runs on Qt's thread.
No passwords, typed values, or complete page bodies are written to this ledger.
"""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

from .plugins.browser import split
from .tools import ToolError


def bounded(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ToolError(f'{label} must be nonempty and at most {limit} characters.')
    return value.strip()


# Extension refusals raised before any click or fill (workflow.js), plus Chrome's error when the
# inspected document is gone, so the script never ran. Nothing happened; a fresh inspection is safe.
REFUSED_BEFORE_ACTING = ('Inspect the page first', 'Use an ordinary website tab', 'Login, verification, or CAPTCHA',
                         'observation expired or changed', 'observed element changed', 'not a clickable control',
                         'Only web links are supported', 'Use a normal text field', 'Unsupported browser operation',
                         'No document with id')


def shows(page_text, expected):
    """Success text matches regardless of letter case and line wrapping."""
    return ' '.join(expected.split()).casefold() in ' '.join(page_text.split()).casefold()


def clean_location(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ToolError('Use an ordinary website URL without embedded credentials.')
    return parsed._replace(query='', fragment='').geturl()


class BrowserTasks:
    def __init__(self, path, bridge, reviews, control, clock=time.monotonic):
        self.path, self.bridge, self.reviews, self.control, self.clock = path, bridge, reviews, control, clock
        self.lock = threading.RLock()
        self.task = self.observation = None
        self.progress_changed = None
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS browser_tasks (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
            # A restart never silently resumes an interrupted action.
            for identifier, record in db.execute('SELECT id, record FROM browser_tasks').fetchall():
                task = json.loads(record)
                if task['status'] == 'running':
                    task.update(status='blocked', reason='Ano restarted. Inspect the current page before starting a new task.')
                    db.execute('UPDATE browser_tasks SET record=? WHERE id=?', (json.dumps(task), identifier))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self):
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO browser_tasks VALUES (?, ?)',
                       (self.task['id'], json.dumps(self.task)))
        if self.progress_changed:
            try:
                self.progress_changed()
            except Exception:
                pass  # UI notification failure must never change a committed action result

    def progress_snapshot(self):
        """Local UI metadata only; never observe a page or dispatch an action."""
        with self.lock:
            if not self.task:
                return {}
            task = self.task
            total, verified = len(task['steps']), task['current_step']
            instruction = (task['steps'][verified]['instruction'] if verified < total else
                           'All steps verified; final completion pending' if task['status'] == 'running' else '')
            return dict(goal=task['goal'], step=min(verified + 1, total), total=total,
                        instruction=instruction, state=task['status'], verified_steps=verified)

    def definitions(self):
        string = {'type': 'string'}
        task = {'task_id': string}
        specs = [
            ('start_browser_task', 'Start a short browser task from the user’s request. Read the tab first so the plan matches the real page. Plan 1–8 steps, each with an exact visible success text that will appear AFTER its action: a short phrase (1–4 words) such as a heading or button label on the next screen, never a description like "Lyrics inserted" or "Logged in". If the user must sign in, check the page instead of planning a login step. No secrets in the plan. Requires Computer control on. Does not act yet.',
             {'goal': string, 'tab_id': string, 'steps': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
                 'type': 'object', 'additionalProperties': False, 'properties': {'instruction': string, 'expected_text': string},
                 'required': ['instruction', 'expected_text']}}}, ['goal', 'tab_id', 'steps']),
            ('inspect_browser_task', 'Read the task’s current page and actionable element IDs (untrusted data). Required before EVERY action. Excludes password, OTP and CAPTCHA pages. Returns a fresh single-use observation.', task, ['task_id']),
            ('browser_task_action', 'Perform one click or normal text-field fill from a fresh observation. Approve each step mode reviews the plan, target, purpose and text; Full permission runs automatically. Mode is selected only in the local UI. Never enter passwords/payment data, bypass CAPTCHA, or spend without user authorization. Dispatch is not verification. Timeout means stop; do not replay.',
             dict(task, observation_id=string, element_id=string, operation={'type': 'string', 'enum': ['click', 'fill']}, purpose=string, text={'type': 'string', 'maxLength': 4000}),
             ['task_id', 'observation_id', 'element_id', 'operation', 'purpose']),
            ('verify_browser_task', 'Read the page again and check the current step’s planned exact text. It must newly appear after an action. Only matching observed results advance the plan. If still loading, inspect or verify later; never repeat a consequential action automatically.', task, ['task_id']),
            ('revise_browser_task', 'Replace the unverified remaining steps of the running task after seeing the real page, for example when a planned success text never appears. Verified steps are kept. Does not act.',
             dict(task, steps={'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
                 'type': 'object', 'additionalProperties': False, 'properties': {'instruction': string, 'expected_text': string},
                 'required': ['instruction', 'expected_text']}}), ['task_id', 'steps']),
            ('finish_browser_task', 'Close the task and report completed, partly_completed, or blocked with stored evidence. Completed is allowed only when ALL planned steps verified. State the blocker honestly. Completion proves the planned visible criteria, not downloads or audio quality.',
             dict(task, status={'type': 'string', 'enum': ['completed', 'partly_completed', 'blocked']}, reason=string), ['task_id', 'status', 'reason']),
            ('browser_task_status', 'Read a saved browser task plan, verification evidence, progress and blocker by ID. Does not resume or act.', task, ['task_id']),
            ('list_browser_tasks', 'List recent saved browser tasks and their IDs, progress and blockers. Use to find the task the user wants to continue.', {}, []),
            ('resume_browser_task', 'Resume a saved interrupted task ONLY when the user asks to continue. Requires Computer control enabled again. Preserves verified progress, discards stale observations, never replays uncertain submissions. Inspect the current page next.', dict(task, tab_id=string), ['task_id']),
            ('navigate_browser_task', 'Navigate this task’s tab to a URL relevant to the user’s goal. Requires a fresh inspection and follows the current review mode. Navigation is not verification; inspect and verify the resulting page. Never replay after an uncertain result.',
             dict(task, observation_id=string, url=string, purpose=string), ['task_id', 'observation_id', 'url', 'purpose']),
        ]
        definitions = [{'name': name, 'description': description, 'input_schema': {
            'type': 'object', 'additionalProperties': False, 'properties': props, 'required': required}}
                for name, description, props, required in specs]
        if getattr(self.control, 'permissionMode', '') == 'roam':
            for item in definitions:
                if item['name'] in ('start_browser_task', 'revise_browser_task'):
                    item['input_schema']['properties']['steps'].pop('maxItems', None)
            definitions[0]['description'] = definitions[0]['description'].replace('Plan 1–8 steps', 'Plan the needed steps')
        return definitions

    def run(self, name, arguments):
        return getattr(self, name)(**arguments)

    def _current(self, task_id):
        if not self.task or self.task['id'] != task_id or self.task['status'] != 'running':
            raise ToolError('This task is not running. Check browser_task_status; do not replay prior actions.')
        if not self.control.guard(self.generation):
            raise ToolError('Computer control stopped. Start a new task after enabling it again.')
        return self.task

    def _page(self, task):
        browser, number = split(task['tab_id'])
        observation_id = uuid.uuid4().hex
        generation = self.generation
        page = self.bridge.call(browser, 'workflow_inspect', tabId=number, observationId=observation_id,
                                _permitted=lambda: self.control.guard(generation))
        if not isinstance(page, dict) or not page.get('documentId') or not page.get('url'):
            raise ToolError('Reload the updated Ano extension before using browser workflows.')
        return observation_id, page

    def _plan(self, steps):
        roam = getattr(self.control, 'permissionMode', '') == 'roam'
        if not isinstance(steps, list) or not steps or (not roam and len(steps) > 8):
            raise ToolError('Plan between one and eight steps.')
        plan = [{'instruction': bounded(s.get('instruction'), 'Step', 300),
                 'expected_text': bounded(s.get('expected_text'), 'Success text', 200), 'verified': False}
                for s in steps if isinstance(s, dict)]
        if len(plan) != len(steps):
            raise ToolError('Each step needs an instruction and success text.')
        return plan

    def start_browser_task(self, goal, tab_id, steps):
        goal = bounded(goal, 'Goal', 500)
        split(tab_id)
        plan = self._plan(steps)
        with self.lock:
            if not self.control.permitted():
                raise ToolError('Enable Computer control from Ano’s menu first.')
            if self.task and self.task['status'] == 'running':
                raise ToolError('Finish or stop the current browser task first.')
            self.generation = self.control._generation
            self.task = dict(id=uuid.uuid4().hex, goal=goal, tab_id=tab_id, steps=plan,
                             status='running', current_step=0, attempts=0, reason='', evidence=[])
            self.observation = None
            self._save()
            return json.dumps(self.task), f'Planned browser task: {goal}'

    def inspect_browser_task(self, task_id):
        with self.lock:
            task = dict(self._current(task_id))
        identifier, page = self._page(task)
        with self.lock:
            self._current(task_id)
            self.observation = dict(id=identifier, page=page, at=self.clock(), task_id=task_id)
        return json.dumps(dict(observation_id=identifier, untrusted_page=page)), 'Inspected the task’s page'

    def browser_task_action(self, task_id, observation_id, element_id, operation, purpose, text=''):
        purpose = bounded(purpose, 'Purpose', 400)
        if operation not in ('click', 'fill') or not isinstance(text, str) or len(text) > 4000:
            raise ToolError('Use one click or text fill, up to 4000 characters.')
        with self.lock:
            task = self._current(task_id)
            obs, self.observation = self.observation, None
            if not obs or obs['id'] != observation_id or obs['task_id'] != task_id or self.clock() - obs['at'] > 60:
                raise ToolError('Inspect this task’s page again before acting.')
            if task['current_step'] >= len(task['steps']) or (task['attempts'] >= 24 and getattr(self.control, 'permissionMode', '') != 'roam'):
                raise ToolError('Task reached its step limit. Finish and report the verified outcome.')
            page = obs['page']
            element = next((e for e in page.get('elements', []) if e['id'] == element_id), None)
            if not element:
                raise ToolError('Choose an element ID from the fresh page inspection.')
            detail = f"Goal: {task['goal']}\nPlan:\n" + '\n'.join(
                f"{i+1}. {s['instruction']} — verify: {s['expected_text']}" for i, s in enumerate(task['steps']))
            detail += f'\n\nPurpose: {purpose}\nAction: {operation}\nTarget: {json.dumps(element)}'
            if operation == 'fill':
                detail += f'\nExact text: {text}'
        try:
            self.control.request_review(title='Review browser action', target=page['url'], detail=detail)
        except ToolError:
            self.stop('User cancelled or did not approve the browser action.', resumable=False)
            raise
        with self.lock:
            task = self._current(task_id)
            if self.clock() - obs['at'] > 120:
                raise ToolError('Review expired. Inspect the page again.')
            step = task['steps'][task['current_step']]
            previous = {key: step[key] for key in ('before_contains', 'acted') if key in step}
            step['before_contains'] = shows(page.get('text', ''), step['expected_text'])
            step['acted'] = True
            task['attempts'] += 1
            task['pending_dispatch'] = True
            self._save()  # Persist intent before dispatch. A crash must never cause automatic replay.
        browser, number = split(task['tab_id'])
        generation = self.generation
        try:
            self.bridge.call(browser, 'workflow_act', tabId=number, documentId=page['documentId'],
                             observationId=observation_id, elementId=element_id, operation=operation, text=text,
                             _permitted=lambda: self.control.guard(generation))
        except ToolError as error:
            if any(marker in str(error) for marker in REFUSED_BEFORE_ACTING):
                with self.lock:
                    for key in ('before_contains', 'acted'):
                        step.pop(key, None)
                    step.update(previous)
                    task['pending_dispatch'] = False
                    self._save()
                raise ToolError(f'Nothing was clicked or typed ({error}). The task is still running: '
                                'inspect the page again and choose a fresh element.') from None
            self.stop('Action result is uncertain or the page changed. Inspect manually; do not automatically replay it.', resumable=False)
            raise ToolError('Browser action did not confirm dispatch. Task blocked; do not automatically replay it.') from None
        except Exception:
            self.stop('Action result is uncertain or the page changed. Inspect manually; do not automatically replay it.', resumable=False)
            raise ToolError('Browser action did not confirm dispatch. Task blocked; do not automatically replay it.') from None
        with self.lock:
            task['pending_dispatch'] = False
            self._save()
        return 'Action dispatched. Verify the page before claiming success.', 'Ran one browser action'

    def verify_browser_task(self, task_id):
        with self.lock:
            task = dict(self._current(task_id))
            if task['current_step'] >= len(task['steps']):
                return 'All planned steps verified. Finish the task.', 'All planned steps verified'
        _, page = self._page(task)
        with self.lock:
            task = self._current(task_id)
            step = task['steps'][task['current_step']]
            self.observation = None
            if not step.get('acted') or step.get('before_contains') or not shows(page.get('text', ''), step['expected_text']):
                return ('Not verified: the planned success text has not newly appeared after an action. Inspect the page; '
                        'do not assume success. If the action worked but the planned text was a poor guess, '
                        'use revise_browser_task with text the page actually shows.'), 'Browser step remains unverified'
            step['verified'] = True
            task['evidence'].append(dict(step=task['current_step']+1, origin=urlsplit(page['url']).netloc,
                                         observed_text=step['expected_text']))
            task['current_step'] += 1
            self._save()
            return json.dumps(task), f"Verified browser step {task['current_step']} of {len(task['steps'])}"

    def revise_browser_task(self, task_id, steps):
        plan = self._plan(steps)
        with self.lock:
            task = self._current(task_id)
            if task.get('pending_dispatch'):
                raise ToolError('An action result is still uncertain. Inspect the page before revising the plan.')
            task['steps'] = task['steps'][:task['current_step']] + plan
            self._save()
            return json.dumps(task), 'Revised the browser task plan'

    def finish_browser_task(self, task_id, status, reason):
        reason = bounded(reason, 'Outcome or blocker', 600)
        with self.lock:
            task = self._current(task_id)
            if status not in ('completed', 'partly_completed', 'blocked'):
                raise ToolError('Choose completed, partly_completed, or blocked.')
            count = task['current_step']
            if status == 'completed' and count != len(task['steps']):
                raise ToolError('Cannot mark completed: planned steps are still unverified.')
            if status == 'partly_completed' and not count:
                raise ToolError('No step is verified yet. Report blocked instead.')
            task.update(status=status, reason=reason)
            self.observation = None
            self._save()
            return json.dumps(task), f'Browser task: {status.replace("_", " ")}'

    def browser_task_status(self, task_id):
        with self._db() as db:
            row = db.execute('SELECT record FROM browser_tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ToolError('No saved task with this ID.')
        return row[0], 'Read saved browser task'

    def list_browser_tasks(self):
        with self._db() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT record FROM browser_tasks ORDER BY rowid DESC LIMIT 15')]
        return json.dumps([dict(id=t['id'], goal=t['goal'], status=t['status'],
                                verified_steps=t['current_step'], total_steps=len(t['steps']), reason=t.get('reason', ''))
                           for t in records]), 'Listed saved browser tasks'

    def resume_browser_task(self, task_id, tab_id=''):
        with self.lock:
            if not self.control.permitted():
                raise ToolError('Enable Computer control again after finishing the private login, then ask Ano to continue.')
            if self.task and self.task['status'] == 'running':
                raise ToolError('A task is already running. Inspect it instead of restarting it.')
            with self._db() as db:
                row = db.execute('SELECT record FROM browser_tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise ToolError('No saved task with this ID.')
            task = json.loads(row[0])
            if task['status'] == 'completed' or task.get('pending_dispatch') or task.get('resumable') is False:
                raise ToolError('This task cannot be automatically resumed. It is completed, denied, or has an uncertain action. Review it manually; do not replay it.')
            if tab_id:
                split(tab_id)
                task['tab_id'] = tab_id
            self.generation = self.control._generation
            self.task = task
            task.update(status='running', reason='', attempts=0)
            self.observation = None
            self._save()
            return json.dumps(task), 'Resumed saved progress; inspect the current page before acting'

    def navigate_browser_task(self, task_id, observation_id, url, purpose):
        purpose = bounded(purpose, 'Purpose', 400)
        clean_location(url)
        with self.lock:
            task = self._current(task_id)
            obs, self.observation = self.observation, None
            if not obs or obs['id'] != observation_id or self.clock() - obs['at'] > 60:
                raise ToolError('Inspect this task’s page again before navigation.')
            if task['current_step'] >= len(task['steps']):
                raise ToolError('All steps have already verified. Finish the task.')
            if task['attempts'] >= 24 and getattr(self.control, 'permissionMode', '') != 'roam':
                raise ToolError('Task reached its normal action limit. Report the progress before continuing.')
        try:
            self.control.request_review(title='Navigate for this task', target=url,
                                        detail=f"Goal: {task['goal']}\nPurpose: {purpose}")
        except ToolError:
            self.stop('Navigation was not approved.', resumable=False)
            raise
        with self.lock:
            task = self._current(task_id)
            if self.clock() - obs['at'] > 120:
                raise ToolError('Navigation review expired. Inspect the page again.')
            step = task['steps'][task['current_step']]
            step['before_contains'] = shows(obs['page'].get('text', ''), step['expected_text'])
            step['acted'] = True
            task['attempts'] += 1
            task['pending_dispatch'] = True
            self._save()
        browser, number = split(task['tab_id'])
        generation = self.generation
        try:
            self.bridge.call(browser, 'navigate', tabId=number, url=url,
                             _permitted=lambda: self.control.guard(generation))
        except Exception:
            self.stop('Navigation outcome is uncertain. Inspect manually before continuing.', resumable=False)
            raise ToolError('Navigation did not confirm dispatch; it was not retried.') from None
        with self.lock:
            task['pending_dispatch'] = False
            self._save()
        return 'Navigation dispatched. Inspect and verify the resulting page.', 'Navigated the task tab'

    def continuation(self):
        with self.lock:
            if not self.task or self.task['status'] != 'running' or not self.control.guard(self.generation):
                return ''
            return (f"Browser task {self.task['id']} remains running: {self.task['current_step']}/{len(self.task['steps'])} steps verified. "
                    'Continue with fresh inspection, action and verification. If a human must act or the task is blocked, '
                    'call finish_browser_task with the honest blocker. If every step verified, call finish_browser_task completed. '
                    'Do not claim success merely because an action was dispatched.')

    def stop(self, reason='Computer control stopped. Finish any private login, re-enable control, then ask to resume.', resumable=True):
        with self.lock:
            self.observation = None
            if self.task and self.task['status'] == 'running':
                self.task.update(status='blocked', reason=reason,
                                 resumable=resumable and not self.task.get('pending_dispatch', False))
                self._save()

    def turn_finished(self):
        """An API failure or exhausted reasoning budget must not leave a task falsely running."""
        with self.lock:
            if self.task and self.task['status'] == 'running':
                status = 'partly_completed' if self.task['current_step'] else 'blocked'
                self.task.update(status=status, reason='The reasoning turn ended before the task was finished. Review the saved evidence before starting a fresh task.')
                self.observation = None
                self._save()
