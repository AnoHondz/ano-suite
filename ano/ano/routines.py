"""Explicit, local command recipes. No model call, shell generation, or passive recording."""
import ast
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import operator
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, urlsplit

from .claude import ClaudeError
from .tools import ToolError, local_command
from .intent import command_verb


SITES = {
    'canvas': 'https://strayer.instructure.com/',
    'strayer': 'https://www.strayer.edu/',
    'github': 'https://github.com/',
    'youtube': 'https://www.youtube.com/',
    'google': 'https://www.google.com/',
    'gmail': 'https://mail.google.com/',
    'google calendar': 'https://calendar.google.com/',
    'google docs': 'https://docs.google.com/document/',
    'google sheets': 'https://docs.google.com/spreadsheets/',
    'google drive': 'https://drive.google.com/',
    'wikipedia': 'https://www.wikipedia.org/',
    'khan academy': 'https://www.khanacademy.org/',
    'desmos': 'https://www.desmos.com/calculator',
    'overleaf': 'https://www.overleaf.com/',
    'outlook web': 'https://outlook.live.com/mail/',
    'whatsapp': 'https://web.whatsapp.com/',
    'tradingview': 'https://www.tradingview.com/chart/',
}
APPS = {
    'calculator': 'Calculator', 'notepad': 'Notepad', 'word': 'Word',
    'excel': 'Excel', 'powerpoint': 'PowerPoint', 'outlook': 'Outlook',
    'paint': 'Paint', 'snipping tool': 'Snipping Tool', 'spotify': 'Spotify',
    'vs code': 'Visual Studio Code', 'visual studio code': 'Visual Studio Code',
    'chrome': 'Google Chrome', 'brave': 'Brave', 'edge': 'Microsoft Edge',
}
MEDIA = {
    'pause music': 'play_pause', 'play music': 'play_pause', 'play pause': 'play_pause',
    'next song': 'next', 'previous song': 'previous', 'stop music': 'stop',
    'mute sound': 'mute', 'toggle mute': 'mute', 'volume up': 'volume_up', 'volume down': 'volume_down',
}
CHECKS = {
    'check battery': 'Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining,BatteryStatus',
    'check disk space': 'Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free',
    'check memory': 'Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory',
    'check network': 'Get-NetAdapter | Select-Object Name,Status,LinkSpeed',
    'check printers': 'Get-Printer | Select-Object Name,PrinterStatus',
    'check running apps': 'Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 12 Name,CPU,WorkingSet64',
    'check windows version': 'Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber',
    'check uptime': 'Get-CimInstance Win32_OperatingSystem | Select-Object LastBootUpTime',
    'check time zone': 'Get-TimeZone | Select-Object Id,DisplayName',
    'check audio devices': 'Get-CimInstance Win32_SoundDevice | Select-Object Name,Status',
    'check display devices': 'Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status',
    'check storage devices': 'Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,Size',
    'check print jobs': 'Get-CimInstance Win32_PrintJob | Select-Object Name,Document,Status',
    'check network addresses': 'Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress',
    'check recent updates': 'Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID,InstalledOn',
}
MAX_STEPS = 8
DEFAULT_ROUTINES = {
    'study': ['open canvas', 'open word', 'open calculator'],
    'course notes': ['open canvas', 'open notepad'],
    'research': ['open google', 'open wikipedia', 'open google docs'],
    'coding': ['open github', 'open vs code'],
    'writing': ['open word', 'open google docs'],
    'spreadsheets': ['open excel', 'open google sheets'],
    'presentations': ['open powerpoint', 'open google drive'],
    'email': ['open gmail'],
    'quick notes': ['open notepad'],
    'math practice': ['open desmos', 'open calculator'],
    'video learning': ['open youtube', 'open notepad'],
    'budget worksheet': ['open excel', 'open calculator'],
    'study resources': ['open khan academy', 'open wikipedia'],
    'project workspace': ['open github', 'open vs code', 'open my documents'],
    'laptop check': ['check laptop health', 'check battery', 'check disk space'],
    'connection check': ['check browser connection', 'check network'],
    'focus timer': ['remind me in 25 minutes to take a break'],
    'calendar review': ['open google calendar', 'show reminders'],
    'capture workspace': ['open snipping tool', 'open paint'],
    'document workspace': ['open my documents', 'open my downloads'],
    'daily review': ['show reminders', 'show my notes'],
}


@dataclass
class Plan:
    title: str
    steps: list = field(default_factory=list)
    text: str = ''
    uses_model: bool = False


def normalize(text):
    text = command_verb(strip_request_prefix(text))
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def strip_request_prefix(text):
    text = text.strip()
    for _ in range(3):
        text = re.sub(r'^(?:(?:hey\s+)?ano[,! ]+|please\s+|(?:can|could|would) you(?: please)?\s+'
                      r'|(?:can|could|may) [il](?: please)? (?:get|have|see)\s+)',
                      '', text, count=1, flags=re.I)
    return text


def natural_reminder(text, now):
    """Parse a bounded, explicit day + 12-hour clock grammar without an LLM."""
    day = r'(today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)'
    clock = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)'
    match = re.fullmatch(r'remind me (?:on )?' + day + r'(?: at)?\s+' + clock + r'\s+to\s+(.+)', text, re.I | re.S)
    if not match:
        match = re.fullmatch(r'(?:on )?' + day + r'(?: at)?\s+' + clock + r':\s*remind me to\s+(.+)', text, re.I | re.S)
    if not match:
        return None
    label, hour, minute, meridiem, body = match.groups()
    hour, minute = int(hour), int(minute or 0)
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise ToolError('Use a valid time from 1:00 to 12:59 am or pm.')
    hour = hour % 12 + (12 if meridiem.lower() == 'pm' else 0)
    label = label.casefold()
    if label in ('today', 'tomorrow'):
        days = int(label == 'tomorrow')
    else:
        weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        days = (weekdays.index(label) - now.weekday()) % 7
        if not days and now.replace(hour=hour, minute=minute, second=0, microsecond=0) <= now:
            days = 7
    due = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        raise ToolError('That time has already passed today. Choose a future time; nothing was scheduled.')
    return body, due


def calculate(expression):
    expression = expression.replace('×', '*').replace('÷', '/').replace('^', '**')
    for word, symbol in [('divided by', '/'), ('multiplied by', '*'), ('times', '*'), ('plus', '+'), ('minus', '-')]:
        expression = re.sub(r'\b' + word + r'\b', symbol, expression, flags=re.I)
    percent = re.fullmatch(r'\s*([\d.]+)\s*%\s+of\s+([\d.]+)\s*', expression, re.I)
    if percent:
        expression = f'{percent[1]} / 100 * {percent[2]}'
    if len(expression) > 160:
        raise ToolError('Keep calculations under 160 characters.')
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
                  ast.Pow: operator.pow}
    try:
        tree = ast.parse(expression, mode='eval')
        if len(list(ast.walk(tree))) > 60:
            raise ValueError()
        def evaluate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = node.value
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp) and type(node.op) in operations:
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Pow) and abs(right) > 12:
                    raise ValueError()
                value = operations[type(node.op)](left, right)
            else:
                raise ValueError()
            if type(value) not in (int, float) or not abs(value) <= 1e100:
                raise ValueError()
            return value
        value = evaluate(tree.body)
        return str(int(value)) if value == int(value) else f'{value:.12g}'
    except (SyntaxError, ValueError, OverflowError, ZeroDivisionError, TypeError):
        raise ToolError('Use numbers, parentheses, +, -, *, /, //, %, or powers up to 12. Check for division by zero.') from None


class RoutineStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS routines (name TEXT PRIMARY KEY, commands TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, name):
        with self._db() as db:
            row = db.execute('SELECT commands FROM routines WHERE name=?', (name.casefold(),)).fetchone()
        return json.loads(row[0]) if row else None

    def names(self):
        with self._db() as db:
            return [row[0] for row in db.execute('SELECT name FROM routines ORDER BY name')]

    def save(self, name, commands):
        with self._db() as db:
            if db.execute('SELECT 1 FROM routines WHERE name=?', (name,)).fetchone():
                raise ToolError('That routine name is already saved. Choose another name so the existing routine stays intact.')
            db.execute('INSERT INTO routines VALUES (?,?)', (name, json.dumps(commands)))


class LocalRoutines:
    def __init__(self, toolbox, store, folders=None, clock=datetime.now, scheduled=None, monitors=None):
        self.toolbox, self.store = toolbox, store
        self.folders = folders or {}
        self.clock = clock
        self.scheduled = scheduled  # ScheduledActions; only reply() reaches it, so only the user's own words schedule
        self.monitors = monitors  # Monitors; likewise only the user's own "when …, …" words create a rule
        self.challenges = None  # ChallengeRunner, set by app.py once the Controller exists
        self.goals = None  # GoalRunner ("keep working on …"), likewise
        self.background = None  # BackgroundTasks ("in the background: …"), likewise
        self.claude_jobs = None  # ClaudeJobs: work requests no local command handles go to a hidden Claude Code session
        self.forced_local = False  # set per request by PaidTasks when the user typed "local:" on the OpenAI brain
        self.ict_watch = None  # IctWatch ("watch ict setups"), set by app.py
        self.care = None  # local proactive health observer; no model or repair execution
        self.repairs = None  # fixed verified repair policy, not arbitrary model commands
        from .triage import LocalTriage
        self.triage = LocalTriage(wall_clock=self.clock)
        self.last_plan_uses_model = False

    def route_reply(self, text, chat=None, history=None):
        prefix = re.fullmatch(r'\s*local:\s*(\S[\s\S]*)', text, re.I)
        previous = self.forced_local
        try:
            if prefix:
                text = prefix[1]
                self.forced_local = True
            decision = self.triage.decide(text, chat)
            exact = self.reply(text, history=history)
            if exact is not None:
                return exact
            if decision.question:
                return decision.question + '\n\n· Local clarification · No LLM used'
            return self.reply(decision.command) if decision.command else None
        finally:
            self.forced_local = previous

    def install_defaults(self):
        """Seed recipes, without running them or replacing any user-taught recipe."""
        for name, commands in DEFAULT_ROUTINES.items():
            if self.store.get(name) is None and all(self.plan(command, False) is not None for command in commands):
                self.store.save(name, commands)

    def catalog(self):
        lines = [
            'Local commands: exact read/write/research commands work without an LLM. Original drafts use the local model. Use one command per message.',
            'Apps: ' + '; '.join('open ' + name for name in APPS),
            'Websites: ' + '; '.join('open ' + name for name in SITES),
            'Folders: ' + '; '.join('open my ' + name for name in self.folders),
            'Media: ' + '; '.join(MEDIA) + '. Play/pause and mute are toggles; current playback is not read.',
            'PC checks: check laptop health; ' + '; '.join(CHECKS),
            'Windows: list windows; focus window 123; minimize window 123; maximize window 123; restore window 123.',
            'Browser: list tabs; check browser connection. These require the existing extension.',
            'Website work: show website tasks lists saved progress. Ask Ano to continue a saved website task after finishing login and enabling control.',
            'Search: search web for TOPIC; search youtube for TOPIC; search github for TOPIC; find files named WORDS.',
            'Read: read public page https://example.org; read file FULL_PATH; list folder FULL_PATH.',
            'Documents: list documents; read document NAME; summarize document NAME; summarize file FULL_PATH.',
            'Word files: write word document NAME: EXACT TEXT; read word document NAME; make a word report about Ano. New .docx files are verified by text read-back and never overwrite existing files. Opening may require Office setup.',
            'Word drafts: draft word document NAME about TOPIC. Uses the local model, labels its draft, and verifies the saved Word text. Factual accuracy still needs review.',
            'Diagnostics: show recent actions; show local AI status. Reports actual tool outcomes and local server/RAM state without an AI generation request.',
            'Write exact text: write document NAME: YOUR TEXT; append document NAME: MORE TEXT; replace in document NAME: OLD => NEW. Only .md/.txt in AnoWorkspace; edits keep backups.',
            'Research: research TOPIC. Reads up to three public sources and saves linked evidence notes, not a generated analysis.',
            'Research writing: research and summarize TOPIC. Uses the local model for a short cited draft from fetched sources.',
            'Original writing: draft document NAME about TOPIC. Uses the local LLM and saves a draft for your review.',
            'Images: generate image: PROMPT; generate portrait image: PROMPT; generate landscape image: PROMPT. Uses the installed image provider; local SDXS uses no API credits.',
            'Notes: take a note: TEXT; show my notes; find notes about WORDS.',
            'Reminders: remind me in 25 minutes to TEXT; remind me tomorrow at 4:45 pm to TEXT; remind me at YYYY-MM-DDTHH:MM to TEXT; show reminders.',
            'Scheduled typing: at 11:19 pm type "continue" in chatgpt; tomorrow at 9 am type "TEXT" in notepad; '
            'in 30 minutes type "TEXT" in vs code; show scheduled actions; cancel scheduled action 3. Runs unattended and presses Enter.',
            'Codex overnight: watch codex; codex watch status; stop watching codex. Presses Resume goal after a usage-limit reset.',
            'Monitors: when claude is done, tell me and fly around; when my download finishes, open it; '
            'when codex is out of usage, resume it after the reset; when I open spotify, say hello every time; '
            'show monitors; stop monitor 2; pause monitoring; resume monitoring.',
            'Math/time: calculate 24 times 17; calculate 15% of 80; what time is it; what is the date.',
            'Workspaces: start study session; start coding session; start writing session.',
            'Teach: teach routine NAME = open canvas -> open word. Save only; run with: run routine NAME.',
            'Saved routines: ' + (', '.join(self.store.names()) or 'none yet') + '. Say “show routine NAME” to inspect before running.',
            'Custom steps must be supported local commands. Up to eight steps; no shell commands, automatic submissions, or nested routines.',
        ]
        return '\n\n'.join(lines)

    def plan(self, text, teach=True):
        # Preserve paragraphs and whitespace inside exact user-supplied document text.
        literal = strip_request_prefix(text)
        from .supplied_commands import parse_supplied_document, supplied_document_prefix
        supplied = parse_supplied_document(literal)
        if supplied is not None:
            return (Plan('Supplied document clarification', text=supplied.question) if supplied.question else
                    Plan('Save exact supplied document', [(supplied.tool, supplied.arguments)]))
        if supplied_document_prefix(literal):
            return None  # keep the full unhandled goal out of legacy colon-command parsing
        # Reminder bodies are data, just like document text: preserve interior
        # whitespace before normalizing the command framing below.
        if re.match(r'^remind me\b|^(?:on )?(?:today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b', literal, re.I):
            reminder = natural_reminder(literal, self.clock())
            if reminder:
                body, due = reminder
                return Plan('Reminder', [('add_reminder', {'text':body,'when':due.isoformat(timespec='seconds')})])
            relative = re.fullmatch(r'remind me in (\d{1,5}) (minutes?|hours?) to (.+)', literal, re.I | re.S)
            if relative:
                amount, unit, body = relative.groups()
                minutes = int(amount) * (60 if unit.lower().startswith('hour') else 1)
                if not 1 <= minutes <= 525600:
                    raise ToolError('Choose a reminder between one minute and one year from now.')
                due = self.clock() + timedelta(minutes=minutes)
                return Plan('Reminder', [('add_reminder', {'text':body,'when':due.isoformat(timespec='seconds')})])
            absolute = re.fullmatch(r'remind me at (\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?) to (.+)', literal, re.I | re.S)
            if absolute:
                return Plan('Reminder', [('add_reminder', {'text':absolute[2],'when':absolute[1]})])
        revision = re.fullmatch(r'(?:revise|edit) word document (.+?):\s*(\{.*\})', literal, re.I | re.S)
        if revision:
            try:
                spec = json.loads(revision[2])
            except ValueError:
                return Plan('Revision needs valid data', text='Supply valid JSON with old, new and output_name. Nothing was written.')
            if not isinstance(spec, dict) or set(spec) - {'old', 'new', 'output_name', 'expected_count'} or not {'old', 'new', 'output_name'} <= set(spec):
                return Plan('Revision needs valid data', text='Supply old, new and output_name, and optionally expected_count. Nothing was written.')
            return Plan('Revise Word document', [('revise_word_document', {'name': revision[1], **spec})])
        presentation = re.fullmatch(r'create (?:presentation|powerpoint) (.+?):\s*(\{.*\})', literal, re.I | re.S)
        if presentation:
            try:
                spec = json.loads(presentation[2])
            except ValueError:
                return Plan('Presentation needs valid data', text='Supply valid JSON with title and slides. Nothing was written.')
            if not isinstance(spec, dict) or set(spec) - {'title', 'subtitle', 'slides'} or not {'title', 'slides'} <= set(spec):
                return Plan('Presentation needs valid data', text='Supply title and slides, and optionally subtitle. Nothing was written.')
            return Plan('Create supplied presentation', [('create_presentation', {'name': presentation[1], **spec})])
        spreadsheet = re.fullmatch(r'create spreadsheet (.+?):\s*(\{.*\})', literal, re.I | re.S)
        if spreadsheet:
            try:
                spec = json.loads(spreadsheet[2])
            except ValueError:
                return Plan('Spreadsheet needs valid data', text='Supply valid JSON with headers and rows. Nothing was written.')
            if not isinstance(spec, dict) or set(spec) - {'headers', 'rows', 'chart_column', 'formulas'} or not {'headers', 'rows'} <= set(spec):
                return Plan('Spreadsheet needs valid data', text='Supply headers and rows, and optionally chart_column and formulas. Nothing was written.')
            return Plan('Create supplied spreadsheet', [('create_spreadsheet', {'name': spreadsheet[1], **spec})])
        note = re.fullmatch(r'(?:take|save|add) (?:a )?note:\s*(.+)', literal, re.I | re.S)
        if note:
            return Plan('Save note', [('add_note', {'text': note[1]})])
        for pattern, tool, fields in [
            (r'(?:write|create) pdf (.+?): ?(.+)', 'create_pdf', ('name', 'text')),
            (r'(?:write|create) word document (.+?): ?(.+)', 'write_word_document', ('name', 'text')),
            (r'write document (.+?): ?(.+)', 'write_document', ('name', 'text')),
            (r'append document (.+?): ?(.+)', 'append_document', ('name', 'text')),
            (r'replace in document (.+?): (.+?) => (.*)', 'replace_document_text', ('name', 'old', 'new')),
        ]:
            match = re.fullmatch(pattern, literal, re.I | re.S)
            if match:
                arguments = dict(zip(fields, match.groups()))
                if tool == 'create_pdf':
                    arguments['title'] = arguments['name']
                return Plan('Document writing', [(tool, arguments)])
        raw = normalize(text)
        key = raw.casefold().rstrip('.!?')
        if key.startswith(("what's ", 'what’s ')):
            key = 'what is ' + key[7:]
        key = {'list my documents': 'list documents', 'show my documents': 'list documents',
               'list my notes': 'show my notes'}.get(key, key)
        def step(title, tool, **args):
            return Plan(title, [(tool, args)])
        if key in ('health','ano health','scorpred health','reliability status','system health dashboard'):
            return step('Reliability status','reliability_status',verify_broker=False)
        if key in ('trading bot readiness','tradingbot readiness','tradinbot readiness','prepare trading bot','prepare the trading bot','get trading bot ready','get tradinbot ready','check paper readiness','is trading bot ready'):
            return step('Read-only paper readiness','reliability_status',verify_broker=True)
        if key in ('open reliability dashboard','open health dashboard','show reliability dashboard'):
            return step('Open reliability dashboard','open_reliability_dashboard')
        scor_key=re.sub(r'\bscopred\b','scorpred',key)
        mail_aliases={'open sports desk':'ano_sports_desk','sports desk':'ano_sports_desk',
                      'enable email commands':'ano_mail_enable','turn on email commands':'ano_mail_enable',
                      'disable email commands':'ano_mail_disable','stop email commands':'ano_mail_disable',
                      'email command status':'ano_mail_status','phone command status':'ano_mail_status',
                      'email me picks':'ano_sports_picks','email me all picks':'ano_sports_picks',
                      'email me all sports picks':'ano_sports_picks','email me props':'ano_sports_props',
                      'email me player props':'ano_sports_props'}
        mail_aliases['email me paper trading health']='ano_trading_health'
        for sport in ('nba','soccer','nfl'):
            for kind in ('picks','props'):
                for phrase in (f'email me {sport} {kind}',f'send me {sport} {kind}',f'email {sport} {kind}'):
                    mail_aliases[phrase]=f'ano_{sport}_{kind}'
            # User preference: a bare props request is emailed in the saved report format, not shown in chat.
            for phrase in (f'{sport} player props',f'{sport} players props',f'{sport} props',
                           f'{sport} player props today',f'{sport} players props today',f'{sport} props today'):
                mail_aliases[phrase]=f'ano_{sport}_props'
        # Parlays and on-demand reports, in the words the user uses (typed commands only; fixed recipient).
        for phrase in ('player parlays','player parlay','email me player parlays','send me player parlays',
                       'send me the player parlays','email me the player parlays','player parlays email'):
            mail_aliases[phrase]='ano_player_parlays'
        for sport in ('nba','soccer','nfl'):
            for phrase in (f'{sport} player parlays',f'{sport} player parlay',f'email me {sport} player parlays',
                           f'send me {sport} player parlays',f'send me the {sport} player parlays',f'{sport} parlay like the nba'):
                mail_aliases[phrase]=f'ano_{sport}_player_parlays'
        for phrase in ('parlays','parlay','email me parlay','email me parlays','send me parlay','send me parlays',
                       'send me the parlays','email me the parlays','todays parlays',"today's parlays"):
            mail_aliases[phrase]='ano_parlays'
        for phrase in ('send me the email','send in the email','email me the email','send me the morning email',
                       'resend the morning email','resend the email','send me todays email',"send me today's email",
                       'resend my morning scorpred email','send me my morning email'):
            mail_aliases[phrase]='ano_morning_email'
        if key in mail_aliases:
            return step('Ano sports email desk',mail_aliases[key])
        if key in ('odds credits','credits left','how many credits','how many credits left','scorpred credits','odds api credits'):
            return step('ScorPred odds credits','scorpred_credits')
        record=re.fullmatch(r"(?:how (?:did|do|are|were|is) (?:my |the |our |yesterday'?s? )*(?:picks?|parlays?|bets?|props?|player parlays?)"
                            r"(?: (?:do|doing|go|going|hit|look))?|(?:did|do) (?:my |the |yesterday'?s? )*(?:picks?|parlays?|bets?) (?:hit|win|cash)|"
                            r"(?:my |the )?(?:picks?|parlay) (?:record|results|tracker)|how did we do|pick tracker)"
                            r"(?: (?P<when>yesterday|last night|today|this week|last week|the last 7 days|this month|last 30 days))?",key)
        if record:
            when=record['when'] or 'yesterday'
            days=7 if 'week' in when or '7' in when else 30 if 'month' in when or '30' in when else 1
            return step('ScorPred pick record','scorpred_pick_record',days=days)
        if key in ('is the trading bot working','is trading bot working','are the bots working','are the trading bots working',
                   'trading bot status','is the bot trading','is trading working'):
            return step('Read-only paper readiness','reliability_status',verify_broker=True)
        from .sports_query import parse as sports_query
        from .sports_chat import parse_player_prop
        prop_query=parse_player_prop(scor_key)
        if prop_query:return step('Check exact NBA player line','scorpred_player_prop',**prop_query)
        detail_query=re.fullmatch(r'scorpred (nba|soccer|nfl) details(?: (\d{4}-\d{2}-\d{2}))?',scor_key)
        if detail_query:
            return step('Read detailed sports evidence','scorpred_insights',sport=detail_query[1],details=True,
                        **({'date':detail_query[2]} if detail_query[2] else {}))
        from .sports_email import report_context
        player_query=report_context(scor_key,today=self.clock().date())
        if player_query and player_query['props']:
            args={'sport':player_query['sport']}
            if player_query['day']: args['date']=player_query['day']
            return step('Read player-prop research','scorpred_props',**args)
        query=sports_query(scor_key)
        if query:
            return step('Read dated sports insights','scorpred_insights',**query)
        if re.fullmatch(
            r'(?:(?:hey )?ano[, ]+)?(?:please )?'
            r'(?:(?:can|could|would|will) you (?:please )?|(?:tell|ask) (?:chatgpt|ano) to )?'
            r'(?:start|run|open|launch|turn on) (?:the )?scorpred(?: app)?'
            r'(?: (?:for me|now|please|from ano|on my (?:laptop|computer)))?', scor_key) or re.fullmatch(
            r'(?:please )?(?:get scorpred (?:running|started)|'
            r'(?:i want|i need) (?:you to )?(?:run|start|open|launch) scorpred)', scor_key):
            return step('Start local ScorPred','scorpred_start')
        if scor_key in ('repair scorpred','fix scorpred','recover scorpred','restart scorpred','restart the scorpred app'):
            return step('Diagnose and recover stopped ScorPred','scorpred_repair')
        if key in ('repair my laptop','fix my laptop','repair memory','fix high ram','diagnose memory'):
            return Plan('Diagnose before repair',[('system_overview',{})],
                        text='Checking measured PC health first. No apps will be closed or files deleted. '
                             'Stopped ScorPred has a fixed recovery routine; other changes use a reviewed Run fix proposal.')
        if scor_key in ('scorpred status','check scorpred','check scorpred status'):
            return step('ScorPred status','scorpred_status')
        if scor_key in ('refresh scorpred report','generate scorpred report','get scorpred report'):
            return step('Generate local ScorPred report','scorpred_refresh')
        if scor_key in ('email me scorpred results','email me scorpred predictions','email scorpred report',
                         'scorpred email me','send me scorpred predictions','email me scorpred report',
                         "email me today's scorpred predictions",'run scorpred and email me the report',
                         'run scorpred and email me predictions'):
            return step('Email authorized ScorPred daily report','scorpred_email')
        scor=re.fullmatch(r'(?:show |get |check |send me (?:the )?)?scorpred (soccer|nba|nfl)(?: (premier league|bundesliga|la liga))?(?: (\d{4}-\d{2}-\d{2}))?',scor_key)
        if scor:
            args={'sport':scor[1]}
            if scor[2]:
                if scor[1]!='soccer': return None
                args['league']={'premier league':39,'bundesliga':78,'la liga':140}[scor[2]]
            if scor[3]: args['date']=scor[3]
            return step('Read ScorPred insights','scorpred_insights',**args)
        hardware_question = (re.search(r'^(?:which|what) (?:gpu|graphics card|video card)\b', key)
                             and re.search(r'\b(?:do (?:i|you|we) have|(?:is|are) installed|(?:am i|are you) using|'
                                           r'(?:in|on) (?:my|this) (?:computer|pc|laptop))\b', key))
        if key in ('check gpu', 'check graphics', 'show gpu', 'show graphics adapters',
                   'what is my gpu', 'what is my graphics card') or hardware_question:
            return Plan('Read graphics adapters on this PC', [('inspect_system', {'command':
                'Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,VideoProcessor,DriverVersion'})],
                text='Graphics adapters reported by Windows on this PC. AdapterRAM is the Windows-reported value; '
                     'it is not a verified dedicated-VRAM measurement, particularly for integrated graphics.')
        arithmetic = re.fullmatch(r'(?:what is|what[\x27\u2019]s|whats|how much is)\s+(.+?)\??', raw, re.I)
        if arithmetic:
            expression = arithmetic[1]
            words = r'\b(?:times|plus|minus|divided by|multiplied by|of)\b'
            numeric = re.sub(words, '', expression, flags=re.I)
            if (re.search(r'\d', numeric) and re.fullmatch(r'[0-9+\-*/%^().\s×÷]+', numeric)
                    and (re.search(r'[+\-*/%^×÷]', expression) or re.search(words, expression, re.I))):
                return Plan('Calculator', text=calculate(expression))
        if key in ('show recent actions', 'show action log', 'why did my last task fail'):
            return step('Local action diagnostics', 'recent_tool_activity')
        if key in ('paper trading status','check paper connection'):
            return step('Paper connection check','paper_connection_status')
        if key in ('can you trade on tradingview','can you make trades on tradingview',
                   'are you able to make trades on trading view','paper trading capabilities'):
            runner=getattr(self.ict_watch,'executor',None) if self.ict_watch else None
            state=('ON' if runner.enabled else 'OFF') if runner else 'not loaded or not connected'
            modes=getattr(runner,'strategy_enabled',()) if runner else ()
            modes=modes if isinstance(modes,(tuple,list)) else ()
            return Plan('Paper trading capabilities',text=(
                f'Experimental gap-fill paper runner: {state}. It uses Alpaca paper orders for QQQ/DIA under v1.2, '
                'with data, news, calendar and risk checks. Connect that Alpaca PAPER account in TradingView to view its orders; '
                'TradingView’s own Paper Trading simulator is a different account. '
                f'Additional enabled paper adapters: {", ".join(modes) or "none"}. '
                +('ICT paper execution is enabled. ' if 'ict' in modes else 'ICT is analysis-only with current settings. ')+
                'Only configured strategy adapters may execute; arbitrary model-chosen orders and live trading are unavailable. '
                'For chart analysis, say “analyze qqq” or “show chart assistant”.'))
        if key in ('show chart assistant','give me the ano chart indicator'):
            return step('Ano chart indicator','ict_tradingview_indicator',strategy='chart assistant')
        if key in ('tradingview indicator','trading view indicator','ano setups indicator','give me the tradingview indicator',
                   'make me a tradingview indicator','show possible entries on tradingview','forex tradingview indicator'):
            return step('Ano Setups indicator','ict_tradingview_indicator',strategy='ano setups')
        silver = re.fullmatch(r'(?:show |check |analyze |analyse |scan )?silver bullet(?: (?:on|for))? (qqq|dia)', key)
        if silver:
            return step('Silver Bullet research','silver_bullet_setups',symbol=silver[1].upper())
        smc = re.fullmatch(r'(?:show |check |analyze |analyse |scan )?(?:smc|smart money concepts)(?: (?:on|for))? (qqq|dia)', key)
        if smc:
            return step('SMC research', 'smc_analysis', symbol=smc[1].upper())
        if key in ('smc rules','learn smc','train smc','train ano smc'):
            return Plan('SMC rules',text=(
                'Ano SMC v1: confirmed swing breaks (BOS/CHOCH), liquidity sweeps, displacement, '
                'three-candle FVGs and premium/discount from the latest confirmed swing range. '
                'Order block: last opposite candle within ten consecutive bars before a displaced structure break; full wick zone. '
                'A close beyond its far edge invalidates it and confirms an opposite breaker; retests occur only after confirmation. '
                'Daily/4h/1h bias compares the last two completed source-bar closes; missing data means unavailable. '
                'Scan with “smc qqq” or “smc dia”; Silver Bullet adds strict time and closed 15m direction filters. '
                '“Run the trading model” monitors all implemented strategies. '
                'Research definitions only; no automatic SMC orders, trade quota or proven edge.'))
        if key in ('silver bullet rules','learn silver bullet','train silver bullet','train ano silver bullet'):
            return Plan('Silver Bullet rules', text=(
                'Ano Silver Bullet v1: research only, no automatic orders. QQQ/DIA; New York 10–11 AM or 2–3 PM. '
                'Sweep a named level, align with the last two complete 15-minute RTH closes before the sweep, '
                'then confirm displacement, market structure shift and a three-candle FVG inside the same window. '
                'Entry at the FVG midpoint only after confirmation; stop beyond the sweep; untouched target at least 2R away. '
                'Entry expires after 30 elapsed minutes or at window end; simulated positions exit at window end. '
                'Missing data means WAIT; no daily/1h bias filter or proven edge. Say “silver bullet qqq”.'))
        indicators = re.fullmatch(r'(?:analyze|analyse|check) (qqq|dia)(?: chart| indicators| technicals)?',key)
        if indicators:
            return step('Numeric chart analysis','analyze_paper_indicators',symbol=indicators[1].upper())
        if key in ('is ollama running', 'show local ai status', 'check local ai',
                   'which models do i have', 'show ai memory use', 'ram status', 'automatic ram status'):
            return step('Local AI status', 'local_ai_status')
        if key in ('how much ram do i have', 'what is the ram of my pc', 'whats the ram of my pc',
                   'what processor do i have', 'what cpu do i have', 'how much disk space do i have',
                   'whats the processing speed of my laptop', 'what is the processing speed of my laptop'):
            return step('Verified PC facts', 'system_overview')
        reminder = natural_reminder(raw, self.clock())
        if reminder:
            body, due = reminder
            return step('Reminder', 'add_reminder', text=body, when=due.isoformat(timespec='seconds'))
        report = (r'(?:open (?:microsoft )?word and (?:make sure you )?)?'
                  r'(?:make|create|write) (?:a )?(?:word )?(?:document|report) '
                  r'(?:on|about) (?:everything (?:on|about) )?ano[.!?]*')
        if re.fullmatch(report, raw, re.I):
            return step('Verified Ano Word report', 'create_ano_report', open_document=True)
        match = re.fullmatch(r'read word document (.+)', raw, re.I)
        if match:
            return step('Read Word document', 'read_word_document', name=match[1])
        image = re.fullmatch(r'(?:generate|create) (?:(square|portrait|landscape) )?image:\s*(.+)', literal, re.I | re.S)
        if image:
            return step('Image generation', 'generate_image', prompt=image[2], shape=(image[1] or 'square').lower())
        image = re.fullmatch(r'(?:generate|create|make|draw) (?:an? )?(?:image|picture|drawing|photo) of (.+)',
                             literal, re.I | re.S)
        if image:
            return step('Image generation', 'generate_image', prompt=image[1], shape='square')
        if key in ('show commands', 'local commands', 'what can you do without ai', 'show my routines',
                   'what are your capabilities', 'what are yr capabilities', 'what can you do'):
            return Plan('Command library', text=self.catalog())
        if teach and re.match(r'^show routine\s+', raw, re.I):
            name = re.sub(r'^show routine\s+', '', raw, flags=re.I).strip().casefold()
            commands = self.store.get(name)
            if commands is None:
                raise ToolError('No routine has that name. Say “show my routines”.')
            return Plan('Inspect routine', text=f'Routine “{name}”: ' + ' -> '.join(commands) + '\nNothing ran.')
        if teach and re.match(r'^teach routine\b', raw, re.I):
            match = re.fullmatch(r'teach routine ([\w][\w -]{0,49})\s*=\s*(.+)', raw, re.I)
            if not match:
                raise ToolError('Use: teach routine NAME = open canvas -> open word')
            name, sequence = match.groups()
            name = name.strip().casefold()
            commands = [item.strip() for item in sequence.split('->')]
            if not 1 <= len(commands) <= MAX_STEPS or any(not command for command in commands):
                raise ToolError('Teach between one and eight nonempty steps, separated with ->.')
            plans = [self.plan(command, teach=False) for command in commands]
            if any(plan is None for plan in plans) or sum(len(plan.steps) for plan in plans) > MAX_STEPS:
                raise ToolError('Every taught step must be a supported local command; maximum eight actions. Nothing was saved or run.')
            self.store.save(name, commands)
            return Plan('Learned routine', text=f'Saved routine “{name}” with {len(commands)} steps. Nothing ran. Say “run routine {name}” to use it.')
        if teach and re.match(r'^run routine\b', raw, re.I):
            name = re.sub(r'^run routine\s*', '', raw, flags=re.I).strip().casefold()
            commands = self.store.get(name)
            if not commands:
                raise ToolError('No routine has that name. Say “show my routines”.')
            plans = [self.plan(command, teach=False) for command in commands]
            if any(plan is None for plan in plans):
                raise ToolError('A saved step is no longer supported. The routine was not run.')
            return Plan(name, [item for plan in plans for item in plan.steps], '\n'.join(p.text for p in plans if p.text),
                        uses_model=any(p.uses_model for p in plans))
        if key.startswith(('teach routine', 'run routine', 'show routine')):
            return None
        if key in MEDIA:
            return step('Media control', 'media_control', action=MEDIA[key])
        if key == 'check memory':  # readable "Memory: 9.1 of 15.9 GB used (57%)" plus the biggest programs, not a raw table
            return step('Verified PC facts', 'system_overview')
        if key in CHECKS:
            return step('Read-only PC check', 'inspect_system', command=CHECKS[key])
        fixed = {'check laptop health': 'system_overview', 'list windows': 'list_windows',
                 'show startup apps': 'list_startup_apps', 'list startup apps': 'list_startup_apps',
                 'open startup settings': 'open_startup_settings',
                 'list tabs': 'list_tabs', 'check browser connection': 'browser_connection_status',
                 'show my notes': 'search_notes', 'show reminders': 'list_reminders', 'list documents': 'list_documents',
                 'show website tasks': 'list_browser_tasks'}
        if key in fixed:
            return step(key, fixed[key])
        if key in ('start ano with windows', 'start ano when windows starts'):
            return step('Ano at sign-in', 'set_ano_autostart', enabled=True)
        if key in ('do not start ano with windows', 'stop ano starting with windows'):
            return step('Ano at sign-in', 'set_ano_autostart', enabled=False)
        for pattern, tool in [(r'read document (.+)', 'read_document'),
                              (r'summarize document (.+)', 'summarize_document'),
                              (r'summarize file (.+)', 'summarize_document')]:
            match = re.fullmatch(pattern, raw, re.I)
            if match:
                return step('Document reading', tool, name=match[1])
        match = re.fullmatch(r'draft document (.+?) about (.+)', raw, re.I)
        if match:
            return Plan('Local model draft', [('draft_document', {'name': match[1], 'topic': match[2]})], uses_model=True)
        match = re.fullmatch(r'draft word document (.+?) about (.+)', raw, re.I)
        if match:
            return Plan('Local Word draft', [('draft_word_document', {'name': match[1], 'topic': match[2]})], uses_model=True)
        match = re.fullmatch(r'research and summarize (.+)', raw, re.I)
        if match:
            return Plan('Local research synthesis', [('research_and_summarize', {'topic': match[1]})], uses_model=True)
        match = re.fullmatch(r'research (.+)', raw, re.I)
        if match:
            return step('Research evidence notes', 'research_topic', topic=match[1])
        if key in ('what time is it', 'what is the date'):
            return Plan('Local clock', text=self.clock().strftime('%A, %B %d, %Y at %I:%M %p'))
        if key in ('start study session', 'start writing session', 'start coding session'):
            commands = {'start study session': ['open canvas', 'open word', 'open calculator'],
                        'start writing session': ['open word', 'open google docs'],
                        'start coding session': ['open github', 'open vs code']}[key]
            return Plan(key, [item for cmd in commands for item in self.plan(cmd, False).steps])
        # Known sites with an explicit browser do not need model tool selection.
        # Whole-message matching prevents partial execution of compound requests.
        browser_site = re.fullmatch(r'open (?:my )?(.+?) (?:on|in|using) (?:google )?(chrome|edge|brave)[.!?]*', raw, re.I)
        if browser_site and browser_site[1].casefold() in SITES:
            return step('Open website in browser', 'open_browser_tab',
                        browser=browser_site[2].lower(), url=SITES[browser_site[1].casefold()])
        match = re.fullmatch(r'(?:open|launch|start) (.+)', raw, re.I)
        if match:
            target = match[1].casefold().rstrip('.!?')
            if target in ('microsoft word', 'ms word'):
                target = 'word'
            if target in SITES:
                return step('Open website', 'open_website', url=SITES[target])
            if target in APPS:
                return step('Open app', 'open_app', name=APPS[target])
            folder = target.removeprefix('my ')
            if folder in self.folders:
                return step('Open folder', 'open_file', path=str(self.folders[folder]))
            if match[1].startswith('https://'):
                parsed = urlsplit(match[1])
                if not parsed.hostname or parsed.username or parsed.password:
                    raise ToolError('Use an HTTPS address without embedded credentials.')
                return step('Open website', 'open_website', url=match[1])
        patterns = [
            (r'search web for (.+)', 'open_website', 'url', lambda x: 'https://www.google.com/search?q=' + quote(x)),
            (r'search youtube for (.+)', 'open_website', 'url', lambda x: 'https://www.youtube.com/results?search_query=' + quote(x)),
            (r'search github for (.+)', 'open_website', 'url', lambda x: 'https://github.com/search?q=' + quote(x)),
            (r'find files named (.+)', 'find_files', 'query', str),
            (r'read public page (https://\S+)', 'read_public_page', 'url', str),
            (r'read file (.+)', 'read_file', 'path', str),
            (r'open file (.+)', 'open_file', 'path', str),
            (r'list folder (.+)', 'list_folder', 'path', str),
            (r'take a note:\s*(.+)', 'add_note', 'text', str),
            (r'find notes about (.+)', 'search_notes', 'query', str),
        ]
        for pattern, tool, argument, convert in patterns:
            match = re.fullmatch(pattern, raw, re.I)
            if match:
                return step('Local command', tool, **{argument: convert(match[1])})
        match = re.fullmatch(r'(focus|minimize|maximize|restore) window (\d+)', key)
        if match:
            return step('Window control', 'window_action', window_id=int(match[2]), action=match[1])
        match = re.fullmatch(r'calculate (.+)', raw, re.I)
        if match:
            return Plan('Calculator', text=calculate(match[1]))
        match = re.fullmatch(r'remind me in (\d{1,5}) (minutes?|hours?) to (.+)', raw, re.I)
        if match:
            amount, unit, text = match.groups()
            minutes = int(amount) * (60 if unit.lower().startswith('hour') else 1)
            if not 1 <= minutes <= 525600:
                raise ToolError('Choose a reminder between one minute and one year from now.')
            due = self.clock() + timedelta(minutes=minutes)
            return step('Reminder', 'add_reminder', text=text, when=due.isoformat(timespec='seconds'))
        match = re.fullmatch(r'remind me at (\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?) to (.+)', raw, re.I)
        if match:
            return step('Reminder', 'add_reminder', text=match[2], when=match[1])
        # Compose only explicit known commands after all literal-body commands
        # have had a chance to match. Preflight every step before doing anything.
        parts = re.split(r'\s+(?:and then|then|and)\s+(?=(?:open|launch|start|read|list|show|calculate|check|take|remind|write)\b)', raw, flags=re.I)
        if len(parts) > 1:
            if len(parts) > MAX_STEPS:
                raise ToolError('Use at most eight explicit local commands. Nothing ran.')
            plans = [self.plan(part, teach=False) for part in parts]
            if all(plan is not None for plan in plans):
                steps = [item for plan in plans for item in plan.steps]
                if len(steps) > MAX_STEPS:
                    raise ToolError('This workflow exceeds eight actions. Nothing ran.')
                return Plan('Local multi-step workflow', steps,
                            '\n'.join(plan.text for plan in plans if plan.text),
                            any(plan.uses_model for plan in plans))
        return None

    def reply(self, text, history=None):
        self.last_plan_uses_model = False
        if not self.toolbox.allowed():
            raise ClaudeError('Ano is paused, locked, or in a private login. No local command ran.')
        try:
            key=' '.join(text.lower().split()).rstrip('.!?')
            from .forex_analysis import pair_from
            fx_pair=pair_from(key) if re.match(r'^(?:check|analy[sz]e|scan|show|ict|smc|forex|setups?)\b',key) else None
            if fx_pair or key in ('forex setups','forex setup','check forex','scan forex','forex check'):
                from .forex_analysis import analyze,describe as describe_fx
                from .oanda_practice import PAIRS as fx_pairs
                pairs=[fx_pair] if fx_pair else list(fx_pairs)
                try:
                    text='\n\n'.join(describe_fx(analyze(p)) for p in pairs)
                except Exception as error:
                    text=f'Forex check unavailable: {type(error).__name__}. No orders were affected.'
                return text+'\n\n· OANDA practice prices · No LLM used'
            if key in ('overnight report','morning report','what happened overnight','what happened last night','overnight trades'):
                from datetime import datetime,timedelta,timezone
                from .night_guard import morning_summary
                from .paths import data_directory
                import sqlite3
                state=data_directory();since=datetime.now(timezone.utc)-timedelta(hours=14)
                lines=['Overnight report (last 14 hours) · paper and practice money']
                try:
                    with sqlite3.connect(state/'strategy-performance.sqlite3') as db:
                        closes=db.execute('SELECT provider,strategy,pair,session,net,closed_at FROM closes WHERE closed_at>=? ORDER BY closed_at',(since.isoformat(),)).fetchall()
                except sqlite3.Error:closes=[]
                from .performance import strategy_label
                for provider,strategy,pair,session,net,_ in closes:
                    lines.append(f'• {pair.replace("_","/")} {strategy_label(provider,strategy)} ({session.replace("_"," ")}): {"+" if float(net)>=0 else "−"}${abs(float(net)):,.2f}')
                if not closes:lines.append('• No trades closed.')
                apps=morning_summary(state,since)
                lines.append('Apps closed to keep the bots running: '+(', '.join(f'{a["closed"]} ({a["freed_mb"]:,} MB)' for a in apps) if apps else 'none'))
                return '\n'.join(lines)+'\n\n· Local evidence · No LLM used'
            if key in ('stop overnight guard','turn off overnight guard','start overnight guard','turn on overnight guard'):
                from .paths import data_directory
                from .night_guard import DEFAULTS
                from .reliability import atomic_json,read_json
                path=data_directory()/'overnight-trading.json';enabled=key.startswith(('start','turn on'))
                atomic_json(path,{**DEFAULTS,**read_json(path),'enabled':enabled})
                return ('Overnight guard ON: from 1:00 to 7:00 AM ET, while you are away (10 minutes without input), other apps may be closed if RAM stays above 90%, so trading continues.'
                        if enabled else 'Overnight guard OFF: Ano will not close any apps.')+'\n\n· Local command · No LLM used'
            if key in ('trading scorecard','bot scorecard','scorecard','compare bots','compare the bots','which bot is better',
                       'stocks vs forex','stocks or forex','strategy scorecard'):
                from .performance import describe_scorecard
                from .paths import data_directory
                return describe_scorecard(data_directory())+'\n\n· Local evidence · No LLM used'
            if key in ('trading performance','strategy performance','forex performance'):
                from .performance import describe
                from .paths import data_directory
                return describe(data_directory(),provider='oanda_practice' if key=='forex performance' else None)+'\n\n· Local evidence · No LLM used'
            if key in ('scorpred accuracy','forecast accuracy'):
                from .report_ledger import ReportLedger
                from .paths import data_directory
                return str(ReportLedger(data_directory()/'report-observations.sqlite3').summary())+'\n\n· Local evidence · No LLM used'
            if ' '.join(text.lower().split()).rstrip('.!?') in ('supervisor status','keepalive status','ano service status'):
                from .paths import data_directory
                from .reliability import read_json
                from .supervisor import observation
                status=observation(data_directory())
                return str(status or 'Supervisor has no saved observation yet.')+'\n\n· Local command · No LLM used'
            scheduled = self.care.command(text) if self.care else None
            if scheduled is None and self.repairs:
                scheduled = self.repairs.command(text)
            if scheduled is None and self.scheduled:
                scheduled = self.scheduled.command(text)
            if scheduled is None and self.monitors:
                scheduled = self.monitors.command(text)
            if scheduled is None and self.challenges:
                scheduled = self.challenges.command(text, local=self.forced_local)
            if scheduled is None and self.goals:
                scheduled = self.goals.command(text, local=self.forced_local)
            if scheduled is None and self.background:
                scheduled = self.background.command(text, local=self.forced_local)
            forex = getattr(self, 'forex_runner', None)
            if scheduled is None and forex:
                scheduled = forex.command(text)
                if ' '.join(text.lower().split()).rstrip('.!?') == 'stop paper trading':
                    forex.command('stop forex trading')
            if scheduled is None and self.ict_watch:
                scheduled = self.ict_watch.command(text)
            if scheduled is not None:
                return scheduled + '\n\n· Local command · No LLM used'
            from .sports_email import email_request
            request = email_request(text, history, today=self.clock().date())
            if request and request.get('question'):
                return request['question'] + '\n\n· Local clarification · No LLM used'
            plan = (Plan('Premium sports email', [('ano_sports_report', request)]) if request else self.plan(text))
        except (ToolError, ValueError) as error:
            raise ClaudeError(str(error)) from None
        if plan is None:
            # Last stop before the chat model: work requests ("fix …", "claude: …") go to a background Claude Code job.
            job = self.claude_jobs.command(text) if self.claude_jobs else None
            return job + '\n\n· Claude Code job' if job else None
        self.last_plan_uses_model = plan.uses_model
        if len(plan.steps) > MAX_STEPS:
            raise ClaudeError('This routine exceeds the eight-action limit. Nothing ran.')
        output = [plan.text] if plan.text else []
        for index, (name, arguments) in enumerate(plan.steps):
            with local_command(self.toolbox):  # the user typed this exact command; no model chose it
                result, _, failed = self.toolbox.run(name, arguments)
            if failed:
                completed = '\n'.join(output)
                raise ClaudeError((completed + '\n\n' if completed else '') +
                                  f'Stopped at step {index + 1}: {result} Remaining steps were not run; no AI fallback was used.')
            if name == 'silver_bullet_setups':
                from .silver_bullet import describe
                result = describe(json.loads(result))
            if name == 'ict_setups':
                report=json.loads(result)
                lines=[f'{report["symbol"]} · ICT/SMC research · {report["date"]} · ANALYSIS ONLY']
                if report.get('stale'):
                    lines.append('WAIT — stale or missing IEX data; not a current entry.')
                for setup in report['setups']:
                    lines.append(f'{setup["direction"]}: {setup["liquidity"]}; FVG {setup["fvg"]}; '
                                 f'entry {setup["entry"]}, stop {setup["stop"]}, target {setup["target"]}; '
                                 f'{setup["status"]} (bar simulation).')
                if not report['setups']:
                    lines.append('WAIT — no confirmed sweep / shift / FVG setup.')
                lines.append('No orders placed; no proven edge. Chart: '+report['chart'])
                result='\n'.join(lines)
            if name == 'smc_analysis':
                from .smc import describe
                result = describe(json.loads(result))
            if name == 'analyze_paper_indicators':
                from .chart_analysis import describe_snapshot
                try:
                    result=describe_snapshot(json.loads(result))
                except (ValueError,TypeError,KeyError):
                    pass  # Formatting must never repeat or mask a completed data read.
            output.append(result)
        footer = 'Local workflow · Draft used the local LLM' if plan.uses_model else 'Local command · No LLM used'
        return '\n\n'.join(output) + '\n\n· ' + footer


class RoutineBrain:
    supports_progress = True
    """Run exact local commands first; only unmatched requests reach the configured model."""
    def __init__(self, routines, fallback=None):
        self.routines, self.fallback = routines, fallback
        self.last_route = 'local_model'

    @property
    def modes(self):
        return self.fallback.modes if self.fallback else []

    @property
    def mode(self):
        return self.fallback.mode if self.fallback else 'commands'

    @mode.setter
    def mode(self, value):
        if self.fallback:
            self.fallback.mode = value

    def reply(self, history, **kwargs):
        self.last_route = 'local_model'
        latest = history[-1] if history else {}
        if kwargs.get('screenshot') is None and latest.get('role') == 'user':
            self.last_route = 'local_command'
            text = self.routines.route_reply(latest.get('body', ''), latest.get('source_chat_id'), history=history)
            if text is not None:
                self.last_route = 'local_model' if getattr(self.routines, 'last_plan_uses_model', False) is True else 'local_command'
                return text
            self.last_route = 'local_model'
        if not self.fallback:
            raise ClaudeError('No AI model is connected. Say “show commands” for commands that work without AI.')
        return self.fallback.reply(history, **kwargs)
