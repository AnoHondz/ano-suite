"""Per-reply execution policy. Free roam is a user-selected session, never a model tool."""
import hashlib
import json
import time


def workflow_continuation(toolbox):
    plugins = getattr(toolbox, 'plugins', [])
    if not isinstance(plugins, (list, tuple)):
        return ''
    for plugin in plugins:
        callback = getattr(plugin, 'continuation', None)
        if callable(callback):
            hint = callback()
            if isinstance(hint, str) and hint:
                return hint
    return ''


class RunBudget:
    def __init__(self, toolbox, normal_steps=24, max_seconds=None, clock=time.monotonic):
        self.toolbox, self.normal_steps = toolbox, normal_steps
        self.roaming = self._roam()
        self.steps = self.failures = self.same_failure = 0
        self.previous_failure = None
        self.reason = ''
        self.recovery = {}
        self.errors = {}  # failed tool -> Ano's own error message, shown to the user, never sent back as guidance
        self.tools_started = False
        self.deadline = clock() + max_seconds if max_seconds is not None else None
        self.clock = clock

    def failure_message(self, message):
        """An interrupted model request must not encourage replaying earlier actions."""
        if self.tools_started:
            return (message + ' Tools ran before this interruption; some actions may already '
                    'have taken effect. Inspect the current state before retrying. Do not '
                    'blindly repeat typing, sends, submissions or other changes.')
        return message

    def continuation(self):
        """Require resolution of failed calls and uncertain desktop input before finishing.

        Only tool names and fixed guidance are retained: no field values or credentials.
        A fresh desktop read clears the inspection requirement, not proof of task success.
        """
        pending = workflow_continuation(self.toolbox)
        if self.recovery:
            pending += ('\nUnresolved execution results: ' + ', '.join(sorted(self.recovery))
                        + '. Inspect current state before continuing. For failed calls, correct the '
                        'cause and retry only when safe. For uncertain input, read or observe the '
                        'target first; never blindly replay clicks, typing, sends or submissions. '
                        'Verify every requested item against the resulting state. Continue within '
                        'the existing permissions and budget, or report partial progress and the '
                        'specific blocker if permission, information or capability is missing.')
        return pending.strip()

    def unfinished(self):
        """The user-facing reply for an unverified task: the specific blockers when known."""
        blockers = [f'· {name}: {self.errors[name]}' for name in sorted(self.recovery) if name in self.errors]
        if not blockers:
            return 'The task is not verified as complete.\n\n' + self.continuation()
        workflow = workflow_continuation(self.toolbox)
        return ('The task is not verified as complete. What blocked it:\n' + '\n'.join(blockers)
                + ('\n\n' + workflow if workflow else ''))

    def _roam(self):
        callback = getattr(self.toolbox, 'free_roam', None)
        return callable(callback) and callback() is True

    def can_act(self):
        if self.reason:
            return False
        if self.deadline is not None and self.clock() >= self.deadline:
            self.reason = 'Stopped at the local task time limit. Some steps may be incomplete; inspect the current state before retrying.'
            return False
        allowed = getattr(self.toolbox, 'allowed', None)
        if (callable(allowed) and allowed() is False) or (self.roaming and not self._roam()):
            self.reason = 'Stopped because control was stopped, paused, locked, or the permission mode changed.'
            return False
        return True

    def next(self):
        if not self.can_act():
            return False
        if not self.roaming and self.steps >= self.normal_steps:
            self.reason = 'Stopped at the normal step limit. Free roam permits longer tasks.'
            return False
        self.steps += 1
        return True

    def record(self, name, arguments, failed, output=None):
        if failed:
            self.recovery[name] = True
            if isinstance(output, str) and output.strip():
                self.errors[name] = ' '.join(output.split())[:300]
        else:
            if name != 'desktop_input':
                self.recovery.pop(name, None)
                self.errors.pop(name, None)
            if name in ('desktop_observe', 'desktop_read'):
                self.recovery.pop('desktop_input', None)
            if name == 'desktop_input':
                # Both text receipts and screenshot receipts may report uncertain input.
                text = getattr(output, 'text', output)
                receipt = None
                if isinstance(text, str):
                    try:
                        receipt, _ = json.JSONDecoder().raw_decode(text.lstrip())
                    except ValueError:
                        pass
                if (isinstance(receipt, dict) and
                        (receipt.get('text_verified') is False or
                         receipt.get('action_verified') is False or
                         receipt.get('action_dispatch_uncertain') is True)):
                    self.recovery[name] = True
                elif isinstance(text, str) and 'follow-up screenshot was unavailable' in text:
                    self.recovery[name] = True
        if not failed:
            self.failures = self.same_failure = 0
            self.previous_failure = None
            return
        # Hashes live only in memory; never persist tool arguments or field values.
        key = hashlib.sha256(json.dumps([name, arguments], sort_keys=True, default=str).encode()).digest()
        self.failures += 1
        self.same_failure = self.same_failure + 1 if key == self.previous_failure else 1
        self.previous_failure = key
        if self.same_failure >= 3 or self.failures >= 8:
            self.reason = 'Blocked after repeated tool failures. Inspect the cause before retrying; completed actions were not replayed.'
