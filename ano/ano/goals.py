"""Long goals: Ano keeps working, turn after turn, until the goal is done or it needs the user.

"keep working on GOAL" starts one. After every reply Ano reads the STATUS line it was asked to end with:
DONE finishes, NEED YOU stops and says what is needed, anything else sends the next "continue" turn.
A goal is capped at MAX_ROUNDS turns (each turn may cost OpenAI credits) and stops on Stop, Pause, or two
failed turns in a row. It is saved after every turn, so after a restart Ano offers to pick it up; it never
restarts paid work on its own.
"""
import json
import re
import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot

MAX_ROUNDS = 12
STATUS = re.compile(r'STATUS:\s*(DONE|CONTINUE|NEED YOU)\b:?\s*([^\n]*)', re.I)
# Worded with care: the local brain picks its tools by keywords in this text, so it avoids words such as
# "reply", "email" or "sign in" that would offer it unrelated tools.
INSTRUCTIONS = ('Finish with exactly one last line: "STATUS: DONE" when everything is finished and verified, '
                '"STATUS: CONTINUE" when more work remains, or "STATUS: NEED YOU: <what>" when only I can continue '
                '(for example a payment or a decision).')


def first_prompt(goal):
    return (f'Goal: {goal}\n\nWork on this goal step by step until it is completely finished. Use only the tools this '
            f'goal needs, verify what you produced, and keep going in later turns. {INSTRUCTIONS}')


def next_prompt(goal, round_number):
    return (f'Continue the goal: {goal}\nThis is turn {round_number} of at most {MAX_ROUNDS}. See what is already done '
            f'above, do the next steps for this goal only, and verify them. {INSTRUCTIONS}')


def read_status(reply):
    """('done' | 'continue' | 'need' | 'missing', detail) from the reply's last STATUS line."""
    # A quoted status in the middle of a response is not the runner protocol.
    lines = (reply or '').strip().splitlines()
    found = STATUS.fullmatch(lines[-1].strip()) if lines else None
    if not found:
        return 'missing', ''
    word = found[1].upper()
    return ('done' if word == 'DONE' else 'need' if word == 'NEED YOU' else 'continue'), found[2].strip()


class GoalRunner(QObject):
    _forget = Signal()  # from a command on the worker thread; the saved goal is cleared on the main thread

    def __init__(self, controller, memory, verifier=None):
        super().__init__()
        self.controller, self.memory = controller, memory
        self.verifier = verifier
        self.lock = threading.Lock()
        self.requested = None   # set by command() on the reply worker thread; started on the main thread
        self.stop_requested = False
        self.goal = None        # {'goal', 'round', 'failures'} while running
        self.waiting = False
        controller.replyFinished.connect(self._reply_finished)
        self._forget.connect(lambda: self._save(None))
        # Memory's database belongs to the main thread, so commands read this copy instead.
        self.saved = saved = memory.setting('active_goal', None)
        if saved:  # interrupted by a restart: offer it, never restart paid work silently
            QTimer.singleShot(4000, lambda: controller.add_message(
                'assistant', f'A goal was in progress when Ano closed: “{saved["goal"]}” (turn {saved["round"]} of '
                             f'{MAX_ROUNDS}). Say “continue the goal” to pick it up, or “stop the goal”.'))

    # -------------------------------------------------- commands (called from LocalRoutines on a worker thread)
    def command(self, text, local=False):
        """local: the user typed "local:" on the OpenAI brain, so every turn of this goal uses the free brain."""
        clean = ' '.join(text.split()).rstrip('.!')
        key = clean.lower()
        # "keep working on X", or "work on X until it's done"; a plain "work on my essay" stays a normal request.
        until = r' until (?:it(?:\'s| is) )?(?:done|finished|complete)'
        if match := (re.fullmatch(rf'(?:keep working on|keep going on) (?P<goal>.{{3,600}}?)(?:{until})?', clean, re.I | re.S)
                     or re.fullmatch(rf'work on (?P<goal>.{{3,600}}?){until}', clean, re.I | re.S)):
            goal = match['goal'].strip(' :')
            with self.lock:
                if self.goal or self.requested:
                    return 'A goal is already running. Say “stop the goal” first, or “goal status”.'
                self.requested = dict(goal=goal, round=0, failures=0, local=local)
            cost = 'every turn uses the free local AI' if local else 'each turn may use OpenAI credits'
            return (f'Goal set: “{goal}”. I will keep working on it, turn after turn, until it is done or I need you '
                    f'(at most {MAX_ROUNDS} turns; {cost}). Say “stop the goal” any time.')
        if key in ('continue the goal', 'resume the goal', 'continue my goal', 'pick up the goal'):
            saved = self.saved
            with self.lock:
                if self.goal or self.requested:
                    return 'The goal is already running.'
                if not saved:
                    return 'There is no unfinished goal. Start one with: keep working on …'
                self.requested = dict(saved, failures=0)
            return f'Picking up the goal “{saved["goal"]}” at turn {saved["round"] + 1}.'
        if key in ('stop the goal', 'stop goal', 'cancel the goal', 'stop working on it', 'stop working on the goal'):
            with self.lock:
                was = bool(self.goal or self.requested or self.saved)
                self.stop_requested, self.requested, self.saved = True, None, None
            self._forget.emit()
            return 'Goal stopped after the current turn.' if was else 'No goal is running.'
        if key in ('goal status', 'what is the goal', "what's the goal"):
            current = self.goal or self.saved
            if not current:
                return 'No goal is running. Start one with: keep working on …'
            return f'Goal: “{current["goal"]}” · turn {current["round"]} of {MAX_ROUNDS}' + ('' if self.goal else ' · paused')
        return None

    # -------------------------------------------------- the loop (main thread)
    @Slot(str, bool)
    def _reply_finished(self, reply, failed):
        if self.requested and not self.goal:
            QTimer.singleShot(600, self._start)
            return
        if not self.goal or not self.waiting:
            return
        pending = self.goal.get('pending_job')
        if pending and getattr(self.controller, 'last_completed_job', None) != pending:
            return  # A reply from another queued command cannot complete this goal turn.
        self.waiting = False
        completed = getattr(self.controller, 'last_completed_job', None)
        if completed and completed not in self.goal.setdefault('jobs', []):
            self.goal['jobs'].append(completed)
        if self.stop_requested or getattr(self.controller, 'paused', False):
            self._end('Goal stopped. Say “continue the goal” to pick it up again.', keep=not self.stop_requested)
            return
        if failed:
            self.goal['failures'] += 1
            if self.goal['failures'] >= 2:
                self._end('The goal stopped: two turns in a row failed or were stopped. Say “continue the goal” to try again.', keep=True)
                return
        else:
            self.goal['failures'] = 0
            state, detail = read_status(reply)
            # A reply without a STATUS line is taken as "keep going" once; twice in a row, Ano stops guessing.
            self.goal['missing'] = self.goal.get('missing', 0) + 1 if state == 'missing' else 0
            if self.goal['missing'] >= 2:
                self._end('The goal paused: the last two replies did not say whether it is finished. Check the result above; '
                          'say “continue the goal” to keep going.', keep=True)
                return
            if state == 'done':
                try:
                    check = self.verifier(self.goal['goal'], self.goal.get('jobs', [])) if self.verifier else {
                        'passed': False, 'reason': 'No independent completion checker is connected.'}
                except Exception:
                    check = {'passed': False, 'reason': 'Independent verification failed; completed actions must not be replayed.'}
                self.memory.set_setting('last_goal_verification', check)
                if check.get('passed') is True and check.get('references'):
                    self._end(f'Goal output verified after {self.goal["round"]} turn(s): “{self.goal["goal"]}”. '
                              + check.get('scope', ''), keep=False)
                else:
                    if (check.get('repairable') is True and self.goal.get('verification_retries', 0) < 2
                            and self.goal['round'] < MAX_ROUNDS):
                        self.goal['verification_retries'] = self.goal.get('verification_retries', 0)+1
                        self.goal['verification_issue'] = check.get('reason', 'Output requirements are unmet.')
                        QTimer.singleShot(800, self._send_next)
                        return
                    self._end('Completion unverified: '+check.get('reason', 'Independent evidence is missing.')+
                              ' Progress is saved. Inspect existing results before continuing; do not repeat uncertain actions.', keep=True)
                return
            if state == 'need':
                self._end(f'The goal is waiting for you: {detail or "see the last reply"}. '
                          'When that is sorted, say “continue the goal”.', keep=True)
                return
        if self.goal['round'] >= MAX_ROUNDS:
            self._end(f'The goal reached its {MAX_ROUNDS}-turn limit without saying it was done. '
                      'Check the replies above; say “continue the goal” for more turns.', keep=True, reset_rounds=True)
            return
        QTimer.singleShot(800, self._send_next)

    def _start(self):
        with self.lock:
            goal, self.requested = self.requested, None
            if goal is None:
                return
            self.goal, self.stop_requested = goal, False
        self._send_next()

    def _send_next(self):
        if not self.goal:
            return
        if self.stop_requested:  # "stop the goal" can arrive between a reply and the next turn
            self._end('Goal stopped. Say “continue the goal” to pick it up again.', keep=False)
            return
        self.goal['round'] += 1
        self._save(dict(goal=self.goal['goal'], round=self.goal['round'], local=self.goal.get('local', False), jobs=self.goal.get('jobs', [])))
        self.waiting = True
        prompt = first_prompt(self.goal['goal']) if self.goal['round'] == 1 else next_prompt(self.goal['goal'], self.goal['round'])
        if self.goal.get('verification_issue'):
            prompt += ('\nIndependent output check failed: '+self.goal.pop('verification_issue')+
                       '\nInspect existing results and correct only the missing requirement. '
                       'Do not duplicate completed work or repeat uncertain actions.')
        self.controller.send(('local: ' if self.goal.get('local') else '') + prompt)
        pending = getattr(self.controller, 'last_enqueued_job', None)
        if pending:
            self.goal['pending_job'] = pending
            if pending not in self.goal.setdefault('jobs', []):
                self.goal['jobs'].append(pending)
            self._save(dict(goal=self.goal['goal'],round=self.goal['round'],local=self.goal.get('local',False),jobs=self.goal['jobs']))

    def _end(self, message, keep, reset_rounds=False):
        goal, self.goal, self.waiting = self.goal, None, False
        if keep and goal:
            self._save(dict(goal=goal['goal'], round=0 if reset_rounds else goal['round'], local=goal.get('local', False), jobs=goal.get('jobs', [])))
        else:
            self._save(None)
        self.controller.add_message('assistant', message)

    def _save(self, value):  # main thread only
        self.saved = value
        self.memory.set_setting('active_goal', value)
