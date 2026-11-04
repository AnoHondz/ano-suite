"""Release idle Ano model memory, without killing apps or interrupting inference."""
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time

from .inference_gate import acquire, InferenceBusy, memory_pressure
from .local_runtime import ollama_json
from .model_roles import local_model_name


class MemoryManager:
    def __init__(self, state, models, *, chat_models=(), query=ollama_json,
                 sample=memory_pressure, clock=time.time, interval=15):
        self.state=Path(state)
        self.models=frozenset(m for m in models if local_model_name(m))
        self.chat_models=frozenset(chat_models)&self.models
        self.query,self.sample,self.clock=query,sample,clock
        self.interval=interval
        self.lock_path=self.state/'local-inference.lock'
        self.stop=threading.Event()
        self.thread=None
        self.started=clock()
        self.last_attempt={}
        self.last_unloaded=[]
        from .night_guard import NightGuard
        self.night_guard=NightGuard(self.state,sample)

    def _health(self, state, pressure, **details):
        from .reliability import atomic_json
        result={'at':datetime.now(timezone.utc).isoformat(),'pid':os.getpid(),'state':state,
                'enabled':True,'ram_percent':pressure[0] if pressure else None,
                'available_bytes':pressure[1] if pressure else None,
                'last_unloaded':self.last_unloaded,
                'policy':{'pressure_percent':85,'critical_percent':92,
                          'pressure_idle_seconds':30,'large_model_idle_seconds':120,
                          'chat_model_idle_seconds':300},
                'scope':'Configured Ano models only; no app closing, file deletion, trading changes or paid fallback.',**details}
        atomic_json(self.state/'memory-manager.health.json',result)
        return result

    def sweep(self, *, force=False):
        pressure=self.sample()
        try:
            # Cleanup must respect exactly the same cross-process inference lock.
            # Its own checks do not reset the last real request's idle timestamp.
            lease=acquire(self.lock_path,lambda:None,activity=False)
        except InferenceBusy:
            return self._health('inference_active',pressure)
        with lease:
            now=self.clock()
            try:last_use=max(self.started,self.lock_path.stat().st_mtime)
            except OSError:last_use=now
            idle=max(0,now-last_use)
        # Routine status reads must not occupy the inference lock while waiting
        # on HTTP. Reacquire and recheck activity immediately before an unload.
        tight=bool(pressure and (pressure[0]>=85 or pressure[1]<1536*1024**2))
        try:
            resident=self.query('/api/ps').get('models',[])
            sizes={item.get('name') or item.get('model'):item.get('size',0) for item in resident
                   if type(item.get('size',0)) in (int,float)}
            def eligible(idle,now):
                candidates=[]
                for item in resident:
                    name=item.get('name') or item.get('model')
                    if name not in self.models:continue
                    wait=30 if tight else 300 if name in self.chat_models else 120
                    if (force or idle>=wait) and now-self.last_attempt.get(name,float('-inf'))>=60:
                        candidates.append(name)
                return sorted(candidates,key=lambda name:sizes.get(name,0),reverse=True)
            candidates=eligible(idle,now)
            if not candidates:
                return self._health('waiting_for_idle' if resident else 'monitoring',pressure,idle_seconds=round(idle))
            try:cleanup=acquire(self.lock_path,lambda:None,activity=False)
            except InferenceBusy:return self._health('inference_active',pressure)
            with cleanup:
                now=self.clock()
                idle=max(0,now-max(self.started,self.lock_path.stat().st_mtime))
                candidates=eligible(idle,now)
                requested=[]
                for name in candidates[:2]:
                    self.last_attempt[name]=now
                    # Empty generation with keep_alive=0 only unloads. No prompt,
                    # inference, downloads, cloud requests or process termination.
                    answer=self.query('/api/generate',{'model':name,'keep_alive':0})
                    if answer.get('done') is True and answer.get('done_reason')=='unload':requested.append(name)
                    if tight and not force and requested:
                        relieved=self.sample()
                        if relieved and relieved[0]<85 and relieved[1]>=1536*1024**2:
                            break  # Keep the small chat model warm when enough RAM is free.
                if requested:
                    remaining={m.get('name') or m.get('model') for m in self.query('/api/ps').get('models',[])}
                    unloaded=[name for name in requested if name not in remaining]
                    if unloaded:self.last_unloaded=unloaded
                    return self._health('released_idle_models' if unloaded else 'release_pending',self.sample(),
                                        unloaded=unloaded,idle_seconds=round(idle))
                return self._health('waiting_for_idle' if candidates==[] and resident else 'monitoring',pressure,
                                    idle_seconds=round(idle))
        except Exception as error:
            # Monitoring is best-effort and cannot stop the UI or trade managers.
            return self._health('temporarily_unavailable',pressure,error_type=type(error).__name__)

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        self.thread=threading.Thread(target=self._run,name='ano-memory-manager',daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:self.sweep()
            except Exception:pass  # A diagnostics/disk failure cannot terminate management.
            # Overnight only, with the user's standing permission: other apps give way to the trading bots.
            try:self.night_guard.check()
            except Exception:pass
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=1)
