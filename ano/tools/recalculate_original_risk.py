"""Back up and recover original risk from stored fills and historical broker GETs only."""
import json,sqlite3
from datetime import datetime,timezone
from ano.paths import data_directory
from ano.performance import Performance,describe_scorecard,scorecard

def main():
    state=data_directory();store=Performance(state)
    backup=state/'backups'/('performance-before-original-risk-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.sqlite3')
    backup.parent.mkdir(exist_ok=True)
    with store.db() as source,sqlite3.connect(backup) as target:source.backup(target)
    with store.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM closes')]
    results=[]
    for row in rows:
        ev=json.loads(row['evidence']);known=store.recover_original_risk(row['provider'],row['trade_id'],ev)
        results.append({'provider':row['provider'],'trade_id':row['trade_id'],'original_risk_verified':known})
    report=describe_scorecard(state)
    out=state/'original-risk-recalculation.json'
    card=scorecard(state);card['strategies']={' / '.join(k):v for k,v in card['strategies'].items()}
    out.write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'backup':str(backup),
        'trades':results,'scorecard':card,'report':report},default=str,indent=2),encoding='utf-8')
    print(report.encode('ascii','backslashreplace').decode());print('Evidence:',out)

if __name__=='__main__':main()
