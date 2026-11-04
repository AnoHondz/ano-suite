"""Conservative, model-free near-miss routing. This module never dispatches tools.

Only fixed aliases and uniquely close app/site names may auto-match. Suggestions
need an explicit yes in the same identified chat; payload text is never spellchecked.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import re
import threading
import time


@dataclass(frozen=True)
class Decision:
    command: str = ''
    question: str = ''
    confidence: float = 0


ALIASES = {
    'whats my ram':'check memory','whats my memory':'check memory',
    'what is my memory usage':'check memory','how much ram am i using':'check memory',
    "what's my ram": 'check memory', 'what is my ram': 'check memory',
    'show my ram': 'check memory', 'how much memory is free': 'check memory',
    'is my battery ok': 'check battery', 'how is my battery': 'check battery',
    'battery status': 'check battery', 'how much battery is left': 'check battery',
    'am i online': 'check network', 'is my internet working': 'check network',
    'how much storage is free': 'check disk space', 'disk space': 'check disk space',
    'what time is it now': 'what time is it', 'what is the time': 'what time is it',
    'tell me the time': 'what time is it', 'time please': 'what time is it',
    "what's the time": 'what time is it', 'time': 'what time is it',
    "what's today's date": 'what is the date', 'what is todays date': 'what is the date',
    'what is today\'s date': 'what is the date', 'tell me the date': 'what is the date',
    'today date': 'what is the date', 'date': 'what is the date',
    'list my reminders': 'show reminders', 'show my reminders': 'show reminders',
    'list notes': 'show my notes', 'show notes': 'show my notes',
    'show saved notes': 'show my notes', 'list my files': 'list documents',
}
TARGET_ALIASES = {'calc': 'calculator', 'the calculator': 'calculator', 'ms word': 'word',
                  'microsoft word': 'word', 'ms excel': 'excel', 'microsoft excel': 'excel',
                  'google chrome': 'chrome', 'microsoft edge': 'edge', 'code': 'vs code',
                  'trading view': 'tradingview', 'whatsapp web': 'whatsapp', 'whats app': 'whatsapp'}
AUTO_VERBS = {'open', 'launch', 'start', 'opne', 'opn', 'opene', 'oppen', 'openn',
              'lanuch', 'lanch', 'strat', 'satrt'}
CONFIRM = {'yes', 'yes please', 'confirm', 'do it', 'yep', 'yeah', 'sure', 'ok', 'okay'}


def one_edit(a, b):
    """One insertion/deletion/substitution or adjacent transposition."""
    if a == b:
        return True
    if len(a) == len(b):
        different = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(different) == 1 or (len(different) == 2 and different[1] == different[0] + 1
            and a[different[0]] == b[different[1]] and a[different[1]] == b[different[0]])
    if abs(len(a) - len(b)) == 1:
        short, long = sorted((a, b), key=len)
        return any(long[:i] + long[i+1:] == short for i in range(len(long)))
    return False


class LocalTriage:
    def __init__(self, clock=time.monotonic, wall_clock=datetime.now):
        self.clock = clock
        self.wall_clock = wall_clock
        self.pending = {}
        self.lock = threading.Lock()

    def _ask(self, question, chat, command='', choices=None):
        if chat is not None and (command or choices):
            with self.lock:
                self.pending[chat] = (self.clock() + 300, command, choices or {})
        return Decision(question=question)

    def _at_time(self, hour, minute, meridiem, body, chat):
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            return Decision(question='Use a valid time from 1:00 to 12:59 am or pm. No reminder was set.')
        now = self.wall_clock()
        due = now.replace(hour=hour % 12 + (12 if meridiem.lower() == 'pm' else 0),
                          minute=minute, second=0, microsecond=0)
        label = f'{hour}:{minute:02d} {meridiem.lower()}'
        if due <= now:
            due += timedelta(days=1)
            return self._ask(f'That time passed today. Did you mean tomorrow at {label}? No reminder was set.',
                             chat, f'remind me at {due:%Y-%m-%dT%H:%M:%S} to {body}')
        return Decision(command=f'remind me at {due:%Y-%m-%dT%H:%M:%S} to {body}', confidence=1)

    def decide(self, text, chat=None):
        from .routines import APPS, SITES, strip_request_prefix
        raw = strip_request_prefix(text).strip()
        key = re.sub(r'\s+', ' ', raw).lower().rstrip('.!?').replace('’', "'")
        with self.lock:
            pending = self.pending.pop(chat, None) if chat is not None else None
        if pending and pending[0] >= self.clock():
            _, command, choices = pending
            if key in choices:
                chosen = re.fullmatch(r'remind me today at (\d{1,2})(?::(\d{2}))?\s+(am|pm) to (.+)', choices[key], re.S)
                if chosen:
                    return self._at_time(int(chosen[1]), int(chosen[2] or 0), chosen[3], chosen[4], chat)
                return Decision(command=choices[key], confidence=1)
            if command and key in CONFIRM:
                return Decision(command=command, confidence=1)
            if choices and key in CONFIRM | {'maybe', 'not sure'}:
                return self._ask('Please choose am or pm; a yes alone does not specify the time. No reminder was set.',
                                 chat, choices=choices)
            if key in ('no', 'cancel', 'never mind'):
                return Decision(question='Cancelled. No action ran.')
        elif pending and key in CONFIRM:
            return Decision(question='That confirmation expired. Please repeat the command. No action ran.')
        if re.match(r"^(?:do not|don't|dont|never) (?:open|launch|start)\b", key):
            if re.search(r'\b(?:and|or|but|then)\b', key):
                return Decision()
            return Decision(question='No app or site was opened.')
        # Remove only conversational framing; never modify embedded document bodies.
        key = re.sub(r'(?:\s+(?:for me|please|thanks|thank you|right now|now))+$', '', key)
        if key in ALIASES:
            return Decision(command=ALIASES[key], confidence=1)
        if re.fullmatch(r'[\d\s+*/%().^×÷-]+', key) and re.search(r'\d', key) and re.search(r'[+*/%^×÷-]', key):
            return Decision(command='calculate ' + key, confidence=1)
        duration = re.fullmatch(r'remind me in (\d{1,5})\s*(mins?|minutes?|hrs?|hours?) (?:to|about) (.+)', raw, re.I | re.S)
        if duration:
            unit = 'hours' if duration[2].lower().startswith('h') else 'minutes'
            return Decision(command=f'remind me in {duration[1]} {unit} to {duration[3]}', confidence=1)
        simple_duration = re.fullmatch(r'remind me in (an hour|a minute|half an hour) (?:to|about) (.+)', raw, re.I | re.S)
        if simple_duration:
            minutes = {'an hour':60, 'a minute':1, 'half an hour':30}[simple_duration[1].lower()]
            return Decision(command=f'remind me in {minutes} minutes to {simple_duration[2]}', confidence=1)
        explicit = re.fullmatch(r'remind me at (\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?) to (.+)', raw, re.I | re.S)
        if explicit:
            return self._at_time(int(explicit[1]), int(explicit[2] or 0), explicit[3].replace('.',''), explicit[4], chat)
        # A time without am/pm is genuinely ambiguous, even if one is more likely.
        reminder = re.fullmatch(r'remind me at (\d{1,2}(?::\d{2})?)\s+to\s+(.+)', raw, re.I | re.S)
        if reminder:
            hour = int(reminder[1].split(':')[0])
            if ':' in reminder[1] and int(reminder[1].split(':')[1]) > 59:
                return Decision(question='Use minutes from 00 to 59. No reminder was set.')
            if (hour == 0 or 13 <= hour <= 23) and ':' in reminder[1]:
                return self._at_time(hour % 12 or 12, int(reminder[1].split(':')[1]),
                                     'pm' if hour >= 12 else 'am', reminder[2], chat)
            if 1 <= hour <= 12:
                choices = {label: f'remind me today at {label} to {reminder[2]}'
                           for label in (reminder[1] + ' am', reminder[1] + ' pm')}
                choices.update({label.replace(' ', ''): command for label, command in list(choices.items())})
                choices['am'], choices['pm'] = choices[reminder[1] + ' am'], choices[reminder[1] + ' pm']
                return self._ask(f'Did you mean {reminder[1]} am or {reminder[1]} pm? No reminder was set.', chat, choices=choices)
            return Decision(question='Use a valid time with am or pm. No reminder was set.')
        reminder_header = re.split(r'\s+(?:to|about)\s+', raw, maxsplit=1, flags=re.I)[0]
        if re.match(r'^remind me\b', raw, re.I) and re.search(r'\b(before|after|when|until|sunset|sunrise|business|working)\b', reminder_header, re.I):
            return Decision()  # retain the full timing problem for the selected reasoning brain
        if re.match(r'^remind me (?:to|about|at|in|on|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', raw, re.I):
            return Decision(question='When should I remind you? Use a duration or a date and time with am or pm. No reminder was set.')
        note = re.fullmatch(r'(?:make|save|take|add) (?:a )?note (?:that |to )?(.+)', raw, re.I | re.S)
        if note and not note[1].startswith(':'):
            return Decision(command='take a note: ' + note[1], confidence=1)
        if key in ('read file', 'open file', 'list folder', 'find files named'):
            return Decision(question='Which file or folder? Supply its name or allowed local path. No action ran.')
        if key == 'calculate':
            return Decision(question='What expression should I calculate? No model was called.')
        # A literal domain is not a fuzzy app name. Preserve path/query case and
        # never invent a domain from an arbitrary name or quoted text.
        domain = re.fullmatch(r'(?:open|launch) ((?:www\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?::\d{1,5})?(?:[/?#][^\s<>"\x27]*)?)', raw)
        if domain:
            return Decision(command='open https://' + domain[1], confidence=1)
        launch = re.fullmatch(r'([a-z]+)\s+([a-z][a-z ]{1,40})', key)
        if not launch or not any(one_edit(launch[1], verb) for verb in ('open', 'launch', 'start')):
            return Decision()
        target = launch[2]
        if re.search(r'\b(?:and|then|after|before|while|because|if)\b', target):
            return Decision()  # full multi-step goal belongs to the reasoning/task path
        if re.search(r'\bor\b', target):
            return Decision(question='Which app or site should I open? No action ran.')
        target = TARGET_ALIASES.get(target, target)
        trusted_verb = launch[1] in AUTO_VERBS
        targets = sorted(set(APPS) | set(SITES))
        if target in targets:
            if not trusted_verb:
                return self._ask(f'Did you mean `open {target}`? No action ran. Reply yes to confirm.', chat, 'open ' + target)
            return Decision(command='open ' + target, confidence=1)
        ranked = sorted(((SequenceMatcher(None, target, candidate).ratio(), candidate) for candidate in targets), reverse=True)
        best, candidate = ranked[0]
        margin = best - ranked[1][0]
        if trusted_verb and len(target) >= 5 and best >= .88 and margin >= .12 and one_edit(target, candidate):
            return Decision(command='open ' + candidate, confidence=best)
        if best >= .75:
            return self._ask(f'Did you mean `open {candidate}`? No action ran. Reply yes to confirm.', chat, 'open ' + candidate)
        if ' ' not in target:  # one unknown word is most likely a misspelled app name
            return Decision(question='Which app or site should I open? No action ran.')
        # A phrase, not an app or site name ("start paper trading the gap fill", "start smc paper strategy"):
        # let the local commands and the reasoning brain handle it instead of stopping here.
        return Decision()
