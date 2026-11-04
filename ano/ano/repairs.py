"""Fixed autonomous repairs: recover a stopped local ScorPred service, and answer the
wallpaper crawler's flags from a short fixed list of safe fixes (CRAWLER_FIXES).

Never restart a responding/busy service, run generated shell, kill processes,
delete user files, change security, send email or touch trading. Every attempt has evidence.
"""
from datetime import datetime,timezone
import json
from pathlib import Path
import threading
import time
import uuid
from PySide6.QtCore import QObject,QTimer,Signal,Slot
from .tools import ToolError


class RepairPolicy:
    def __init__(self,path,clock=time.time):
        self.path,self.clock=Path(path),clock
        self.lock=threading.RLock()
        try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.data={}
        if not isinstance(self.data,dict):self.data={}
        for key,value in dict(enabled=False,attempts=[],pending_id='',notices=[]).items():self.data.setdefault(key,value)
        if type(self.data['enabled']) is not bool:self.data['enabled']=False
        if not isinstance(self.data['attempts'],list):self.data['attempts']=[]
        if not isinstance(self.data['notices'],list):self.data['notices']=[]
        self.offline_checks=0

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temp.write_text(json.dumps(self.data,indent=2),encoding='utf-8');temp.replace(self.path)

    def ready(self,probe,receipt):
        with self.lock:
            if not self.data['enabled'] or receipt.get('state') in ('running','uncertain') or self.data['pending_id']:
                self.offline_checks=0;return False
            if probe.get('state')!='offline':
                self.offline_checks=0;return False
            self.offline_checks+=1
            if self.offline_checks<3:return False
            now=self.clock()
            recent=[t for t in self.data['attempts'] if isinstance(t,(int,float)) and 0<=now-t<86400]
            return len(recent)<2 and (not recent or now-max(recent)>=3600)

    def started(self,identifier):
        with self.lock:
            self.data['pending_id']=identifier
            self.data['uncertain_notified']=False
            now=self.clock();self.data['attempts']=[t for t in self.data['attempts'] if isinstance(t,(int,float)) and now-t<86400]+[now]
            self.offline_checks=0;self.save()

    def completion(self,receipt):
        with self.lock:
            identifier=self.data['pending_id']
            if not identifier or receipt.get('id')!=identifier or receipt.get('state')=='running':return None
            # Unknown evidence remains a block, even if the process was restarted.
            if receipt.get('state') not in ('verified','failed','uncertain'):return None
            if receipt['state']=='uncertain' and self.data.get('uncertain_notified'):return None
            if receipt['state']=='uncertain':self.data['uncertain_notified']=True
            message='ScorPred repair '+receipt['state']+': '+receipt.get('summary','No detailed receipt.')
            self.data['notices']=(self.data['notices']+[{'at':datetime.now(timezone.utc).isoformat(),'message':message}])[-20:]
            self.data['pending_id']='' if receipt['state']!='uncertain' else identifier
            self.save();return message

    def summary(self,receipt):
        return (f'Automatic repairs: {"ON" if self.data["enabled"] else "OFF"}. '
                'Scope: stopped ScorPred only, after three refused-connection checks. At most two attempts per rolling day, at least one hour apart. '
                'Responding, busy, degraded or unidentified services are not restarted. No process killing, file deletion, security changes or email.\n'
                'Crawler-flagged fixes: free memory by unloading idle Ano AI models when RAM is 92%+, and start the email worker '
                'when it has stopped. At most three each per hour, ten minutes apart. Trading bots are never touched.\n'
                f'Last ScorPred command: {receipt.get("state","none")} — {receipt.get("summary","")}\n'
                f'Last automatic probe: {self.data.get("last_probe",{}).get("state","not checked yet")}.\n'
                'Other computer faults use diagnosis and the existing reviewed Run fix workflow.')


# ── Crawler-signalled repairs ────────────────────────────────────────────────────────────────
# The wallpaper's crawler visits flagged stations; ano.wallpaper writes those flags to crawler-signals.json.
# Ano answers a flag only from this fixed list (never model text). The fix itself checks it still applies.
CRAWLER_FIXES={'system':'freeing memory: unloading idle ano ai models','mail':'starting the email worker again'}


def free_memory(manager,sample=None):
    """RAM 92%+: unload idle Ano models now, through the memory manager's own inference-lock-respecting sweep."""
    from .inference_gate import memory_pressure
    pressure=(sample or memory_pressure)()
    if not pressure or pressure[0]<92:return None
    unloaded=manager.sweep(force=True).get('unloaded') or []
    return 'unloaded '+', '.join(unloaded) if unloaded else 'no ano ai models were loaded; other apps are using the memory'


def restart_mail(state,running=None,resume=None):
    """Start the opted-in email worker when none is running. A live worker is never replaced."""
    from .supervisor import processes
    from .sports_mail import resume_configured_service
    if (running or (lambda:processes('ano.sports_mail')))():return None
    return 'email worker started again' if (resume or resume_configured_service)(state) else None


class CrawlerRepairs:
    def __init__(self,policy,signals,fixes,clock=time.time):
        self.policy,self.signals,self.fixes,self.clock=policy,Path(signals),fixes,clock

    def flags(self):
        """Stations the crawler is flagging right now (the signal file is rewritten every few seconds)."""
        try:data=json.loads(self.signals.read_text(encoding='utf-8'))
        except (OSError,ValueError):return []
        try:age=self.clock()-datetime.fromisoformat(data['at']).timestamp()
        except (KeyError,TypeError,ValueError,AttributeError):return []
        return [f for f in data.get('flags',[]) if isinstance(f,dict)] if 0<=age<=30 else []

    def records(self):
        rows=self.policy.data.setdefault('crawler',[])
        return [r for r in rows if isinstance(r,dict)] if isinstance(rows,list) else []

    def ready(self,station):
        now=self.clock()
        times=[t for t in self.policy.data.setdefault('crawler_attempts',{}).get(station,[]) if isinstance(t,(int,float)) and 0<=now-t<3600]
        busy=any(r.get('station')==station and r.get('state')=='working' for r in self.records())
        return not busy and len(times)<3 and (not times or now-max(times)>=600)

    def due(self):
        """Flagged stations with a safe fix that is not rate-limited."""
        return [f['station'] for f in self.flags() if f.get('station') in CRAWLER_FIXES and f['station'] in self.fixes and self.ready(f['station'])]

    def start(self,station):
        with self.policy.lock:
            now=self.clock();attempts=self.policy.data.setdefault('crawler_attempts',{})
            attempts[station]=[t for t in attempts.get(station,[]) if isinstance(t,(int,float)) and 0<=now-t<3600]+[now]
            record={'id':uuid.uuid4().hex,'station':station,'what':CRAWLER_FIXES[station],'state':'working',
                    'at':datetime.now(timezone.utc).isoformat(),'detail':''}
            self.policy.data['crawler']=(self.records()+[record])[-20:];self.policy.save();return record

    def finish(self,record_id,outcome):
        """outcome: (done|failed, detail), or None when the fix no longer applied (the attempt is refunded)."""
        with self.policy.lock:
            rows=self.records();row=next((r for r in rows if r.get('id')==record_id),None)
            if row is None:return
            if outcome is None:
                rows.remove(row);times=self.policy.data.setdefault('crawler_attempts',{}).get(row['station'],[])
                if times:times.pop()
            else:row['state'],row['detail']=outcome
            self.policy.data['crawler']=rows;self.policy.save()

    @staticmethod
    def run(fix):
        try:detail=fix()
        except Exception as error:return ('failed',f'the fix stopped with {type(error).__name__}')
        return None if detail is None else ('done',detail)


class RepairCare(QObject):
    _done=Signal(object)
    _crawler_done=Signal(object)
    def __init__(self,path,plugin,allowed,notify):
        super().__init__()
        self.policy=RepairPolicy(path);self.plugin=plugin;self.allowed=allowed;self.notify=notify
        self.running=False;self.closed=False
        self._done.connect(self._finish)
        self.timer=QTimer(self);self.timer.setInterval(120000);self.timer.timeout.connect(self.tick);self.timer.start()
        # fixes for crawler flags are wired in by the app once their owners exist (see CRAWLER_FIXES)
        self.fixes={};self.crawler=CrawlerRepairs(self.policy,Path(path).parent/'crawler-signals.json',self.fixes)
        self._crawler_done.connect(self._crawler_finish)
        self.signal_timer=QTimer(self);self.signal_timer.setInterval(20000);self.signal_timer.timeout.connect(self.signals);self.signal_timer.start()

    @Slot()
    def signals(self):
        if self.closed or not self.policy.data['enabled'] or not self.allowed():return
        # A crawler flag on a stopped ScorPred only means checking sooner; its three checks and daily limits still apply.
        if any(f.get('station')=='scorpred' and f.get('state')=='down' for f in self.crawler.flags()):self.tick()
        for station in self.crawler.due():
            record,fix=self.crawler.start(station),self.fixes[station]
            threading.Thread(target=lambda r=record,f=fix:self._crawler_done.emit((r['id'],CrawlerRepairs.run(f))),
                             name='ano-crawler-repair',daemon=True).start()

    @Slot(object)
    def _crawler_finish(self,result):
        record_id,outcome=result
        self.crawler.finish(record_id,outcome)
        if outcome and not self.closed:self.notify(f'Crawler repair {outcome[0]}: {outcome[1]}.')

    def command(self,text):
        key=' '.join(text.lower().split()).rstrip('.!?')
        if key in ('enable automatic repairs','disable automatic repairs'):
            with self.policy.lock:
                self.policy.data['enabled']=key.startswith('enable');self.policy.offline_checks=0;self.policy.save()
            return self.policy.summary(self.plugin._receipt())
        if key in ('repair status','automatic repair status','what can you repair'):
            return self.policy.summary(self.plugin._receipt())
        return None

    @Slot()
    def tick(self):
        if self.closed or self.running or not self.policy.data['enabled'] or not self.allowed():
            self.policy.offline_checks=0;return
        self.running=True
        threading.Thread(target=self._probe,name='ano-repair-observer',daemon=True).start()

    def _probe(self):
        try:result=self.plugin.repair_probe()
        except Exception:result={'state':'blocked'}
        if not self.closed:self._done.emit(result)

    @Slot(object)
    def _finish(self,result):
        self.running=False
        if self.closed or not self.policy.data['enabled'] or not self.allowed():
            self.policy.offline_checks=0;return
        receipt=self.plugin._receipt()
        message=self.policy.completion(receipt)
        if message:self.notify(message)
        self.policy.data['last_probe']={**result,'at':datetime.now(timezone.utc).isoformat()}
        self.policy.save()
        if self.policy.ready(result,receipt):
            try:
                # Trusted fixed policy only; this entry point never accepts model text.
                self.plugin._launch('scorpred_repair')
                self.policy.started(self.plugin._receipt()['id'])
            except ToolError:
                self.policy.offline_checks=0

    def close(self):
        self.closed=True;self.timer.stop();self.signal_timer.stop()
