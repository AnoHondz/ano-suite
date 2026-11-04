"""One read-only health/readiness snapshot for Ano, ScorPred and paper trading.

No order or email dispatch code lives here. Broker verification uses PaperReader
GET requests only. Local history records observations, never claimed uptime.
"""
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path
from contextlib import closing, contextmanager
import json
import queue
import sqlite3
import threading
import uuid
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler

from .paper_health import read_status
from .paper_reconcile import reconcile


def read_json(path):
    try:
        if path.stat().st_size>2_000_000:return {}
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8');temporary.replace(path)


def age(stamp,now):
    try:
        value=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        return (now-value).total_seconds() if value.tzinfo else None
    except (ValueError,TypeError,AttributeError):return None


class Reliability:
    def __init__(self,state,scor_root=None,getter=None,broker=reconcile,clock=lambda:datetime.now(timezone.utc)):
        self.state=Path(state)
        self.scor_root=Path(scor_root or Path.home()/'Documents/ScorPred-Surface-2026-10-24/ScorPred')
        self.getter=getter or self._get;self.broker=broker;self.clock=clock
        self.directory=self.state/'reliability';self.directory.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY,at TEXT NOT NULL,component TEXT NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS checks_at ON checks(at)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.directory/'history.sqlite3',timeout=5)
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _get(path):
        if path not in ('/api/local-runtime','/health','/api/health'):raise ValueError('Unknown local health endpoint')
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        from urllib.error import HTTPError
        try:response=build_opener(ProxyHandler({}),NoRedirect()).open('http://127.0.0.1:5001'+path,timeout=3)
        except HTTPError as error:
            if path!='/api/health' or error.code!=503:raise
            response=error
        with response:
            raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('Oversized health response')
        return json.loads(raw)

    def snapshot(self,verify_broker=False,record=True):
        now=self.clock();checks=[]
        def add(name,state,detail):checks.append({'component':name,'state':state,'detail':detail})
        if (self.state/'supervisor.health.json').exists():
            from .supervisor import observation
            watched=observation(self.state,now)
            add('Supervisor','healthy' if watched['fresh'] and watched.get('state')=='watching' else 'waiting' if watched['fresh'] else 'blocked',
                'Live owner and recent heartbeat verified.' if watched['fresh'] else 'Saved supervisor record is stale or has no verified live owner; UI recovery is required.')
        try:
            runtime=self.getter('/api/local-runtime')
            if runtime.get('app')!='ScorPred' or Path(runtime.get('root','')).resolve()!=self.scor_root.resolve():
                raise ValueError('Service identity mismatch')
            health=self.getter('/health')
            from .scorpred_health import assess
            try:pipelines=self.getter('/api/health')
            except Exception:pipelines={}
            state,detail=assess(health,pipelines)
            add('ScorPred',state,detail)
        except Exception:
            add('ScorPred','blocked','Local service/identity/database could not be verified. Run ScorPred, then check again.')
        fx=read_json(self.state/'forex-execution.health.json')
        if fx:
            fx_age=age(fx.get('at'),self.clock())
            fx_ok=fx_age is not None and 0<=fx_age<=90 and fx.get('ready') and not fx.get('error')
            add('Forex runner','healthy' if fx_ok and not fx.get('scan_error') else 'blocked',
                ('OANDA practice management verified; entries '+('enabled' if fx.get('enabled') else 'disabled')+
                 '; last scan '+str(fx.get('last_scan'))+'. '+str(fx.get('scan_error') or '')) if fx_ok else
                'Forex heartbeat/management unavailable. '+str(fx.get('error') or 'No fresh management evidence.'))
        paper=read_status(self.state,self.clock);runner=paper.get('runner') or {}
        from .strategy_signals import STRATEGIES,bounds
        modes=runner.get('strategy_execution') or []
        modes=[name for name in modes if name in STRATEGIES] if isinstance(modes,list) else []
        versions=('paper_safety_v3',) if modes else ('paper_safety_v2','paper_safety_v3')
        loop_ok=runner.get('fresh') and runner.get('state')=='running' and runner.get('recovery_ready') and not runner.get('errors') and runner.get('safety_version') in versions
        add('Paper runner','healthy' if loop_ok else 'blocked',
            ('Fresh heartbeat; recovery completed; '+('entries enabled' if runner.get('enabled') or modes else 'entries disabled')) if loop_ok
            else 'Heartbeat stale, recovery incomplete, or management errors present. No new entry should be assumed ready.')
        broker=read_json(self.state/'paper-broker-read.json')
        if verify_broker:
            broker=self.broker(state=self.state,now=now)
            atomic_json(self.state/'paper-broker-read.json',broker)
        broker_age=age(broker.get('at'),self.clock())
        broker_ok=(broker.get('broker_verified') and broker_age is not None and 0<=broker_age<=90 and
                   broker.get('account_active') and not broker.get('trading_blocked') and broker.get('open_orders_complete') and
                   not broker.get('issues'))
        add('Paper broker','healthy' if broker_ok else 'blocked',
            'Fresh GET-only paper account/order reconciliation.' if broker_ok else
            'Fresh complete broker reconciliation required. '+('; '.join(broker.get('issues') or []) or broker.get('notice','No verified broker snapshot.')))
        exposure=(len(broker.get('positions') or [])+int(broker.get('other_position_count') or 0)+int(broker.get('open_order_count') or 0))
        clear=bool(broker_ok and exposure==0 and paper.get('unfinished_local_records')==0)
        add('Entry exposure','healthy' if clear else 'waiting',
            'Account verified empty; no unfinished local trades.' if clear else 'Account exposure/unfinished work must be reconciled before another entry.')
        mail=read_json(self.state/'sports-mail/heartbeat.json');mail_age=age(mail.get('at'),self.clock())
        mail_counts={};mail_error=False
        path=self.state/'sports-mail/receipts.sqlite3'
        if path.is_file():
            try:
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                    mail_counts=dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state'))
            except sqlite3.Error:mail_error=True
        uncertain=sum(mail_counts.get(k,0) for k in ('submitting','delivery_unknown'))
        add('Email','blocked' if mail_error else 'waiting' if uncertain or not path.is_file() else 'healthy',
            'Receipt ledger unreadable; delivery and unresolved submissions cannot be verified.' if mail_error else
            f'{mail_counts.get("sent",0)} SMTP-accepted reports; {uncertain} unresolved submissions. SMTP acceptance is not inbox confirmation.')
        config=read_json(self.state/'sports-mail/config.json')
        if config.get('enabled'):
            mail_ok=mail_age is not None and 0<=mail_age<=120 and mail.get('state') in ('checked','baseline_created')
            add('Phone command worker','healthy' if mail_ok else 'blocked',
                'Recent successful email-command check.' if mail_ok else 'Enabled, but a recent successful worker check is missing. Use enable email commands to resume it.')
        memory_path=self.state/'memory.sqlite3';strategy={};research_health={}
        if memory_path.is_file():
            try:
                with closing(sqlite3.connect(memory_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                    row=db.execute("SELECT value FROM settings WHERE key='trading_model_snapshots'").fetchone()
                    strategy=json.loads(row[0]) if row else {}
                    row=db.execute("SELECT value FROM settings WHERE key='research_watch_health'").fetchone()
                    research_health=json.loads(row[0]) if row else {}
            except (sqlite3.Error,ValueError):pass
        research_age=age(research_health.get('at'),self.clock())
        research_ok=research_age is not None and 0<=research_age<=90 and research_health.get('thread_running') and not research_health.get('error')
        models=[name for name,symbols in (research_health.get('configured') or {}).items() if symbols]
        add('Research watch','healthy' if research_ok else 'waiting',
            ('Thread alive; '+', '.join(models)+'. '+('Research window open.' if research_health.get('window_active') else 'Waiting for weekday 09:25–15:10 New York research window.')) if research_ok
            else 'Fresh research-thread evidence unavailable. '+str(research_health.get('error') or 'Start all trading models to configure the analysis watch.'))
        from . import session_strategies as ss
        now=self.clock()
        local=now.astimezone(ss.NY)
        gap_window=local.weekday()<5 and (9,35)<=(local.hour,local.minute)<(9,37)
        active_windows=[name for name in modes if bounds(name,now)]
        if runner.get('enabled') and gap_window:active_windows.append('gap fill')
        entry_window=bool(active_windows)
        infrastructure=bool(loop_ok and broker_ok and clear)
        readiness={'mode':'ALPACA PAPER ONLY','infrastructure_ready':infrastructure,
                   'entry_window_now':entry_window,'entry_enabled':bool(runner.get('enabled') or modes),
                   'execution_ready_now':False,'live_ready':False,
                   'reason':'Per-entry closed-bar, strategy window, market-data, news, account exposure and shared risk checks must still pass at dispatch.',
                   'authorized_execution':(['gap_fill_v1_2'] if runner.get('enabled') else [])+[STRATEGIES[name] for name in modes],
                   'strategy_modes':modes,'risk_halted':bool(runner.get('risk_halted')),
                   'analysis_only':[name for name in STRATEGIES if name not in modes],
                   'strategy_edge':'Not established by infrastructure checks or bar simulations.'}
        add('Entry timing','waiting' if not entry_window else 'healthy',
            'No enabled strategy entry window is open now (New York time).' if not entry_window else
            'Entry windows open: '+', '.join(active_windows)+'. Signal and dispatch checks remain mandatory.')
        add('Paper execution modes','waiting' if not readiness['entry_enabled'] or runner.get('risk_halted') else 'healthy',
            ('DAILY RISK HALT; no new entries. ' if runner.get('risk_halted') else '')+
            ('Enabled: '+', '.join(readiness['authorized_execution']) if readiness['authorized_execution'] else 'All entries disabled.')+
            ' One shared account owner; simulated funds only.')
        add('Strategy evidence','waiting','Paper forward testing only; no verified live-trading readiness or guaranteed edge.')
        from .report_ledger import ReportLedger
        try:
            forecasts=ReportLedger(self.state/'report-observations.sqlite3').summary()
            forecast_error=read_json(self.state/'report-observations.health.json').get('state')=='error'
        except (OSError,ValueError,sqlite3.Error):
            forecasts={'recorded':0,'settled_scored':0,'brier_score':None};forecast_error=True
        add('Forecast evidence','blocked' if forecast_error else 'healthy' if forecasts['settled_scored'] else 'waiting',
            'Forecast recording unavailable; inspect the local ledger before making performance claims.' if forecast_error else
            f'{forecasts["recorded"]} observations; {forecasts["settled_scored"]} verified settled forecasts scored. '
            'No accuracy claim without verified outcomes.')
        if record:
            with self.db() as db:
                db.executemany('INSERT INTO checks(at,component,state,detail) VALUES (?,?,?,?)',
                               [(now.isoformat(),c['component'],c['state'],c['detail']) for c in checks])
        with self.db() as db:
            first,last,total=db.execute('SELECT min(at),max(at),count(DISTINCT at) FROM checks').fetchone()
            failures=db.execute("SELECT count(*) FROM checks WHERE state='blocked'").fetchone()[0]
        report={'at':now.isoformat(),'checks':checks,'readiness':readiness,'strategies':strategy,'forecasts':forecasts,
                'soak':{'first_observation':first,'last_observation':last,'sample_count':total,'blocked_observations':failures,
                        'target_days':7,'status':'Collecting real observations; no unobserved uptime or week-long pass claimed.'},
                'scope':'This health check uses GET-only broker reads and local diagnostics; it submits no orders or emails. Enabled background strategies operate separately.'}
        if record:atomic_json(self.directory/'latest.json',report)
        return report


def describe(report):
    lines=[f'Ano / ScorPred health · {report["at"]}']
    lines += [f'{c["component"]}: {c["state"].upper()} — {c["detail"]}' for c in report['checks']]
    r=report['readiness']
    lines += [f'Paper infrastructure ready: {r["infrastructure_ready"]}. Entry window now: {r["entry_window_now"]}.',
              r['reason'],'Live trading: disabled. '+r['strategy_edge'],
              f'7-day reliability observations: {report["soak"]["sample_count"]} samples; {report["soak"]["blocked_observations"]} blocked checks.',report['scope']]
    return '\n'.join(lines)


def render(report):
    rows=''.join(f'<tr><td>{escape(c["component"])}</td><td style="color:{"#13714f" if c["state"]=="healthy" else "#a44b20"}">{escape(c["state"].upper())}</td><td>{escape(c["detail"])}</td></tr>' for c in report['checks'])
    r=report['readiness']
    return ('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="background:#f3f5ef;color:#163c36;font-family:M PLUS Rounded 1c;font-size:14px">'
            '<h1>ANO · Reliability &amp; paper readiness</h1><p>'+escape(report['at'])+'</p>'
            '<h2>Paper infrastructure: '+('READY' if r['infrastructure_ready'] else 'BLOCKED')+'</h2>'
            '<p>Live trading is disabled. Infrastructure readiness is not an order instruction or proof of a profitable strategy.</p>'
            '<table cellspacing="0" cellpadding="12" width="100%"><tr><th>Component</th><th>State</th><th>Evidence / next step</th></tr>'+rows+'</table>'
            '<h2>Seven-day reliability log</h2><p>Started: '+escape(str(report['soak']['first_observation'] or 'not observed'))+
            '<br>Latest observation: '+escape(str(report['soak']['last_observation'] or 'not observed'))+
            '<br>'+str(report['soak']['sample_count'])+' observed samples; '+str(report['soak']['blocked_observations'])+
            ' blocked checks recorded.</p><p>'+escape(report['soak']['status'])+'</p><p>'+escape(report['scope'])+'</p></body></html>')


class ReliabilityMonitor:
    def __init__(self,service,allowed=lambda:True,interval=60):
        self.service,self.allowed,self.interval=service,allowed,interval
        self.events=queue.Queue();self.stop=threading.Event();self.last={};self.thread=None

    def sample(self):
        permitted=self.allowed()
        if permitted:
            from .report_ledger import ReportLedger
            try:ReportLedger(self.service.state/'report-observations.sqlite3',clock=self.service.clock).refresh_results()
            except (OSError,ValueError,sqlite3.Error):pass
        report=self.service.snapshot(verify_broker=permitted)
        for c in report['checks']:
            name,state=c['component'],c['state'];previous=self.last.get(name)
            if state!=previous and ((state=='blocked') or (previous=='blocked' and state=='healthy')):
                self.events.put(f'{name}: {state.upper()} — {c["detail"]}')
            self.last[name]=state
        return report

    def start(self):
        def run():
            previous_error=None
            while not self.stop.is_set():
                try:
                    self.sample()
                    atomic_json(self.service.directory/'monitor.health.json',{'at':self.service.clock().isoformat(),'state':'checked'})
                    if previous_error:self.events.put('Reliability observer recovered; health observations resumed.')
                    previous_error=None
                except Exception as error:
                    kind=type(error).__name__
                    try:atomic_json(self.service.directory/'monitor.health.json',{'at':self.service.clock().isoformat(),'state':'error','error_type':kind})
                    except OSError:pass
                    if kind!=previous_error:self.events.put(f'Reliability observer failed ({kind}); fresh health is unverified. Inspect local diagnostics.')
                    previous_error=kind
                self.stop.wait(self.interval)
        self.thread=threading.Thread(target=run,name='ano-reliability',daemon=True);self.thread.start()

    def close(self):self.stop.set()

    def poll(self):
        messages=[]
        while True:
            try:messages.append(self.events.get_nowait())
            except queue.Empty:return messages
