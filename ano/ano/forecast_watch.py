"""Quiet pre-event capture and verified settlement, independent of report requests."""
from datetime import datetime,timedelta,timezone
import threading,time,os
import psutil
import httpx
from .report_ledger import ReportLedger
from .sports_reports import SportsReports,NY
from .reliability import atomic_json


class ForecastWatch:
    def __init__(self,state,root,allowed=lambda:True):
        self.state,self.root,self.allowed=state,root,allowed;self.stop=threading.Event();self.thread=None
    def health(self,value):
        atomic_json(self.state/'forecast-watch.health.json',{'at':datetime.now(timezone.utc).isoformat(),
            'owner_pid':os.getpid(),'owner_started':psutil.Process().create_time(),
            'background_thread_alive':bool(self.thread and self.thread.is_alive()),**value})
    @staticmethod
    def get(path,params):
        if path!='/api/local-runtime' and path not in ('/api/dashboard/nba','/api/dashboard/nfl','/api/dashboard/soccer'):raise ValueError('Unknown forecast route')
        with httpx.Client(timeout=httpx.Timeout(25,connect=3),trust_env=False,follow_redirects=False) as c:
            r=c.get('http://127.0.0.1:5001'+path,params=params)
        if len(r.content)>2000000:raise ValueError('Oversized forecast response')
        return r.json(),r.status_code
    def sample(self):
        self.health({'state':'checking'})
        ledger=ReportLedger(self.state/'report-observations.sqlite3')
        service=SportsReports(self.root,getter=self.get,ledger=ledger,channel='automatic-watch');service.identity()
        today=datetime.now(NY).date();count=0;errors=[];waiting=0;provisional=0;started=time.monotonic()
        for day in (today,today+timedelta(days=1)):
            for sport,league in (('nba',None),('nfl',None),('soccer',39),('soccer',78),('soccer',140)):
                if self.stop.is_set():return
                if time.monotonic()-started>220:
                    errors.append(sport+': observation budget exhausted');continue
                try:
                    payload,code=service.snapshot(sport,str(day),league)
                    if code!=200 or payload.get('error'):errors.append(sport+': unavailable')
                    else:
                        count+=len(payload.get('slate') or [])
                        waiting+=sum((r.get('dataQuality') or {}).get('state')=='waiting' for r in payload.get('slate') or [])
                        provisional+=sum((r.get('dataQuality') or {}).get('state')=='provisional' for r in payload.get('slate') or [])
                        from .reliability import read_json
                        if read_json(self.state/'report-observations.health.json').get('state')=='error':errors.append(sport+': recording unavailable')
                except Exception as e:errors.append(sport+': '+type(e).__name__)
        settled=ledger.refresh_results(limit=4)
        self.health({
            'state':'waiting' if errors else 'checked','rows_seen':count,'quality_waiting_rows':waiting,'quality_provisional_rows':provisional,'settled_now':settled,'errors':errors,
            'scope':'Research forecasts captured before kickoff; source/quality states retained. No recommendations, bets or emails sent.'})
    def start(self):
        def run():
            while not self.stop.is_set():
                started=time.monotonic();interval=300
                try:
                    if self.allowed():self.sample()
                    else:self.health({'state':'paused','scope':'No forecast requests while paused/private/locked.'})
                except Exception as e:
                    self.health({'state':'waiting','error_type':type(e).__name__});interval=30
                self.stop.wait(max(1,interval-(time.monotonic()-started)))
        self.thread=threading.Thread(target=run,name='ano-forecast-evidence',daemon=True);self.thread.start()
    def close(self):self.stop.set()
