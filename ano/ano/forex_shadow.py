"""Durable native FX observations; no broker writes or automatic promotion."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import hashlib,json,sqlite3,zlib
from .reliability import atomic_json
from . import forex_v2


def fingerprint():
    root=Path(__file__).parent
    names=('forex_v2.py','forex_order_policy.py','forex_replay.py','forex_v2_execution.py',
           'smc.py','trading_risk.py','forex_sessions.py','oanda_practice.py','forex_runner.py')
    return hashlib.sha256(b''.join((root/n).read_bytes() for n in names)).hexdigest()


class ShadowStore:
    def __init__(self,state):
        self.state=Path(state);self.path=self.state/'forex-native-v2.sqlite3'
        with closing(sqlite3.connect(self.path,timeout=10)) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS candles(
                pair TEXT,frame TEXT,t INTEGER,sha TEXT,received_at TEXT,payload BLOB,
                PRIMARY KEY(pair,frame,t,sha));
                CREATE TABLE IF NOT EXISTS observations(
                id INTEGER PRIMARY KEY,pair TEXT,session TEXT,at TEXT,engine TEXT,sha TEXT,payload BLOB,
                UNIQUE(pair,session,at,engine));
                CREATE TABLE IF NOT EXISTS replay_runs(
                id INTEGER PRIMARY KEY,at TEXT,engine TEXT,payload TEXT);
                CREATE TRIGGER IF NOT EXISTS candle_no_update BEFORE UPDATE ON candles BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS candle_no_delete BEFORE DELETE ON candles BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS observation_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS observation_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'immutable'); END;''')

    def observe(self,pair,rows,higher,now,session):
        report=forex_v2.scan(pair,rows,higher,now,session)
        enabled=execution_settings(self.state)['enabled']
        report['mode']='explicitly enabled practice experiment' if enabled else 'shadow; no automatic promotion'
        received=datetime.now(timezone.utc).isoformat();engine=fingerprint()
        with closing(sqlite3.connect(self.path,timeout=10)) as db, db:
            for frame,bars in [('M1',rows),*higher.items()]:
                for bar in bars:
                    # Complete bars only; no stale future HTF bar enters evidence.
                    interval={'M1':60,'daily':86400,'4h':14400,'1h':3600}[frame]
                    if bar['t']+interval>now.timestamp():continue
                    body=json.dumps(bar,sort_keys=True,allow_nan=False).encode();sha=hashlib.sha256(body).hexdigest()
                    db.execute('INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?)',
                        (pair,frame,bar['t'],sha,received,zlib.compress(body)))
            body=json.dumps(report,sort_keys=True,allow_nan=False).encode()
            db.execute('INSERT OR IGNORE INTO observations(pair,session,at,engine,sha,payload) VALUES (?,?,?,?,?,?)',
                (pair,session,now.isoformat(),engine,hashlib.sha256(body).hexdigest(),zlib.compress(body)))
        atomic_json(self.state/'forex-native-v2.health.json',{'at':received,'engine':engine,'version':forex_v2.VERSION,
            'mode':'oanda_practice_auto' if enabled else 'shadow_only','automatic_execution':enabled,'pair':pair,'session':session,
            'signals':len(report['plans']),'diagnostics':report['diagnostics'][-20:],
            'validation':'collecting forward observations; no profitability evidence established',
            'historical_input_notice':'First received now; downloaded history is not proof of contemporaneous availability.'})
        return report


def execution_settings(state):
    from .reliability import read_json
    saved=read_json(Path(state)/'forex-native-execution.json')
    enabled=(saved.get('enabled') is True and saved.get('provider')=='oanda_practice'
             and saved.get('engine')==fingerprint() and saved.get('version')==forex_v2.VERSION
             and saved.get('authorization')=='explicit_user_practice_auto')
    return {'enabled':enabled,'provider':'oanda_practice','engine':fingerprint(),
            'reason':'explicit practice opt-in for current source' if enabled else 'no matching explicit practice opt-in'}


def validation_status(state):
    settings=execution_settings(state)
    return {'version':forex_v2.VERSION,'engine':fingerprint(),'execution_enabled':settings['enabled'],
            'state':'unvalidated practice experiment' if settings['enabled'] else 'shadow collecting',
            'requirements':{'chronological_holdout':True,'minimum_closed_trades':200,
                'minimum_session_dates':30,'net_of_bid_ask_and_slippage':True,
                'positive_95pct_lower_mean_net_r':True,'separate_strategy_pair_session_results':True}}
