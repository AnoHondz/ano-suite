"""Read-only paper runner diagnostics. Never connects to an order endpoint."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

STATE=Path.home()/'Documents'/'AnoWorkspace'/'.ano-state'


def read_status(state=STATE, now=None):
    state=Path(state)
    clock=now if callable(now) else lambda:now or datetime.now(timezone.utc)
    result={'account_mode':'ALPACA PAPER / SIMULATED','broker_verified':False,
            'scope':'Local runner/ledger only; broker fills, exposure and P&L are not verified by this report.'}
    try:
        health=json.loads((state/'paper-execution.health.json').read_text(encoding='utf-8'))
        age=(clock()-datetime.fromisoformat(health['at'])).total_seconds()
        result['runner']={**health,'age_seconds':round(age),'fresh':0<=age<=90}
    except (OSError,ValueError,KeyError,TypeError):result['runner']={'fresh':False,'state':'unavailable'}
    path=state/'paper-execution.sqlite3'
    if not path.is_file():result['ledger']='unavailable';return result
    day=clock().astimezone(ZoneInfo('America/New_York')).date().isoformat()
    try:
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            result['today']=day
            result['decisions']=[{'symbol':r[0]} for r in db.execute('SELECT symbol FROM gap_decisions WHERE day=?',(day,))]
            columns={r[1] for r in db.execute('PRAGMA table_info(gap_trades)')}
            fields=['symbol','side','state','filled_qty']+(['strategy'] if 'strategy' in columns else [])
            result['trades']=[dict(zip(fields,r)) for r in db.execute('SELECT '+','.join(fields)+' FROM gap_trades WHERE day=?',(day,))]
            result['unfinished_local_records']=db.execute("SELECT count(*) FROM gap_trades WHERE state IN ('submitting','uncertain','acknowledged','partially_filled','filled','exiting','exit_uncertain')").fetchone()[0]
        finally:db.close()
    except sqlite3.Error:result['ledger']='read unavailable'
    return result


if __name__=='__main__':print(json.dumps(read_status()))
