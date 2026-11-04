"""Ano Monitor: notice what happens on this PC and act on the user's own "when …, …" rules.

Rules come only from the user's typed words (LocalRoutines.reply hands each message to
Monitors.command before any model sees it); there is no model tool that creates or changes one.

Sensing is local and read-only: process names, the foreground app (window titles that look private
are dropped), the Downloads folder, battery, network adapters, CPU load, and two narrow UI
Automation checks: the Claude Code send button in VS Code ("Stop" while Claude works) and Codex's
goal state (codex_watch), and new Windows notifications, read from a temporary copy of the
notification database (wpndatabase.db; the original is never opened). Notifications from banks and
password managers, and any that look like codes or money, are dropped before anything sees them.
Notification text is only shown or read aloud, never followed as instructions. Nothing is logged or
stored except the rules and when they fired. Sensing
runs only while a rule is active, Ano is not paused, the screen is unlocked and no private login is
in progress; it pauses instantly with "pause monitoring".
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time

from .routines import APPS, SITES
from .scheduled import TARGETS
from .tools import ToolError

PRIVATE_TITLE = re.compile(r'password|passwort|banking|\bbank\b|inprivate|incognito|private brows|1password|bitwarden|keepass|credential', re.I)
PARTIAL_DOWNLOADS = ('.crdownload', '.part', '.partial', '.download')
# Programs and scripts are never opened automatically, even when the rule says "open it".
NEVER_AUTO_OPEN = {'.exe', '.msi', '.msix', '.appx', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.jse', '.wsf', '.scr',
                   '.com', '.pif', '.lnk', '.reg', '.hta', '.cpl', '.jar', '.py', '.pyw'}
# Spoken app names -> process names (without .exe), compared after removing spaces and punctuation.
PROCESS_ALIASES = {'vscode': 'code', 'visualstudiocode': 'code', 'word': 'winword', 'microsoftword': 'winword',
                   'excel': 'excel', 'powerpoint': 'powerpnt', 'outlook': 'outlook', 'teams': 'msteams',
                   'edge': 'msedge', 'microsoftedge': 'msedge', 'chrome': 'chrome', 'googlechrome': 'chrome',
                   'fileexplorer': 'explorer', 'calculator': 'calculatorapp', 'chatgpt': 'chatgpt', 'codex': 'chatgpt'}
LOW_BATTERY = 15
CPU_BUSY, CPU_IDLE, CPU_BUSY_SAMPLES = 50.0, 12.0, 3
NOTIFICATION_GRACE = 20  # seconds: how recent a notification may be when sensing first starts
NOTIFICATION_DB = Path.home() / 'AppData' / 'Local' / 'Microsoft' / 'Windows' / 'Notifications' / 'wpndatabase.db'
# Notifications that are dropped whole: banks, wallets, password managers and authenticators...
SENSITIVE_APP = re.compile(r'bank|wallet|paypal|1password|bitwarden|lastpass|keeper|dashlane|keepass|authenticator|authy|'
                           r'okta|duomobile|python', re.I)  # python: Ano's own tray messages
# ...and anything that looks like a sign-in code, a password or money moving, whichever app sent it.
SENSITIVE_TEXT = re.compile(r'verification|security code|one[- ]time|\botp\b|passcode|pass ?word|\bpin\b|2fa|two[- ]factor|'
                            r'(?:sign[- ]?in|login|log-in|access|confirmation|auth\w*) code|\bcode\b\W{0,3}\d{4,8}|\d{4,8}\W{0,3}is your|'
                            r'card ending|debited|credited|balance|transaction|payment', re.I)
# Spoken app names -> words looked for in the notification's sender id (e.g. Microsoft.Office.OUTLOOK.EXE.15).
NOTIFICATION_ALIASES = {'chatgpt': ('chatgpt', 'openai'), 'phone': ('yourphone',), 'myphone': ('yourphone',),
                        'phonelink': ('yourphone',), 'email': ('mail', 'outlook'), 'mail': ('mail', 'outlook'),
                        'calendar': ('calendar',), 'snippingtool': ('screensketch',), 'store': ('windowsstore',)}
FRIENDLY_SENDERS = {'yourphone': 'Phone Link', 'windowslivemail': 'Mail', 'windowslivecalendar': 'Calendar',
                    'screensketch': 'Snipping Tool', 'windowsstore': 'Microsoft Store', 'webexperience': 'Widgets'}

CONDITIONS = [  # (regex for the condition phrase, kind); the first match wins
    # "a notification mentions "interview"", "i get a notification about rent"
    (r'(?:(?:i get|i receive|i have|there is|there\'s) )?(?:a |an |any |my )?(?:new )?notifications? '
     r'(?:that )?(?:mentions?|mentioning|says?|saying|contains?|containing|includes?|is about|about|with) '
     r'["“\']?(?P<arg>[^"“”\',]{1,60}?)["”\']?', 'notification_text'),
    # "i get a notification (from outlook)", "codex notifies me", "outlook sends me a notification"
    # The app name ends at the comma or "then" ("from microsoft teams, tell me").
    (r'(?:i get|i receive|there is|there\'s|i have) (?:a |an |any )?(?:new )?notifications?'
     r'(?: from (?:my )?(?P<arg>[\w .+-]{1,40}?)(?=,| then ))?', 'notification'),
    (r'(?P<arg>[\w .+-]{1,40}?) (?:notifies me|sends (?:me )?a notification|pops up a notification)', 'notification'),
    (r'claude (?:is |has )?(?:done|finished|finishes|stops working|stopped)(?: in vs ?code)?', 'claude_idle'),
    (r'(?:codex|chatgpt) (?:is |runs |has )?(?:out of usage|run out of usage|hits? (?:its |the )?(?:usage )?limit|is limited)', 'codex_limited'),
    # "codex is back", "chatgpt is ready", "the goal resets", optionally "… when you press resume".
    (r'(?:codex|chatgpt|the goal|my goal)(?: usage)? (?:is |gets |comes |has )?(?:back|ready|available|running again|resumes|resumed|resets?|starts again)'
     r'(?:,? (?:when|after|once|and) (?:you|ano) (?:press(?:es)?|click(?:s)?|hits?|taps?) (?:the )?resume(?: goal)?(?: button)?)?', 'codex_running'),
    (r'(?:my |a |the )?downloads? (?:is |are )?(?:done|finish(?:es|ed)?|completes?|completed)', 'download_done'),
    (r'(?:my )?battery (?:is |gets |goes )?low', 'battery_low'),
    (r"(?:my )?(?:laptop|pc|computer) (?:is |starts )?(?:charging|plugged in)|i plug (?:it |my laptop )?in", 'charging'),
    (r"(?:the )?(?:internet|network|wi-?fi) (?:is back|comes back|reconnects)|i'?m back online", 'online'),
    (r'(?:the )?(?:internet|network|wi-?fi) (?:goes down|drops|disconnects|is down)|i go offline', 'offline'),
    (r'(?:my )?(?:pc|computer|laptop|cpu) (?:is |goes |becomes |gets )?(?:idle|quiet)', 'cpu_idle'),
    (r'(?P<arg>[\w .+-]{1,40}?) (?:opens|starts|launches)', 'app_open'),
    (r'(?P<arg>[\w .+-]{1,40}?) (?:closes|quits|stops running)', 'app_close'),
]
# "i open APP ACTION…" has no word marking where the app name ends, so every split is tried, shortest first.
TRAILING = [(r'i switch to', 'focus'), (r'i (?:open|start|launch)', 'app_open'), (r'i (?:close|quit)', 'app_close')]
DESCRIBE = {'claude_idle': 'Claude finishes in VS Code', 'codex_limited': 'Codex runs out of usage',
            'codex_running': 'Codex is running again', 'download_done': 'a download finishes',
            'battery_low': f'the battery drops to {LOW_BATTERY}%', 'charging': 'the laptop is plugged in',
            'online': 'the network comes back', 'offline': 'the network goes down', 'cpu_idle': 'the PC goes quiet after a busy spell',
            'focus': 'you switch to {arg}', 'app_open': '{arg} opens', 'app_close': '{arg} closes',
            'notification': 'a notification arrives{from_arg}', 'notification_text': 'a notification mentions “{arg}”'}
SOURCES = {'claude_idle': 'claude', 'codex_limited': 'codex', 'codex_running': 'codex', 'download_done': 'downloads',
           'battery_low': 'power', 'charging': 'power', 'online': 'network', 'offline': 'network', 'cpu_idle': 'cpu',
           'focus': 'focus', 'app_open': 'apps', 'app_close': 'apps',
           'notification': 'notifications', 'notification_text': 'notifications'}
ACTIONS_HELP = ('tell me, fly around, say TEXT, type "TEXT" in chatgpt|notepad|vs code, open APP or SITE, '
                'open it (downloads), resume it after the reset (Codex), read it to me or handle it (notifications)')


def squash(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower().removesuffix('.exe'))


def process_key(spoken):
    key = squash(spoken)
    return PROCESS_ALIASES.get(key, key)


# "say/send/type/tell it [this] "MESSAGE"": quoted text is kept whole, even with "and" or a missing closing quote.
MESSAGE = re.compile(r'(?P<verb>say|speak|send|type|write|tell (?:it|chatgpt|codex)|message(?: it)?)'
                     r'(?: (?:this|it|that|the message|this message|a message|the text))?[:,]?\s*'
                     r'(?:["“\'](?P<quoted>.+?)["”\']?|(?P<plain>[^"“\'].*?))\s*$', re.I | re.S)


def message_action(text, condition):
    """A spoken or typed message. With a ChatGPT/Codex rule, say/send/type means: type it into ChatGPT, press Enter."""
    match = MESSAGE.fullmatch(text.strip())
    if not match:
        return None
    body = (match['quoted'] if match['quoted'] is not None else match['plain'] or '').strip().strip('"“”\'').strip()
    aloud = bool(re.search(r'\s+(?:out loud|aloud)$', body, re.I))
    body = re.sub(r'\s+(?:out loud|aloud)$', '', body, flags=re.I).strip().strip('"“”\'').strip()
    if not body:
        return None
    if condition.startswith('codex') and not aloud:
        if len(body) > 500:
            raise ToolError('Keep the message for ChatGPT under 500 characters. Nothing was saved.')
        return {'kind': 'type', 'text': body, 'target': 'chatgpt'}
    if match['verb'].lower() in ('say', 'speak'):
        return {'kind': 'speak', 'text': body[:200]}
    raise ToolError(f'Say where to type it, e.g. type "{body[:40]}" in notepad. Nothing was saved.')


def parse_actions(text, condition):
    text = text.strip()
    # A message to type or say takes everything after it, so its own "and"s and commas stay inside it.
    typed_into = re.fullmatch(r'type ["“\'](.{1,500}?)["”\'] (?:in|into) (' + '|'.join(TARGETS) + ')', text, re.I)
    if not typed_into and re.match(r'(?:say|speak|send|type|write|tell (?:it|chatgpt|codex)|message)\b', text, re.I):
        whole = message_action(text, condition)
        if whole:
            return [whole]
    actions = []
    for part in re.split(r',? and (?:then )?|, then |, ', text, flags=re.I):
        part = part.strip().rstrip('.!')
        if not part:
            continue
        if re.fullmatch(r'tell me|let me know|notify me|alert me|ping me', part, re.I):
            actions.append({'kind': 'notify'})
        elif re.fullmatch(r'fly(?: around)?(?: the screen)?|come (?:and )?find me|fly to me', part, re.I):
            actions.append({'kind': 'fly'})
        elif match := re.fullmatch(r'type ["“\'](.{1,500}?)["”\'] (?:in|into) (' + '|'.join(TARGETS) + ')', part, re.I):
            actions.append({'kind': 'type', 'text': match[1], 'target': match[2].lower()})
        elif message := message_action(part, condition):
            actions.append(message)
        elif re.fullmatch(r'open it|open the (?:file|download)', part, re.I):
            if condition != 'download_done':
                raise ToolError('“open it” only works with “when my download finishes”. Nothing was saved.')
            actions.append({'kind': 'open_download'})
        elif re.fullmatch(r'read it(?: out)?(?: to me| out loud| aloud)?|read (?:it|the notification|them) (?:to me|out loud|aloud)', part, re.I):
            if not condition.startswith('notification'):
                raise ToolError('“read it to me” works with notification rules, e.g. when I get a notification from outlook, '
                                'read it to me. Nothing was saved.')
            actions.append({'kind': 'read'})
        elif re.fullmatch(r'handle it|deal with it|take care of it|sort it out', part, re.I):
            if not condition.startswith('notification'):
                raise ToolError('“handle it” works with notification rules, e.g. when I get a notification from outlook, '
                                'handle it. Nothing was saved.')
            actions.append({'kind': 'handle'})
        elif re.fullmatch(r'resume it(?: after the reset)?|keep it going|resume codex(?: after the reset)?', part, re.I):
            if condition != 'codex_limited':
                raise ToolError('“resume it” only works with “when codex is out of usage”. Nothing was saved.')
            actions.append({'kind': 'codex_watch'})
        elif match := re.fullmatch(r'open (.{1,80})', part, re.I):
            target = match[1].strip().lower().removeprefix('my ')
            if target in SITES:
                actions.append({'kind': 'open', 'site': SITES[target], 'label': target})
            elif target in APPS:
                actions.append({'kind': 'open', 'app': APPS[target], 'label': target})
            elif re.fullmatch(r'https://[^\s]+', target):
                actions.append({'kind': 'open', 'site': target, 'label': target})
            else:
                raise ToolError(f'I can open the built-in apps and sites (say “show commands”), not “{target}”. Nothing was saved.')
        else:
            raise ToolError(f'I can’t do “{part}” from a monitor yet. Actions: {ACTIONS_HELP}. Nothing was saved.')
    if not actions:
        raise ToolError(f'Say what to do, e.g. “when claude is done, tell me”. Actions: {ACTIONS_HELP}.')
    return actions


def parse_rule(text):
    """'when claude is done, tell me and fly around every time' -> (condition, arg, actions, repeat) or None."""
    # Matching ignores case, but the user's own wording is kept: it may be a message to type for them.
    key = ' '.join(text.split()).rstrip('.!?')
    key = re.sub(r'^(?:(?:hey )?ano[,! ]+|please |(?:can|could|would) you(?: please)? )', '', key, flags=re.I)
    repeat = bool(re.search(r'\b(?:every time|each time)\b|^whenever\b', key, re.I))
    key = re.sub(r',? ?\b(?:every time|each time)\b,?', '', key, flags=re.I).strip(' ,')
    match = re.fullmatch(r'(?:when(?:ever)?|if|once) (.+)', key, re.I | re.S)
    if not match:
        return None
    body = match[1]
    for prefix, condition in TRAILING:
        found = re.fullmatch(f'{prefix} (?P<rest>.+)', body, re.I | re.S)
        if not found:
            continue
        words, problem = found['rest'].split(' '), None
        for count in range(1, min(5, len(words))):
            name, rest = ' '.join(words[:count]), ' '.join(words[count:])
            if ',' in name.rstrip(','):
                break
            rest = re.sub(r'^(?:then )', '', rest, flags=re.I)
            try:
                return condition, name.rstrip(',').lower(), parse_actions(rest, condition), repeat
            except ToolError as error:
                problem = problem or error
                if name.endswith(','):
                    break  # the comma marked the end of the app name, so this was the only reading
        raise problem or ToolError('Name the app, e.g. when I open spotify, tell me.')
    for pattern, condition in CONDITIONS:
        found = re.fullmatch(f'(?:{pattern})(?:,| then|,? then)? (?P<rest>.+)', body, re.I | re.S)
        if found:
            arg = (found.groupdict().get('arg') or '').strip().lower()
            return condition, arg, parse_actions(found['rest'], condition), repeat
    raise ToolError('I can watch for: Claude finishing in VS Code, Codex running out of usage or coming back, a download '
                    'finishing, an app opening or closing, switching to an app, low battery, charging, the network going '
                    'down or coming back, the PC going quiet, or a notification (from an app, or mentioning a word). '
                    'Example: when claude is done, tell me and fly around.')


@dataclass
class Event:
    kind: str
    message: str  # 40 characters or fewer: shown in the flight bubble
    arg: str = ''
    payload: dict = field(default_factory=dict)


class MonitorEngine:
    """Turns successive snapshots into events. The first snapshot (and the first after a reset) is a baseline."""

    def __init__(self):
        self.previous = None
        self.busy_samples = 0

    def reset(self):
        self.previous, self.busy_samples = None, 0

    def events(self, now, clock=time.time):
        before, self.previous = self.previous, now
        # Notifications have their own arrival times, so the first read after a rule starts still catches ones
        # that came in during the last few seconds (the rule was typed, then the notification arrived).
        if 'notification_max' in now and (before is None or before.get('notification_max') is None):
            fresh = [note for note in now.get('notifications', ()) if note.get('arrived', 0) >= clock() - NOTIFICATION_GRACE]
            early = [Event('notification', f'{note["app"]}: {note["title"]}'[:40], arg=note['sender'], payload=dict(note))
                     for note in fresh]
        else:
            early = []
        cpu = now.get('cpu')
        quiet = False
        if cpu is not None:  # counted from the first sample, so a busy spell before the baseline still counts
            if cpu >= CPU_BUSY:
                self.busy_samples += 1
            elif cpu <= CPU_IDLE:
                quiet, self.busy_samples = self.busy_samples >= CPU_BUSY_SAMPLES, 0
        if before is None:
            return early
        found = []
        if before.get('claude') == 'working' and now.get('claude') == 'idle':
            found.append(Event('claude_idle', 'Claude finished in VS Code'))
        if before.get('codex') != 'limited' and now.get('codex') == 'limited':
            found.append(Event('codex_limited', 'Codex ran out of usage'))
        if before.get('codex') == 'limited' and now.get('codex') == 'running':
            found.append(Event('codex_running', 'Codex is running again'))
        finished = set(before.get('partial', ())) - set(now.get('partial', ()))
        if finished:
            newest = now.get('newest') or {}
            name = newest.get('name') or Path(next(iter(finished))).stem
            found.append(Event('download_done', ('Downloaded ' + name)[:40], payload=dict(newest)))
        for name in sorted(set(now.get('apps', ())) - set(before.get('apps', ()))):
            found.append(Event('app_open', f'{name} opened'[:40], arg=name))
        for name in sorted(set(before.get('apps', ())) - set(now.get('apps', ()))):
            found.append(Event('app_close', f'{name} closed'[:40], arg=name))
        if now.get('focus') and now.get('focus') != before.get('focus'):
            found.append(Event('focus', f'Switched to {now["focus"]}'[:40], arg=now['focus']))
        battery, was = now.get('battery'), before.get('battery')
        if battery and was:
            if was[0] > LOW_BATTERY >= battery[0] and not battery[1]:
                found.append(Event('battery_low', f'Battery low: {battery[0]:.0f}%'))
            if not was[1] and battery[1]:
                found.append(Event('charging', 'Laptop is charging'))
        if before.get('online') is False and now.get('online') is True:
            found.append(Event('online', 'Back online'))
        if before.get('online') is True and now.get('online') is False:
            found.append(Event('offline', 'Network went down'))
        if quiet:
            found.append(Event('cpu_idle', 'Your PC has gone quiet'))
        if before.get('notification_max') is not None:
            for note in now.get('notifications', ()):
                if note['id'] > before['notification_max']:
                    found.append(Event('notification', f'{note["app"]}: {note["title"]}'[:40], arg=note['sender'],
                                       payload=dict(note)))
        return early + found


def sender_matches(spoken, sender):
    """Does the app the user named ("outlook", "my phone") match a notification's sender id?"""
    key, sender = squash(spoken), squash(sender)
    if not key:
        return False
    if any(word in sender for word in NOTIFICATION_ALIASES.get(key, (key,))):
        return True
    # A full app name ("Google Chrome", "Microsoft Teams") also matches on its distinctive word.
    words = [squash(word) for word in spoken.split() if squash(word) not in ('microsoft', 'google', 'windows', 'app', 'the', 'my')]
    return len(spoken.split()) > 1 and any(len(word) > 3 and word in sender for word in words)


def matches(rule, event):
    if rule['condition'] == 'notification_text' and event.kind == 'notification':
        text = f'{event.payload.get("title", "")} {event.payload.get("body", "")}'.lower()
        return bool(rule['arg']) and rule['arg'].lower() in text
    if rule['condition'] != event.kind:
        return False
    if not rule['arg']:
        return True
    if event.kind == 'notification':
        return sender_matches(rule['arg'], event.arg) or sender_matches(rule['arg'], event.payload.get('app', ''))
    wanted, seen = process_key(rule['arg']), squash(event.arg)
    return bool(wanted) and (wanted == seen or wanted in seen or (len(seen) > 3 and seen in wanted))


def safe_to_open(path):
    path = Path(path)
    return path.is_file() and path.suffix.lower() not in NEVER_AUTO_OPEN


class Monitors:
    """User rules in routines.sqlite3, plus the user-facing commands that manage them."""

    def __init__(self, path, clock=lambda: datetime.now().astimezone()):
        self.path, self.clock = str(path), clock
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitors (id INTEGER PRIMARY KEY, condition TEXT NOT NULL, '
                       'arg TEXT NOT NULL, actions TEXT NOT NULL, repeat INTEGER NOT NULL, status TEXT NOT NULL, '
                       'created TEXT NOT NULL, fired TEXT NOT NULL DEFAULT "", fire_count INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS monitor_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def paused(self):
        with self._db() as db:
            row = db.execute("SELECT value FROM monitor_flags WHERE key='paused'").fetchone()
        return bool(row and row[0] == '1')

    def set_paused(self, value):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO monitor_flags VALUES ('paused', ?)", ('1' if value else '0',))

    def active(self):
        with self._db() as db:
            rows = db.execute("SELECT id, condition, arg, actions, repeat, fire_count FROM monitors "
                              "WHERE status='active' ORDER BY id").fetchall()
        return [dict(id=i, condition=c, arg=a, actions=json.loads(x), repeat=bool(r), fire_count=n)
                for i, c, a, x, r, n in rows]

    @property
    def notifications_paused(self):
        with self._db() as db:
            row = db.execute("SELECT value FROM monitor_flags WHERE key='notifications_paused'").fetchone()
        return bool(row and row[0] == '1')

    def set_notifications_paused(self, value):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO monitor_flags VALUES ('notifications_paused', ?)", ('1' if value else '0',))

    def needs(self):
        needs = {SOURCES[rule['condition']] for rule in self.active()}
        if 'notifications' in needs and self.notifications_paused:
            needs.discard('notifications')
        return needs

    @staticmethod
    def describe(rule):
        when = DESCRIBE[rule['condition']].format(arg=rule['arg'], from_arg=f' from {rule["arg"]}' if rule['arg'] else '')
        what = []
        for action in rule['actions']:
            what.append({'notify': 'tell you', 'fly': 'fly around', 'open_download': 'open the file', 'read': 'read it to you',
                         'handle': 'offer to handle it (you approve first)',
                         'codex_watch': 'resume Codex after the reset'}.get(action['kind'])
                        or (f'say “{action["text"]}”' if action['kind'] == 'speak' else
                            f'type “{action["text"]}” in {TARGETS[action["target"]][0]}' if action['kind'] == 'type' else
                            f'open {action.get("label")}'))
        return f'when {when} → ' + ', '.join(what)

    def add(self, condition, arg, actions, repeat):
        with self._db() as db:
            return db.execute("INSERT INTO monitors(condition, arg, actions, repeat, status, created) VALUES (?,?,?,?,'active',?)",
                              (condition, arg, json.dumps(actions), int(repeat), self.clock().isoformat(timespec='seconds'))).lastrowid

    def fired(self, rule):
        with self._db() as db:
            db.execute("UPDATE monitors SET fire_count=fire_count+1, fired=?, status=? WHERE id=?",
                       (self.clock().isoformat(timespec='seconds'), 'active' if rule['repeat'] else 'done', rule['id']))

    def stop(self, identifier):
        with self._db() as db:
            changed = db.execute("UPDATE monitors SET status='stopped' WHERE id=? AND status='active'", (identifier,)).rowcount
        if not changed:
            raise ToolError(f'No active monitor #{identifier}. Say “show monitors”.')
        return f'Stopped monitor #{identifier}.'

    def listing(self):
        rules = self.active()
        if not rules:
            return 'No monitors are active. Example: when claude is done, tell me and fly around.'
        lines = [f'#{rule["id"]} · {self.describe(rule)}' + (' · every time' if rule['repeat'] else ' · once') for rule in rules]
        if self.notifications_paused and any(rule['condition'].startswith('notification') for rule in rules):
            lines.append('Notifications are paused. Say “resume notifications”.')
        return ('Monitoring is paused. Say “resume monitoring” to continue.\n' if self.paused else 'Monitoring on.\n') + '\n'.join(lines)

    def status(self, claude=None):
        rules = self.active()
        if not rules:
            return ''
        text = f'{len(rules)} monitor{"s" if len(rules) != 1 else ""}'
        if self.paused:
            return text + ' · paused'
        if claude and any(rule['condition'] == 'claude_idle' for rule in rules):
            text += f' · Claude: {claude}'
        return text

    def command(self, text):
        """Handle a monitor command typed by the user. Returns the reply, or None if it is not one."""
        key = ' '.join(text.lower().split()).rstrip('.!?')
        key = re.sub(r'^(?:(?:hey )?ano[,! ]+|please )', '', key)
        if key in ('show monitors', 'list monitors', 'what are you monitoring', 'what are you watching'):
            return self.listing()
        if match := re.fullmatch(r'(?:stop|cancel|delete) monitor #?(\d+)', key):
            return self.stop(int(match[1]))
        if key in ('pause monitoring', 'stop monitoring', 'stop all monitors'):
            self.set_paused(True)
            return 'Monitoring paused. Nothing is being watched. Say “resume monitoring” to continue.'
        if key in ('resume monitoring', 'start monitoring'):
            self.set_paused(False)
            return 'Monitoring resumed. ' + (f'{len(self.active())} active monitor(s).' if self.active() else 'No monitors are set yet.')
        if key in ('pause notifications', 'stop reading notifications', 'stop watching notifications', 'stop reading my notifications'):
            self.set_notifications_paused(True)
            return 'Notifications paused: Ano is not reading them. Your other monitors keep running. Say “resume notifications” to continue.'
        if key in ('resume notifications', 'start reading notifications', 'watch notifications', 'start watching notifications'):
            self.set_notifications_paused(False)
            rules = sum(rule['condition'].startswith('notification') for rule in self.active())
            return ('Notifications resumed. ' + (f'{rules} notification rule(s) active.' if rules else
                    'No notification rules yet. Example: when I get a notification from outlook, read it to me.')
                    + ' Bank, password-manager and code notifications are always skipped.')
        rule = parse_rule(text)
        if rule is None:
            return None
        condition, arg, actions, repeat = rule
        for existing in self.active():  # the same sentence twice is one rule, not two
            if (existing['condition'], existing['arg'], existing['actions'], existing['repeat']) == (condition, arg, actions, repeat):
                return f'Monitor #{existing["id"]} already does that: {self.describe(existing)}. Stop it with: stop monitor {existing["id"]}.'
        identifier = self.add(condition, arg, actions, repeat)
        stored = self.active()[-1]
        paused = ' Monitoring is paused; say “resume monitoring”.' if self.paused else ''
        return (f'Monitor #{identifier} on: {self.describe(stored)}. '
                + ('Runs every time.' if repeat else 'Runs once (say “every time” to repeat).')
                + f' Stop with: stop monitor {identifier}.{paused}')


class Sensors:
    """Reads the PC's state on one background thread (UI Automation objects stay on the thread that made them)."""

    def __init__(self, downloads=None, notifications=None):
        self.downloads = Path(downloads) if downloads else Path.home() / 'Downloads'
        self.notification_db = Path(notifications) if notifications else NOTIFICATION_DB
        self._notes_stamp, self._notes = None, {}
        self._timelines = []
        self._next_search = 0.0

    def snapshot(self, needs):
        state = {}
        if 'apps' in needs:
            import psutil
            state['apps'] = sorted({squash(p.info['name']) for p in psutil.process_iter(['name']) if p.info['name']})
        if 'focus' in needs:
            state['focus'] = foreground_app()
        if 'downloads' in needs:
            state.update(self._downloads())
        if 'power' in needs:
            import psutil
            battery = psutil.sensors_battery()
            state['battery'] = (round(battery.percent), bool(battery.power_plugged)) if battery else None
        if 'network' in needs:
            import psutil
            stats = psutil.net_if_stats()
            addresses = psutil.net_if_addrs()
            state['online'] = any(stats[name].isup and any(a.family == 2 and not a.address.startswith(('127.', '169.254.'))
                                                         for a in addresses.get(name, ())) for name in stats)
        if 'cpu' in needs:
            import psutil
            state['cpu'] = psutil.cpu_percent(interval=None)
        if 'claude' in needs:
            state['claude'] = self.claude_state()
        if 'codex' in needs:
            from .codex_watch import check
            result = check()
            state['codex'] = None if not result.get('found') else 'limited' if result.get('limited') else 'running'
        if 'notifications' in needs:
            state.update(self.notifications())
        return state

    def notifications(self, limit=30):
        """{'notification_max': newest id, 'notifications': the newest safe toasts}, read from a temporary copy.

        The copy is made only when the database changed, read, and deleted at once. Sensitive notifications
        are dropped here, so their text never leaves this function.
        """
        files = [self.notification_db] + [self.notification_db.with_name(self.notification_db.name + ext) for ext in ('-wal', '-shm')]
        stamp = tuple((item.stat().st_mtime_ns, item.stat().st_size) if item.exists() else None for item in files)
        if stamp == self._notes_stamp:
            return self._notes
        import shutil
        import tempfile
        from contextlib import closing
        with tempfile.TemporaryDirectory(prefix='ano-notes-') as folder:
            copy = Path(folder) / 'notes.db'
            for source in files:
                if source.exists():
                    shutil.copyfile(source, copy.with_name(copy.name + source.name[len(self.notification_db.name):]))
            with closing(sqlite3.connect(copy)) as db:
                newest = db.execute('SELECT max(Id) FROM Notification').fetchone()[0] or 0
                rows = db.execute("SELECT n.Id, h.PrimaryId, n.Payload, n.ArrivalTime FROM Notification n JOIN NotificationHandler h "
                                  "ON h.RecordId = n.HandlerId WHERE n.Type IN ('toast', 'toastCondensed') "
                                  "ORDER BY n.Id DESC LIMIT ?", (limit,)).fetchall()
        notes = []
        for identifier, sender, payload, arrival in reversed(rows):
            note = read_toast(identifier, sender, payload)
            if note:
                # ArrivalTime is a Windows FILETIME: 100-nanosecond steps since 1601.
                note['arrived'] = (arrival or 0) / 1e7 - 11644473600
                notes.append(note)
        self._notes_stamp, self._notes = stamp, {'notification_max': newest, 'notifications': notes}
        return self._notes

    def _downloads(self):
        partial, newest = [], None
        try:
            for item in self.downloads.iterdir():
                if item.suffix.lower() in PARTIAL_DOWNLOADS:
                    partial.append(item.name)
                elif item.is_file() and not item.name.startswith('.'):
                    modified = item.stat().st_mtime
                    if newest is None or modified > newest[0]:
                        newest = (modified, item)
        except OSError:
            pass
        return {'partial': sorted(partial),
                'newest': {'name': newest[1].name, 'path': str(newest[1])} if newest else {}}

    def claude_state(self):
        """'working' while a Claude Code panel shows its "Claude is working" row, 'idle' otherwise, None if no panel.

        The row is the last entry of the panel's message list. The list is found once (a slow walk of VS Code's
        accessibility tree, about 15 s); after that only its last entry's own labels are read. The send button is
        not used: it reads "Send message" while Claude works if the user has typed a queued message. The user's
        draft in the input box is never read.
        """
        state = self._read_timelines()
        if state is None and time.monotonic() >= self._next_search:
            self._timelines = self._find_timelines()
            self._next_search = time.monotonic() + (30 if not self._timelines else 300)
            state = self._read_timelines()
        return state

    def _read_timelines(self):
        import uiautomation as auto
        if not self._timelines:
            return None
        states = []
        for timeline in list(self._timelines):
            try:
                last = timeline.GetLastChildControl()
                labels = [(control.Name or '').strip().lower()
                          for control, _ in auto.WalkControl(last, includeTop=True, maxDepth=3)] if last else []
                states.append('working' if any(label.startswith('claude is working') for label in labels) else 'idle')
            except Exception:
                self._timelines.remove(timeline)
        if not states:
            return None
        return 'working' if 'working' in states else 'idle'

    @staticmethod
    def _find_timelines():
        """Each Claude Code panel's message list, reached from its message input (input -> container -> list)."""
        import psutil
        import uiautomation as auto
        pids = {p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() == 'code.exe'}
        found = []
        for window in auto.GetRootControl().GetChildren():
            if window.ProcessId not in pids:
                continue
            for control, _ in auto.WalkControl(window, maxDepth=60):
                if control.ControlTypeName != 'EditControl' or not (control.ClassName or '').startswith('messageInput'):
                    continue
                node = control
                for _ in range(8):
                    node = node.GetParentControl()
                    if node is None:
                        break
                    if (node.ClassName or '').startswith('inputContainer_') and node.GetPreviousSiblingControl():
                        previous = node.GetPreviousSiblingControl()
                        if (previous.ClassName or '').startswith('messageGradient'):
                            previous = previous.GetPreviousSiblingControl()
                        if previous is not None and (previous.ClassName or '').startswith('messagesContainer'):
                            found.append(previous)
                        break
        return found


def friendly_sender(sender):
    """'Microsoft.Office.OUTLOOK.EXE.15' -> 'Outlook', 'OpenAI.Codex_2p2nqsd0c76g0!App' -> 'Codex'."""
    key = squash(sender)
    for word, name in FRIENDLY_SENDERS.items():
        if word in key:
            return name
    package = re.split(r'[_!]', sender)[0] if '!' in sender or '_' in sender else sender
    parts = [part for part in re.split(r'[.\\/]', package) if part and not part.isdigit()
             and part.lower() not in ('microsoft', 'windows', 'office', 'exe', 'app', 'client', 'desktop', 'tray')]
    name = parts[-1] if parts else package
    return name.title() if name.isupper() or name.islower() else name


def read_toast(identifier, sender, payload):
    """One notification as {id, sender, app, title, body}, or None if it is empty or sensitive."""
    import xml.etree.ElementTree as ElementTree
    if SENSITIVE_APP.search(sender or ''):
        return None
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', 'replace')
    if not payload or len(payload) > 65536:
        return None
    try:
        texts = [' '.join((node.text or '').split()) for node in ElementTree.fromstring(payload).iter('text')]
    except ElementTree.ParseError:
        return None
    texts = [text for text in texts if text]
    if not texts or SENSITIVE_TEXT.search(' '.join(texts)) or PRIVATE_TITLE.search(' '.join(texts)):
        return None
    return dict(id=identifier, sender=sender, app=friendly_sender(sender), title=texts[0][:120],
                body=' '.join(texts[1:])[:500])


def foreground_app():
    """The foreground app's process name, or '' for a window whose title looks private."""
    import ctypes
    from ctypes import wintypes
    import psutil
    user32 = ctypes.windll.user32
    window = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(window)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(window, title, length + 1)
    if PRIVATE_TITLE.search(title.value):
        return ''
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    try:
        return squash(psutil.Process(pid.value).name())
    except psutil.Error:
        return ''


class MonitorWorker:
    """One background thread: every few seconds take a snapshot of what active rules need and queue events.

    The Qt side calls poll() on its timer and runs actions on the main thread.
    """
    INTERVAL = 10

    def __init__(self, monitors, sensors=None):
        self.monitors, self.sensors, self.engine = monitors, sensors or Sensors(), MonitorEngine()
        self.allowed = lambda: True
        self.results = queue.Queue()
        self.claude = None
        self.codex = None  # 'limited', 'running', or None while ChatGPT is closed or not checked yet
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='ano-monitor', daemon=True)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()

    def _run(self):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            while not self._stop.wait(self.INTERVAL):
                try:
                    needs = self.monitors.needs()
                    if not needs or self.monitors.paused or not self.allowed():
                        self.engine.reset()  # changes while paused never fire later
                        continue
                    snapshot = self.sensors.snapshot(needs)
                    self.claude = snapshot.get('claude')
                    self.codex = snapshot.get('codex')
                    for event in self.engine.events(snapshot):
                        self.results.put(event)
                except Exception:
                    self.engine.reset()  # a failed read is not a change

    def poll(self):
        """Events gathered since the last call, matched against active rules: [(rule, event)]."""
        firings = []
        while True:
            try:
                event = self.results.get_nowait()
            except queue.Empty:
                break
            for rule in self.monitors.active():
                if matches(rule, event):
                    self.monitors.fired(rule)
                    firings.append((rule, event))
        return firings
