"""Local health observations, sustained alerts and receipts. No repairs or model calls.

No screenshots, window titles, command lines, network calls or personal file reads.
Sampling/notifications stop while paused, locked, busy or in a private login.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid
import psutil
from PySide6.QtCore import QObject, QTimer, Signal, Slot


def sample():
    memory=psutil.virtual_memory()
    disk=psutil.disk_usage(Path.home().anchor)
    processes=[]
    for p in psutil.process_iter(['name','memory_info']):
        try:
            info=p.info
            if info.get('name') and info.get('memory_info'):
                processes.append({'name':info['name'],'rss_mb':round(info['memory_info'].rss/1048576)})
        except (psutil.Error,OSError):
            pass
    return dict(at=datetime.now(timezone.utc).isoformat(),ram_percent=memory.percent,
                available_gb=round(memory.available/1073741824,2),cpu_percent=psutil.cpu_percent(interval=.2),
                disk_free_gb=round(disk.free/1073741824,2),disk_free_percent=round(100-disk.percent,1),
                largest_processes=sorted(processes,key=lambda x:x['rss_mb'],reverse=True)[:3])


class HealthState:
    def __init__(self,path,clock=time.time):
        self.path,self.clock=Path(path),clock
        self.lock=threading.RLock()
        try:
            self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            self.data={}
        if not isinstance(self.data,dict): self.data={}
        self.data.setdefault('enabled',False)
        self.data.setdefault('samples',[])
        self.data.setdefault('events',[])
        self.data.setdefault('last_alert',{})
        if type(self.data['enabled']) is not bool:self.data['enabled']=False
        for name,fields in (('samples',('at','ram_percent','cpu_percent','disk_free_gb')),('events',('at','message'))):
            rows=self.data[name]
            self.data[name]=[r for r in rows if isinstance(r,dict) and all(k in r for k in fields)] if isinstance(rows,list) else []
        if not isinstance(self.data['last_alert'],dict):self.data['last_alert']={}
        self.counts={}

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temp.write_text(json.dumps(self.data,indent=2),encoding='utf-8')
        temp.replace(self.path)

    def accept(self,reading):
        with self.lock:
            previous=self.data['samples'][-1] if self.data['samples'] else None
            self.data['samples']=(self.data['samples']+[reading])[-60:]
            now=self.clock();messages=[]
            tests={'ram':reading['ram_percent']>=90,'cpu':reading['cpu_percent']>=90,
                   'disk':reading['disk_free_gb']<5 or reading['disk_free_percent']<5}
            for kind,high in tests.items():
                self.counts[kind]=self.counts.get(kind,0)+1 if high else 0
                if self.counts[kind]<3 or now-self.data['last_alert'].get(kind,0)<1800:
                    continue
                self.data['last_alert'][kind]=now
                if kind=='ram':
                    names=', '.join(f'{p["name"]} {p["rss_mb"]} MB' for p in reading['largest_processes'])
                    change=(f' Previous observed RAM: {previous["ram_percent"]:.0f}%.' if previous else '')
                    text=f'RAM stayed at or above 90% for three checks; now {reading["ram_percent"]:.0f}%.{change} Largest processes: {names}. No apps closed; high usage alone does not prove a fault.'
                elif kind=='cpu':
                    text=f'CPU stayed at or above 90% for three checks; now {reading["cpu_percent"]:.0f}%. No process stopped.'
                else:
                    text=f'Low system-drive space persisted for three checks: {reading["disk_free_gb"]:.1f} GB free. No files deleted.'
                self.data['events']=(self.data['events']+[{'at':reading['at'],'message':text}])[-30:]
                messages.append(text)
            self.save()
            return messages

    def summary(self):
        with self.lock:
            lines=[f'Proactive care: {"ON" if self.data["enabled"] else "OFF"}. Read-only local health checks; no model calls or automatic repairs.']
            if self.data['samples']:
                s=self.data['samples'][-1]
                lines.append(f'Last observation {s["at"]}: RAM {s["ram_percent"]:.0f}%, CPU {s["cpu_percent"]:.0f}%, disk free {s["disk_free_gb"]:.1f} GB.')
                if len(self.data['samples'])>1:
                    delta=s['ram_percent']-self.data['samples'][-2]['ram_percent']
                    lines.append(f'RAM change since previous observed check: {delta:+.1f} percentage points. This is not a leak diagnosis.')
            else: lines.append('No observations yet.')
            if self.data['events']:
                lines.extend(e['at']+' · '+e['message'] for e in self.data['events'][-3:])
            lines.append('ScorPred has its separate 6 a.m. routine. For app work use “in the background: …” or “keep working on …”. Stop/Pause remains available.')
            return '\n'.join(lines)


class ProactiveCare(QObject):
    _done=Signal(object)
    def __init__(self,path,allowed,notify,reader=sample):
        super().__init__()
        self.state=HealthState(path)
        self.allowed,self.notify,self.reader=allowed,notify,reader
        self.running=False
        self.closed=False
        self._done.connect(self._finish)
        self.timer=QTimer(self);self.timer.setInterval(60000);self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(3000,self.tick)

    def command(self,text):
        key=' '.join(text.lower().split()).rstrip('.!?')
        if key in ('enable proactive care','turn on proactive care','disable proactive care','turn off proactive care'):
            with self.state.lock:
                self.state.data['enabled']=key.startswith(('enable','turn on'))
                self.state.counts.clear()
                self.state.save()
            return self.state.summary()
        if key in ('proactive status','proactive care status','what did you notice','check laptop changes'):
            return self.state.summary()
        return None

    @Slot()
    def tick(self):
        if self.closed or self.running or not self.state.data['enabled'] or not self.allowed():
            # Paused intervals cannot contribute to a sustained-pressure alert.
            self.state.counts.clear()
            return
        self.running=True
        threading.Thread(target=self._read,name='ano-health-observer',daemon=True).start()

    def _read(self):
        try:
            result=self.reader()
        except Exception:
            result=None
        if not self.closed:self._done.emit(result)

    @Slot(object)
    def _finish(self,result):
        self.running=False
        if self.closed or not self.state.data['enabled'] or not self.allowed():
            self.state.counts.clear()
            return
        if result is None:
            self.state.counts.clear()
            return
        try:
            messages=self.state.accept(result)
        except (OSError,ValueError,KeyError,TypeError):
            return
        for text in messages:self.notify('Proactive observation: '+text)

    def close(self):
        self.closed=True;self.timer.stop()
