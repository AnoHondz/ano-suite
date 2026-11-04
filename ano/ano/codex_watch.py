"""Keep a long Codex goal going overnight: notice "Goal usage limited" and press Resume goal after the reset.

The decisions live in CodexWatch (pure, testable). Reading and pressing run in a separate process,
`python -m ano.codex_watch check|resume`, so UI Automation never blocks Ano's window. Both only use
Windows accessibility: reading the ChatGPT window's labels and invoking its own Resume goal button.
No model is called, nothing is typed, and the watch is turned on only by the user's own command.
"""
from datetime import datetime, timedelta
import json
import re
import sys

CHECK_EVERY = timedelta(minutes=10)
RETRY_EVERY = timedelta(hours=1)    # when no reset time is shown, or one is far away
AFTER_RESET = timedelta(minutes=2)
VERIFY_AFTER = timedelta(minutes=2)
WINDOW = timedelta(hours=5, minutes=15)  # a bare clock time further away than one usage window is an old message
LIMITED = 'Goal usage limited'
RESUME = 'Resume goal'
MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
RESET = re.compile(r'(?:reset|available again|try again)\w*(?: on| at| after)?\s+'
                   r'(?:(?P<month>[A-Z][a-z]{2,8})\.? (?P<day>\d{1,2}),?\s+(?:at\s+)?)?'
                   r'(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<meridiem>[AP]\.?M\.?)', re.I)
RELATIVE = re.compile(r'try again in (?:(?P<hours>\d+)\s*h(?:ours?|rs?)?)?\s*(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?', re.I)


def reset_time(texts, now):
    """The latest reset time the window mentions, or None when none is shown or it is clearly stale."""
    found = None
    for text in texts:
        for match in RESET.finditer(text):
            found = match
        relative = RELATIVE.search(text)
        if relative and (relative['hours'] or relative['minutes']):
            found = relative
    if found is None:
        return None
    if found.re is RELATIVE:
        return now + timedelta(hours=int(found['hours'] or 0), minutes=int(found['minutes'] or 0))
    hour = int(found['hour']) % 12 + (12 if found['meridiem'].lower().startswith('p') else 0)
    minute = int(found['minute'])
    if found['month']:
        month = found['month'][:3].lower()
        if month not in MONTHS:
            return None
        due = now.replace(month=MONTHS.index(month) + 1, day=int(found['day']), hour=hour, minute=minute, second=0, microsecond=0)
        return due if due > now else None
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return due if due - now <= WINDOW else None


class CodexWatch:
    """What to do next, from the latest observation. Times are timezone-aware local datetimes."""

    def __init__(self, now):
        self.next_check = now
        self.resume_at = None
        self.limited = False
        self.announced = None  # the reset time last reported, so a waiting state is reported once
        self.unknown_wait_announced = False

    def due(self, now):
        if self.resume_at is not None and now >= self.resume_at:
            return 'resume'
        return 'check' if now >= self.next_check else None

    def checked(self, result, now):
        """Record a check. Returns a message for the user, or None."""
        self.next_check = now + CHECK_EVERY
        if not result.get('found'):
            self.resume_at = None
            self.unknown_wait_announced = False
            return None
        if not result.get('limited'):
            was_limited, self.limited, self.resume_at, self.announced = self.limited, False, None, None
            self.unknown_wait_announced = False
            return 'Codex is running again.' if was_limited else None
        self.limited = True
        reset = datetime.fromisoformat(result['reset']) if result.get('reset') else None
        resume = min(reset + AFTER_RESET, now + RETRY_EVERY) if reset else now + RETRY_EVERY
        if self.resume_at is None or resume < self.resume_at or (reset and reset != self.announced):
            self.resume_at = resume
        if reset == self.announced and reset is not None:
            return None
        if reset is None and self.unknown_wait_announced:
            return None
        self.announced = reset
        self.unknown_wait_announced = reset is None
        when = f'{self.resume_at:%I:%M %p}'.lstrip('0')
        if reset:
            return f'Codex hit its usage limit; it resets at {reset:%I:%M %p}. Ano will press Resume goal at {when}.'.replace(' 0', ' ')
        return f'Codex hit its usage limit and shows no reset time. Ano will try Resume goal at {when}, then hourly.'

    def resumed(self, result, now):
        """Record a resume attempt; verify shortly after. Returns a message for the user."""
        self.resume_at = None
        self.next_check = now + VERIFY_AFTER
        if result.get('pressed'):
            self.unknown_wait_announced = False
            return 'Pressed Resume goal in Codex. Checking in 2 minutes that it is running.'
        self.resume_at = now + RETRY_EVERY
        return f'Could not press Resume goal: {result.get("error", "unknown reason")}. Ano will try again in an hour.'


def _window():
    import psutil
    import uiautomation as auto
    pids = {p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() == 'chatgpt.exe'}
    return next((w for w in auto.GetRootControl().GetChildren() if w.ProcessId in pids and w.Name), None)


def _controls(window):
    import uiautomation as auto
    for control, _ in auto.WalkControl(window, maxDepth=60):
        yield control


def check(now=None):
    now = now or datetime.now().astimezone()
    window = _window()
    if window is None:
        return {'found': False}
    texts = [c.Name.strip() for c in _controls(window) if c.Name and c.Name.strip()][:8000]
    limited = LIMITED in texts
    reset = reset_time([t for t in texts if re.search(r'limit|usage|reset|try again', t, re.I)], now) if limited else None
    return {'found': True, 'limited': limited, 'resume_button': RESUME in texts,
            'reset': reset.isoformat(timespec='minutes') if reset else None}


def resume():
    window = _window()
    if window is None:
        return {'pressed': False, 'error': 'the ChatGPT app is not open'}
    button = next((c for c in _controls(window) if c.Name == RESUME and c.ControlTypeName == 'ButtonControl'), None)
    if button is None:
        return {'pressed': False, 'error': 'no Resume goal button is showing'}
    try:
        button.GetInvokePattern().Invoke()
    except Exception:
        return {'pressed': False, 'error': 'Windows did not let Ano press the button'}
    return {'pressed': True}


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if action not in ('check', 'resume'):
        sys.exit('Use: python -m ano.codex_watch check|resume')
    print(json.dumps(check() if action == 'check' else resume()))


if __name__ == '__main__':
    main()
