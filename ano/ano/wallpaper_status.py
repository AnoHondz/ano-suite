"""Live, read-only status for the desktop wallpaper: one "station" per part of Ano.

To give a new Ano feature its own station on the wallpaper, write a function that returns
station(...) and add it to STATIONS. The wallpaper spaces any number of stations around its
ring by itself, so nothing in the wallpaper changes. Each builder must stay read-only and fast
(local files and loopback only); if one fails, its station shows "no signal" rather than breaking the rest.
"""
from datetime import datetime, time, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import psutil

NY = ZoneInfo('America/New_York')


def state_dir():
    from .paths import data_directory
    return Path(data_directory())


def read(name):
    try:
        value=json.loads((state_dir() / name).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError, ValueError):return {}


def age_seconds(name):
    try:return (datetime.now(timezone.utc) - datetime.fromisoformat(read(name)['at'])).total_seconds()
    except (KeyError, ValueError, TypeError):return None


def beating(name, limit=120):
    """True when a component wrote its heartbeat recently."""
    age = age_seconds(name)
    return age is not None and 0 <= age <= limit


def query(db, sql, args=()):
    """Read-only SQLite; a missing or busy ledger simply returns nothing."""
    import sqlite3
    try:
        with sqlite3.connect(f'file:{state_dir() / db}?mode=ro', uri=True, timeout=1) as con:return con.execute(sql, args).fetchall()
    except sqlite3.Error:return []


def local_json(url, timeout=2):
    import urllib.request
    from urllib.error import HTTPError
    try:response=urllib.request.urlopen(url, timeout=timeout)
    except HTTPError as error:
        if url!='http://127.0.0.1:5001/api/health' or error.code!=503:raise
        response=error
    with response:return json.loads(response.read().decode('utf-8'))


def money(value):
    return ('+' if value >= 0 else '−') + f'${abs(value):,.2f}'


def station(id, name, role, state, *, headline='', details=(), say=(), active=False, repair='', flags=()):
    """One wallpaper station. state: healthy | attention | down | stale. active: something live (e.g. a trade).
    repair: what Ano's Supervisor is actually doing about it, if anything (the wallpaper only shows this).
    flags: (what, steps) checks that need you; a healthy station with flags becomes 'attention' so the crawler visits."""
    flags = [{'what': what, 'steps': list(steps)} for what, steps in flags]
    if flags and state == 'healthy':
        state = 'attention'
    return {'id': id, 'name': name, 'role': role, 'state': state, 'active': active, 'headline': headline,
            'details': [[k, str(v)] for k, v in details], 'say': list(say), 'repair': repair or (flags[0]['what'] if flags else ''),
            'flags': flags}


def supervisor_repair(*components):
    """The Supervisor's latest word on a component, when it is restarting or flagging it; '' when all is well."""
    if not beating('supervisor.health.json', 90):
        return ''
    checks = read('supervisor.health.json').get('checks') or {}
    for name in components:
        value = str(checks.get(name, ''))
        if value.startswith('starting'):
            return f'supervisor restarting {name.lower()}'
        if value.startswith(('attention','degraded','blocked')):
            return f'supervisor: {value.split(":", 1)[-1].strip() or "watching"}'.lower()
    return ''


def today_start():
    return datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def closes(provider):
    from .performance import strategy_label
    rows = query('strategy-performance.sqlite3', 'SELECT strategy, pair, net, closed_at FROM closes WHERE provider=? ORDER BY closed_at DESC', (provider,))
    last = None
    if rows:
        strategy, pair, net, _ = rows[0]
        last = f'{pair.replace("_", "/")} {strategy_label(provider, strategy)} {money(float(net))}'
    today = [float(net) for _, _, net, at in rows if (at or '') >= today_start()]
    return last, today


DAILY_STOP_PCT = 10   # the bots' daily loss stop (ano/trading_risk.py DAILY_LOSS_LIMIT), set by the user 2026-11-02
MAX_OPEN_FOREX = 2    # forex trades allowed at once, on different pairs
MAX_FILLS_DAY = 4     # forex entries a day (ano/forex_order_policy.py), set by the user 2026-11-03
PAUSED_STOCK_STRATEGIES = ('ict', 'amd', 'opening range')  # ano/strategy_execution.py, paused by the user 2026-11-03


def trading_flags(bot, today):
    """Flags for one paper bot: its daily loss stop, the two-losses-in-a-row pause and broker checks.
    Returns (flags, today's P/L as % of the day's start, or None when that start is not known)."""
    flags, base = [], None
    if bot == 'forex':
        rows = query('forex-execution.sqlite3', 'SELECT nav, halt FROM sessions ORDER BY day DESC LIMIT 1')
        if rows:
            base = float(rows[0][0])
            if rows[0][1]:
                flags.append(('daily loss stop hit · no new forex trades today', [f'stop is −{DAILY_STOP_PCT}% of the day start']))
    else:
        for issue in read('paper-broker-read.json').get('issues') or []:
            flags.append(('broker check · ' + str(issue)[:90].lower(), ['Ano exits a position whose stop and target are not working']))
    pct = sum(today) / base * 100 if base else None
    if pct is not None and pct <= -DAILY_STOP_PCT / 2 and not any('stop hit' in what for what, _ in flags):
        flags.append((f'down {abs(pct):.1f}% today · stop at −{DAILY_STOP_PCT}%', [f'today {money(sum(today))}']))
    # The two-loss pause counts today's closes only; for forex, the user's fresh start forgives earlier ones.
    since = float(read('forex-fresh-start.json').get('since', 0)) if bot == 'forex' else 0.0
    provider = 'oanda_practice' if bot == 'forex' else 'alpaca_paper'
    recent = []
    for net, at in query('strategy-performance.sqlite3', 'SELECT net, closed_at FROM closes WHERE provider=? AND closed_at>=? '
                         'ORDER BY closed_at DESC LIMIT 2', (provider, today_start())):
        try:
            if datetime.fromisoformat(at).timestamp() >= since:recent.append(float(net))
        except (TypeError, ValueError):continue
    if len(recent) == 2 and all(n < 0 for n in recent):
        flags.append(('two losses in a row · new entries paused for today', [f'last two {money(recent[0])}, {money(recent[1])}',
                                                                                  'resets on the next trading day']))
    return flags, pct


def percent(value):
    return '' if value is None else f' · {value:+.1f}%'


# ── Stations ──────────────────────────────────────────────────────────────────────────────────

def forex():
    health = read('forex-execution.health.json')
    alive = beating('forex-execution.health.json')
    sessions = sorted({n for o in (health.get('observations') or {}).values() for n in (o.get('sessions') or {})})
    session = ' + '.join(s.replace('_', ' ').title() for s in sessions) or 'between sessions'
    open_trades = []
    for (plan,) in query('forex-execution.sqlite3', "SELECT plan FROM orders WHERE state IN ('filled','submitting','uncertain','pending_verified')"):
        try:p = json.loads(plan);open_trades.append(f'{p["pair"].replace("_", "/")} {p["side"]} · {p["name"]}')
        except (ValueError, KeyError):open_trades.append('forex trade')
    last, today = closes('oanda_practice')
    auto = (health.get('native_execution') or {}).get('enabled')
    state = 'stale' if not alive else 'attention' if health.get('error') or health.get('scan_error') or not health.get('ready') else 'healthy'
    flags, day_pct = trading_flags('forex', today) if alive else ([], None)
    if alive and health.get('ready') and not auto:
        flags.append(('auto-trading paused · engine changed, needs your approval',
                      ['SMC / ICT entries are off until approved', 'say "approve" to renew it']))
    return station('forex', 'FOREX', 'OANDA practice bot', state, active=bool(open_trades),
        headline=(open_trades[0] if open_trades else session),
        details=[('Session', session), ('Open trades', f'{len(open_trades)} of {MAX_OPEN_FOREX}' + (f' · {open_trades[0]}' if open_trades else '')),
                 ('Last trade', last or 'none yet'), ('Today', f'{money(sum(today))} · {len(today)} trades{percent(day_pct)}'),
                 ('Daily limits', f'−{DAILY_STOP_PCT:g}% stop · {MAX_FILLS_DAY} trades'), ('Auto-trading', 'on, approved' if auto else 'watching only')],
        say=['check usdjpy', 'forex setups', 'scorecard', 'overnight report'], repair=supervisor_repair('Forex manager', 'Ano'), flags=flags)


def stocks():
    health = read('paper-execution.health.json')
    alive = beating('paper-execution.health.json')
    now = datetime.now(NY)
    market = 'open' if now.weekday() < 5 and time(9, 30) <= now.time() < time(16) else 'closed'
    open_trades = [f'{s} {side} · {(st or "").split("_")[0]}' for s, st, side in
                   query('paper-execution.sqlite3', "SELECT symbol,strategy,side FROM gap_trades WHERE state IN ('filled','partially_filled','acknowledged','exiting')")]
    last, today = closes('alpaca_paper')
    strategies = health.get('strategy_execution') or []
    state = 'stale' if not alive else 'attention' if health.get('errors') or health.get('risk_halted') else 'healthy'
    flags, day_pct = trading_flags('stocks', today) if alive else ([], None)
    if alive and health.get('risk_halted'):
        flags.insert(0, ('daily loss stop hit · no new stock trades today', [f'stop is −{DAILY_STOP_PCT:g}% of the day start']))
    return station('stocks', 'STOCKS', 'Alpaca paper bot · QQQ, DIA', state, active=bool(open_trades),
        headline=(open_trades[0] if open_trades else f'market {market}'),
        details=[('Market', f'New York {market}'), ('Open trade', open_trades[0] if open_trades else 'none'),
                 ('Strategies on', ', '.join(['gap fill'] + [s for s in strategies if s not in PAUSED_STOCK_STRATEGIES])),
                 ('Last trade', last or 'none yet'), ('Today', f'{money(sum(today))} · {len(today)} trades{percent(day_pct)}'),
                 ('Daily stop', f'−{DAILY_STOP_PCT:g}% of the day start')],
        say=['paper trading status', 'scorecard', 'ict setups for qqq'], repair=supervisor_repair('ETF manager', 'Ano'), flags=flags)


def scorpred():
    detail='Pipeline health unverified.'
    try:
        health = local_json('http://127.0.0.1:5001/health')
        from .scorpred_health import assess
        try:pipelines=local_json('http://127.0.0.1:5001/api/health')
        except (OSError,ValueError):pipelines={}
        readiness,detail=assess(health,pipelines)
        state = 'healthy' if readiness=='healthy' else 'attention'
    except OSError:
        state, health = 'down', {}
    next_email, credits_text, parlays_text, flags = '—', '—', '—', []
    if state=='attention':flags.append(('sports data needs attention',[detail,'No automatic restart for a degraded data feed.']))
    if state != 'down':
        try:
            digest = local_json('http://127.0.0.1:5001/api/local-runtime').get('daily_digest') or {}
            when = datetime.fromisoformat(digest['next_run']).astimezone(NY)
            next_email = when.strftime('%a %I:%M %p ET').replace(' 0', ' ')
        except (OSError, ValueError, KeyError, TypeError):pass
        try:
            credits = local_json('http://127.0.0.1:5001/api/odds-credits')
            used, cap = int(credits['used']), int(credits['cap'])
            credits_text = f'{used} of {cap} this month'
            parlays_text = ' · '.join(f'{s.upper()} {st or "pending"}' for s, st in (credits.get('parlays_today') or {}).items()) or 'none due'
            if used >= cap:
                flags.append(('odds credits used up · picks and parlays pause until they renew',
                              [credits_text, 'the email still sends, without fresh picks', 'a paid Odds API plan lifts this']))
            elif used >= cap * 0.9:
                flags.append((f'odds credits low · {cap - used} left this month', [credits_text, 'a morning uses about 15–20']))
        except (OSError, ValueError, KeyError, TypeError):pass
    return station('scorpred', 'SCORPRED', 'Sports predictions · NBA, NFL, soccer', state,
        headline='running' if state == 'healthy' else 'needs attention' if state == 'attention' else 'offline',
        details=[('Server', {'healthy': 'running', 'attention': 'running, data degraded', 'down': 'offline'}[state]),
                 ('Daily email', next_email), ('Odds credits', credits_text), ('Parlays today', parlays_text),
                 ('Data', detail)],
        say=['nfl player props', 'nba player props', 'show nfl props', 'scorpred status'], repair=supervisor_repair('ScorPred'), flags=flags)


def mail():
    config = read('sports-mail/config.json')
    enabled = config.get('enabled') is True
    alive = beating('sports-mail/heartbeat.json')
    jobs = query('sports-mail/receipts.sqlite3', 'SELECT command, state, at FROM jobs ORDER BY at DESC LIMIT 1')
    last = f'{jobs[0][0]} · {jobs[0][1]}' if jobs else 'none yet'
    state = ('healthy' if alive else 'stale') if enabled else 'healthy'
    return station('mail', 'MAIL', 'Email reports and phone commands', state,
        headline='listening' if enabled and alive else 'off' if not enabled else 'quiet',
        details=[('Phone commands', 'on' if enabled else 'off'), ('Worker', 'running' if alive else 'not seen'),
                 ('Last email', last)],
        say=['email command status', 'email me nba picks', 'nfl player props'], repair=supervisor_repair('Email worker', 'Email heartbeat'))


def assistant():
    ui = beating('ano-ui.health.json')
    try:
        local_json('http://127.0.0.1:11434/api/version');ai = 'ready'
    except (OSError, ValueError):ai = 'offline'
    memory = (state_dir() / 'memory.sqlite3').is_file()
    state = 'down' if not ui else 'attention' if ai != 'ready' or not memory else 'healthy'
    return station('assistant', 'ASSISTANT', 'Ano chat, local AI and memory', state,
        headline='ready' if state == 'healthy' else 'local AI offline' if ai != 'ready' else 'window not responding',
        details=[('Ano window', 'running' if ui else 'not responding'), ('Local AI', ai), ('Memory', 'available' if memory else 'missing')],
        say=['what can you do', 'local: summarise my day', 'paid usage'], repair=supervisor_repair('Ano', 'Ano UI', 'Ollama'))


def system():
    sup = read('supervisor.health.json')
    watching = beating('supervisor.health.json', 90) and sup.get('state') == 'watching'
    ram = psutil.virtual_memory().percent
    guard = {**{'enabled': False}, **read('overnight-trading.json')}
    battery = psutil.sensors_battery()
    power = 'plugged in' if battery is None or battery.power_plugged else f'battery {battery.percent:.0f}%'
    state = 'down' if not watching else 'attention' if ram >= 92 or (battery and not battery.power_plugged and battery.percent < 25) else 'healthy'
    return station('system', 'SYSTEM', 'Supervisor, memory and power', state,
        headline=f'RAM {ram:.0f}% · {power}',
        details=[('Supervisor', 'watching' if watching else 'not running'), ('RAM', f'{ram:.0f}%'), ('Power', power),
                 ('Overnight guard', f'on {guard.get("start", "01:00")}–{guard.get("end", "07:00")} ET' if guard['enabled'] else 'off')],
        say=['overnight report', 'health', 'supervisor status'], repair='' if watching else 'supervisor not running: restart Ano or sign in again')


# Order is the order around the ring, clockwise from the top. Add new stations here.
def work():
    rows=query('jobs.sqlite3','SELECT state,count(*) FROM work_jobs GROUP BY state')
    counts=dict(rows);active=sum(counts.get(k,0) for k in ('running','queued','cancelling'))
    return station('work','WORK QUEUE','Persistent jobs and resumable progress','healthy' if rows else 'stale',
        headline=f'{active} active · {counts.get("paused",0)} paused',active=active>0,
        details=[('Running',counts.get('running',0)),('Queued',counts.get('queued',0)),
                 ('Paused',counts.get('paused',0)),('Answered',counts.get('answered',0)),
                 ('Historical failures',counts.get('failed',0))],say=['show background tasks','show commands'])

def models():
    try:
        rows=local_json('http://127.0.0.1:11434/api/ps').get('models',[])
        details=[(r.get('name','unknown'),f'{r.get("size",0)/1024**3:.2f} GB resident') for r in rows]
        return station('models','LOCAL MODELS','Loaded local inference models','healthy',headline=f'{len(rows)} loaded',
            details=details or [('Residency','Idle; models load when requested')],say=['show local AI status','ram status'])
    except (OSError,ValueError):
        return station('models','LOCAL MODELS','Loaded local inference models','stale',headline='Server not verified',say=['show local AI status'])

def memory_manager():
    h=read('memory-manager.health.json');fresh=beating('memory-manager.health.json')
    return station('memory_manager','MEMORY GUARD','Automatic local-model memory management','healthy' if fresh else 'stale',
        headline=str(h.get('state','No heartbeat')).replace('_',' '),
        details=[('RAM',f'{h.get("ram_percent","?")}%'),('Available',f'{h.get("available_bytes",0)/1024**3:.1f} GB'),
                 ('Last unloaded',', '.join(map(str,h.get('last_unloaded') or [])) or 'none this run'),
                 ('Scope','Configured local models only')],say=['ram status'])

def risk():
    from .performance import r_multiple
    rows=query('strategy-performance.sqlite3','SELECT provider,trade_id,net,gross,evidence FROM closes')
    snapshots={(p,t):json.loads(e) for p,t,e in query('strategy-performance.sqlite3','SELECT provider,trade_id,evidence FROM original_risks')}
    details=[];known=0
    for provider,label in [('oanda_practice','Forex'),('alpaca_paper','Stocks')]:
        group=[dict(zip(('provider','trade_id','net','gross','evidence'),r)) for r in rows if r[0]==provider]
        rs=[v for v in (r_multiple(r,snapshots.get((provider,r['trade_id']))) for r in group) if v is not None]
        known+=len(rs)
        details.append((label,f'{len(group)} recorded closes · '+(f'{sum(rs):+.2f}R' if rs else 'R unknown')))
    details += [('Original risk',f'{known}/{len(rows)} verified'),('R scope','Gross original-risk R; all recorded closes'),
                ('Evidence','Paper/practice observations; no proven edge')]
    return station('risk','RISK LEDGER','Immutable original-stop evidence','healthy' if rows and known==len(rows) else 'attention' if rows else 'stale',
        headline=f'{known}/{len(rows)} original risks verified' if rows else 'No closed-trade evidence',details=details,
        say=['bot scorecard','trading performance'])

STATIONS = [assistant, scorpred, forex, stocks, system, mail, models, memory_manager, work, risk]


# ── Repairs: what Ano is actually doing about problems, so the crawler can go and help ───────────
# The crawler never acts by itself: it walks to where Ano's Supervisor or overnight guard is working
# and shows those real steps. Each repair names the station it belongs to.
COMPONENT_STATION = {'Ano': 'assistant', 'Ano UI': 'assistant', 'Ollama': 'assistant', 'ScorPred': 'scorpred',
                     'Email worker': 'mail', 'Email heartbeat': 'mail', 'ETF manager': 'stocks', 'Forex manager': 'forex'}
POLICY_STATION = {'ano': 'assistant', 'ollama': 'assistant', 'scorpred': 'scorpred', 'sports-mail': 'mail', 'supervisor': 'system'}


def repair(station_id, by, what, status, at=None, steps=()):
    """status: working (Ano is fixing it now) | done (fixed recently) | needs-you (no safe automatic fix)."""
    return {'station': station_id, 'by': by, 'what': what, 'status': status, 'at': at, 'steps': list(steps)}


def biggest_apps(count=3):
    groups = {}
    for p in psutil.process_iter(['name', 'memory_info']):
        try:
            name = (p.info['name'] or '').lower().removesuffix('.exe')
            if name and p.info['memory_info']:groups[name] = groups.get(name, 0) + p.info['memory_info'].rss
        except psutil.Error:continue
    skip = {'memory compression', 'svchost', 'system', 'registry'}
    return [(APP_NAMES.get(n, n), b) for n, b in sorted(groups.items(), key=lambda kv: -kv[1]) if n not in skip][:count]


APP_NAMES = {'code': 'VS Code', 'claude': 'Claude', 'python': 'Python (Ano, ScorPred)', 'pythonw': 'Ano', 'msedge': 'Edge',
             'msedgewebview2': 'Edge WebView', 'chrome': 'Chrome', 'ollama': 'Local AI', 'ollama_llama_server': 'Local AI',
             'lively': 'Wallpaper', 'node': 'Node', 'explorer': 'File Explorer', 'tradingview': 'TradingView'}


def repairs(now=None):
    now = now or datetime.now(timezone.utc)
    found = []
    if beating('supervisor.health.json', 90):
        for component, value in (read('supervisor.health.json').get('checks') or {}).items():
            value, where = str(value), COMPONENT_STATION.get(component)
            if not where:continue
            if value.startswith('starting'):
                found.append(repair(where, 'supervisor', f'restarting {component.lower()}', 'working',
                                    steps=['noticed it stopped', f'starting {component.lower()} again', 'waiting for its heartbeat']))
            elif value.startswith(('attention','degraded','blocked')):
                found.append(repair(where, 'supervisor', value.split(':', 1)[-1].strip().lower() or 'watching', 'needs-you'))
    # restarts the Supervisor finished in the last 10 minutes
    for name, times in read('supervisor.policy.json').items():
        where = POLICY_STATION.get(name)
        recent = [t for t in times if isinstance(t, (int, float)) and 0 <= now.timestamp() - t <= 600]
        component={'ano':'Ano UI','ollama':'Ollama','scorpred':'ScorPred','sports-mail':'Email heartbeat','supervisor':'Supervisor'}.get(name)
        observed=read('supervisor.health.json').get('checks',{}).get(component)
        verified=beating('supervisor.health.json',90) and observed in ('healthy','fresh','running')
        if where and recent and verified and not any(r['station'] == where and r['status'] == 'working' for r in found):
            at = datetime.fromtimestamp(recent[-1], timezone.utc)
            found.append(repair(where, 'supervisor', f'{name} recovery check passed after {at.astimezone(NY):%I:%M %p}'.replace(' 0', ' ').lower(), 'done', at.isoformat()))
    # apps the overnight guard closed in the last 15 minutes to free memory
    try:rows = [json.loads(line) for line in (state_dir() / 'overnight-trading.log.jsonl').read_text(encoding='utf-8').splitlines()[-20:] if line.strip()]
    except (OSError, ValueError):rows = []
    for row in rows:
        try:at = datetime.fromisoformat(row['at'])
        except (KeyError, ValueError):continue
        if 0 <= (now - at).total_seconds() <= 900:
            found.append(repair('system', 'overnight guard', f'closed {row.get("closed")} · freed {row.get("freed_mb", 0) / 1024:.1f} gb', 'done', row['at']))
    # Ano's own repairs (ano/repairs.py): the fixed ScorPred recovery and fixes answering the crawler's flags
    care = read('repairs.json')
    if care.get('pending_id'):
        found.append(repair('scorpred', 'ano', 'restarting scorpred', 'working', steps=['three checks confirmed it stopped', 'starting it again', 'checking it answers']))
    for row in care.get('crawler') or []:
        try:at = datetime.fromisoformat(row['at'])
        except (KeyError, TypeError, ValueError):continue
        if isinstance(row, dict) and 0 <= (now - at).total_seconds() <= 600:
            status = {'working': 'working', 'done': 'done'}.get(row.get('state'), 'needs-you')
            what = row.get('what', 'repair') + (f' · {row["detail"]}' if row.get('detail') else '')
            found.append(repair(row.get('station', 'system'), 'ano', what, status, row['at']))
    # memory pressure with nothing left for Ano to free: say what is using it
    ram = psutil.virtual_memory().percent
    if ram >= 92:
        apps = biggest_apps()
        found.append(repair('system', 'ano', f'ram {ram:.0f}% · close an unused app', 'needs-you',
                            steps=[f'using ram: {n} {b / 1024 ** 3:.1f} gb' for n, b in apps]))
    return found


def sample():
    stations = []
    for build in STATIONS:
        try:
            value=build()
            if (not isinstance(value,dict) or value.get('state') not in ('healthy','attention','down','stale')
                or any(not isinstance(value.get(k),str) for k in ('id','name','role','headline'))
                or not isinstance(value.get('details'),list) or any(not isinstance(r,(list,tuple)) or len(r)!=2 for r in value['details'])
                or not isinstance(value.get('say'),list) or not isinstance(value.get('flags',[]),list)
                or any(not isinstance(f,dict) or not isinstance(f.get('what'),str) or not isinstance(f.get('steps'),list) for f in value.get('flags',[]))):
                raise ValueError('Invalid station observation')
            stations.append(value)
        except Exception as error:
            stations.append(station(build.__name__, build.__name__.upper(), 'status unavailable', 'stale',
                                    headline='no signal', details=[('Error', type(error).__name__)]))
    try:found = repairs()
    except Exception:found = []
    # Each station flag becomes a 'needs you' job, so the crawler walks there and reads out its steps.
    for s in stations:
        for flag in s.get('flags') or []:
            found.append(repair(s['id'], 'ano check', flag['what'], 'needs-you', steps=flag['steps']))
    policy=read('repairs.json');integrity=read('wallpaper-integrity.health.json')
    working=[r for r in found if r.get('status')=='working']
    issues=[s['id'] for s in stations if s['state']!='healthy']
    defense={'mode':'recovering' if working else 'attention' if issues or integrity.get('state')=='attention' else 'monitoring',
             'automatic_repairs':policy.get('enabled') is True,'supervisor_verified':beating('supervisor.health.json',90),
             'asset_integrity':integrity.get('state','unverified'),'asset_detail':integrity.get('detail','Integrity not checked yet.'),
             'issues':issues,'active_repairs':[r['what'] for r in working],
             'scope':'Verified wallpaper assets; stopped-service recovery; idle local models. Unknown corruption needs inspection.'}
    return {'at': datetime.now(timezone.utc).isoformat(), 'stations': stations, 'repairs': found,'defense':defense}
