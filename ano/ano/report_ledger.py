"""Immutable pre-event forecast observations shared across report channels.

Records all supported winner forecasts, including SKIP, to avoid evaluating only
selected successes. Settlement requires a separately verified final result;
neither an LLM nor a displayed prediction can certify an outcome.
"""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib
import json
import math
import sqlite3


def timestamp(value):
    try:
        at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return at.astimezone(timezone.utc) if at.tzinfo else None
    except (ValueError,TypeError):return None


class ReportRecorder:
    """Defer database opening so diagnostics cannot prevent building a report."""
    def __init__(self,path):self.path=Path(path)
    def record_snapshot(self,*args,**kwargs):return ReportLedger(self.path).record_snapshot(*args,**kwargs)


class ReportLedger:
    def __init__(self,path,clock=lambda:datetime.now(timezone.utc)):
        self.path=Path(path);self.clock=clock;self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS forecasts (
                id TEXT PRIMARY KEY,sport TEXT NOT NULL,event_id TEXT NOT NULL,model TEXT NOT NULL,
                selection TEXT NOT NULL,probability REAL NOT NULL,baseline REAL NOT NULL,
                kickoff TEXT NOT NULL,source_observed TEXT,first_seen TEXT NOT NULL,
                eligible INTEGER NOT NULL,exclusion TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS report_visits (
                forecast_id TEXT NOT NULL,channel TEXT NOT NULL,first_seen TEXT NOT NULL,
                PRIMARY KEY(forecast_id,channel));
                CREATE TABLE IF NOT EXISTS results (
                sport TEXT NOT NULL,event_id TEXT NOT NULL,winner TEXT NOT NULL,
                source TEXT NOT NULL,finished_at TEXT NOT NULL,recorded_at TEXT NOT NULL,
                PRIMARY KEY(sport,event_id));
                CREATE TABLE IF NOT EXISTS settlement_attempts (
                sport TEXT NOT NULL,event_id TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(sport,event_id));''')
            db.execute('CREATE INDEX IF NOT EXISTS forecast_event_model ON forecasts(sport,event_id,model,eligible,first_seen)')
            db.execute('CREATE INDEX IF NOT EXISTS forecast_due ON forecasts(eligible,kickoff)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()

    def record_snapshot(self,sport,payload,channel='report'):
        if sport not in ('nba','soccer','nfl'):raise ValueError('Unsupported forecast sport')
        if not isinstance(payload,dict) or not isinstance(payload.get('slate') or [],list):raise ValueError('Invalid forecast slate')
        now=self.clock();count=0
        with self.db() as db:
            for row in payload.get('slate') or []:
                if not isinstance(row,dict):continue
                research=row.get('researchForecast') or {}
                probability=row.get('winProbability') if row.get('winProbability') is not None else research.get('winProbability')
                if type(probability) not in (int,float) or not math.isfinite(probability) or not 0<=probability<=100:
                    continue  # NFL scenario shares and rule confidence are not win probabilities.
                selection=str(research.get('side') if row.get('side')=='Unavailable' else row.get('side') or '')
                teams=[str(row.get('teamA') or ''),str(row.get('teamB') or '')]
                if not selection or selection not in teams+(['Draw','draw'] if sport=='soccer' else []):continue
                if selection=='draw':selection='Draw'
                event=str(row.get('matchId') or '')
                kickoff=timestamp(row.get('matchDate'))
                fresh=row.get('dataFreshness') or {}
                if not isinstance(fresh,dict):fresh={}
                observed=timestamp(fresh.get('observed_at'));expires=timestamp(fresh.get('expires_at'))
                reason=('missing event/time' if not event or not kickoff else
                        'first observed after kickoff' if now>=kickoff else
                        'source freshness unavailable/stale/future' if not observed or not expires or not observed<=now<expires or observed>=kickoff or fresh.get('state','fresh')!='fresh' else '')
                evidence={'event':event,'sport':sport,'teams':teams,'league':row.get('leagueId'),'selection':selection,'probability':probability/100,
                          'source':fresh,'model':row.get('modelSource') or research.get('modelSource') or row.get('model') or row.get('method') or 'scorpred-api-version-unspecified',
                          'data_quality':row.get('dataQuality') or {'state':'unverified'},
                          'kickoff':row.get('matchDate'),'action':row.get('action'),'reason':row.get('reason')}
                encoded=json.dumps(evidence,sort_keys=True,separators=(',',':'),ensure_ascii=False)
                identity=hashlib.sha256(encoded.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identity,sport,event,str(evidence['model']),selection,probability/100,1/3 if sport=='soccer' else .5,
                     kickoff.isoformat() if kickoff else '',observed.isoformat() if observed else None,now.isoformat(),not reason,reason,encoded))
                db.execute('INSERT OR IGNORE INTO report_visits VALUES (?,?,?)',(identity,channel,now.isoformat()))
                count+=1
        return count

    def settle(self,sport,event_id,winner,source,finished_at,*,verified=False,status=None):
        at=timestamp(finished_at);now=self.clock()
        if not verified or status!='final' or not at or at>now or not isinstance(source,str) or not source.startswith('https://'):
            raise ValueError('A verified final result, source and completed timestamp are required.')
        with self.db() as db:
            times=[timestamp(r[0]) for r in db.execute('SELECT kickoff FROM forecasts WHERE sport=? AND event_id=?',(sport,str(event_id)))]
            if not times or any(t is None or at<t for t in times):raise ValueError('Final result cannot precede kickoff or refer to an unknown event.')
            bodies=[json.loads(r[0]) for r in db.execute('SELECT body FROM forecasts WHERE sport=? AND event_id=?',(sport,str(event_id)))]
            valid={team for b in bodies for team in b.get('teams',[])}
            if sport in ('soccer','nfl'):valid.add('Draw')
            if winner not in valid:raise ValueError('Final winner does not match the recorded event teams.')
            existing=db.execute('SELECT winner FROM results WHERE sport=? AND event_id=?',(sport,str(event_id))).fetchone()
            if existing and existing[0]!=winner:raise ValueError('Conflicting final result requires manual review; nothing overwritten.')
            db.execute('INSERT OR IGNORE INTO results VALUES (?,?,?,?,?,?)',(sport,str(event_id),winner,source,at.isoformat(),now.isoformat()))

    def refresh_results(self,fetcher=None,limit=2):
        from .sports_results import verified_result
        fetcher=fetcher or verified_result
        now=self.clock();settled=0
        with self.db() as db:
            rows=db.execute('''SELECT f.sport,f.event_id,f.body FROM forecasts f
                LEFT JOIN results r ON r.sport=f.sport AND r.event_id=f.event_id
                LEFT JOIN settlement_attempts a ON a.sport=f.sport AND a.event_id=f.event_id
                WHERE f.eligible=1 AND r.event_id IS NULL AND f.kickoff<=?
                  AND (a.at IS NULL OR a.at<=?) GROUP BY f.sport,f.event_id LIMIT ?''',
                ((now-timedelta(hours=4)).isoformat(),(now-timedelta(minutes=15)).isoformat(),min(4,max(0,int(limit))))).fetchall()
            for sport,event,body in rows:
                db.execute('INSERT OR REPLACE INTO settlement_attempts VALUES (?,?,?)',(sport,event,now.isoformat()))
        for sport,event,body in rows:
            try:
                result=fetcher(json.loads(body),now=now)
                if result:
                    self.settle(sport,event,result['winner'],result['source'],result['observed_at'],verified=result.get('verified') is True,status='final')
                    settled+=1
            except (ValueError,KeyError,OSError):pass
        return settled

    def summary(self):
        with self.db() as db:
            total,eligible=db.execute('SELECT count(*),coalesce(sum(eligible),0) FROM forecasts').fetchone()
            # Score the FIRST eligible observation per event/model,
            # not every refreshed probability for the same game.
            rows=db.execute('''SELECT f.probability,f.baseline,f.selection,r.winner,f.sport,f.model,f.body FROM forecasts f
                JOIN results r ON r.sport=f.sport AND r.event_id=f.event_id
                WHERE f.eligible=1 AND f.id=(SELECT x.id FROM forecasts x WHERE x.eligible=1
                  AND x.sport=f.sport AND x.event_id=f.event_id AND x.model=f.model
                  ORDER BY x.first_seen,x.rowid LIMIT 1)''').fetchall()
            channels=dict(db.execute('SELECT channel,count(*) FROM report_visits GROUP BY channel'))
            unique_events=db.execute('SELECT count(*) FROM (SELECT DISTINCT sport,event_id FROM forecasts WHERE eligible=1)').fetchone()[0]
            unique_models=db.execute('SELECT count(*) FROM (SELECT DISTINCT sport,event_id,model FROM forecasts WHERE eligible=1)').fetchone()[0]
        errors=[(r[0]-int(r[2]==r[3]))**2 for r in rows]
        baseline=[(r[1]-int(r[2]==r[3]))**2 for r in rows]
        groups={}
        for p,base,selection,winner,sport,model,body in rows:
            quality=(json.loads(body).get('data_quality') or {}).get('state','unverified')
            g=groups.setdefault((sport,model,quality),[]);g.append(((p-int(selection==winner))**2,(base-int(selection==winner))**2))
        return {'recorded':total,'pre_event_eligible':eligible,'unique_pre_event_events':unique_events,
                'eligible_event_models':unique_models,'settled_scored':len(rows),'channels':channels,
                'brier_score':sum(errors)/len(errors) if errors else None,
                'uniform_baseline_brier':sum(baseline)/len(baseline) if baseline else None,
                'by_model':[{'sport':k[0],'model':k[1],'data_quality':k[2],'settled':len(v),'brier':sum(x[0] for x in v)/len(v),
                    'baseline_brier':sum(x[1] for x in v)/len(v),'sample_state':'collecting; no profitability claim'} for k,v in groups.items()],
                'scope':'First eligible pre-event observation per event/model; binary Brier for the selected winner, '
                        'not a full multiclass score. Uniform baseline, not bookmaker value. '
                        'Refreshes are not independent trials. Unsettled or hindsight/stale observations are not scored. '
                        'No verified outcomes means no accuracy or profitability claim.'}
