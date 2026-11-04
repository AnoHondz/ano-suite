"""Fixed local keep-alive service. Never kills processes or sends orders/mail.

Starts only a missing Ano, an offline identified-installation ScorPred, an
offline installed Ollama, and an opted-in email worker. A stale heartbeat on a live process is attention,
not permission to replace an owner. Windows sign-in runs this windowlessly.
"""
from datetime import datetime,timezone
from pathlib import Path
import ctypes,json,os,subprocess,sys,time
import psutil
from .paths import data_directory
from .reliability import atomic_json,read_json

ROOT=Path(__file__).resolve().parent.parent
SCOR_ROOT=Path.home()/'Documents'/'ScorPred-Surface-2026-10-24'/'ScorPred'


def suspend(state=None):
    state=state or data_directory()
    atomic_json(state/'supervisor.suspended.json',{'at':datetime.now(timezone.utc).isoformat(),'reason':'User requested Quit Ano'})


def resume(state=None):
    (state or data_directory()).joinpath('supervisor.suspended.json').unlink(missing_ok=True)


def observation(state,now=None):
    """A saved file alone never proves a live supervisor."""
    h=read_json(state/'supervisor.health.json');now=now or datetime.now(timezone.utc)
    try:
        age=(now-datetime.fromisoformat(h['at'])).total_seconds()
        p=psutil.Process(h['pid']);args=p.cmdline()
        owner=abs(p.create_time()-h['owner_started'])<.01 and '-m' in args and args[args.index('-m')+1]=='ano.supervisor'
        fresh=owner and 0<=age<=90
    except (KeyError,ValueError,TypeError,OSError,psutil.Error):fresh=False;age=None
    return {**h,'fresh':bool(fresh),'age_seconds':age,'verified_state':h.get('state') if fresh else 'unverified/stopped/stale'}


def ensure_running(state):
    """UI-side recovery of a missing helper; never replace a live helper."""
    from . import autostart
    if not autostart.enabled() or (state/'supervisor.suspended.json').exists():return
    if processes('ano.supervisor'):return
    policy=Policy(state)
    if not policy.permit('supervisor'):return
    policy.record('supervisor')
    folder=state/'supervisor-logs';folder.mkdir(exist_ok=True)
    with (folder/'supervisor.log').open('ab') as log:
        subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','ano.supervisor'],cwd=str(ROOT),
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def processes(module):
    """Only this checkout's Python module processes; venv redirectors count too."""
    found=[]
    for process in psutil.process_iter(['pid','name','cmdline','cwd']):
        try:
            row=process.info;args=row.get('cmdline') or []
            if not str(row.get('name','')).lower().startswith('python'):continue
            if '-m' not in args or args[args.index('-m')+1:args.index('-m')+2]!=[module]:continue
            if Path(row.get('cwd') or '').resolve()!=ROOT:continue
            found.append(row['pid'])
        except (psutil.Error,OSError,ValueError):continue
    return found


def trading_window(now=None):
    """True during a forex session or New York stock hours (Mon-Fri), with a few minutes either side."""
    from datetime import time as clock,timedelta
    from .forex_sessions import SESSIONS,context
    from . import ict
    now=now or datetime.now(timezone.utc)
    for name in SESSIONS:
        c=context(name,now)
        if c['start'].weekday()<5 and c['start']-timedelta(minutes=10)<=now<c['end']+timedelta(minutes=15):return True
    ny=now.astimezone(ict.NY)
    return ny.weekday()<5 and clock(9,20)<=ny.time()<clock(16,10)


def keep_awake(on,trading=False):
    """Temporary wake requests; no power-policy changes.

    Plugged in: keep the system awake. During trading sessions also keep the display awake: on this
    Modern Standby laptop the screen timing out sends Windows into standby, which froze the bots
    through the whole London session on 2026-11-02. On battery the display request needs 30%+ charge,
    so a forgotten charger cannot run the laptop flat."""
    if sys.platform!='win32':return False
    class Power(ctypes.Structure):
        _fields_=[('ac',ctypes.c_ubyte),('battery',ctypes.c_ubyte),('percent',ctypes.c_ubyte),
                  ('reserved',ctypes.c_ubyte),('life',ctypes.c_uint32),('full',ctypes.c_uint32)]
    power=Power()
    known=bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(power)))
    ac=known and power.ac==1
    charged=known and (ac or (power.percent!=255 and power.percent>=30))
    system=bool(on and ac);display=bool(on and trading and charged)
    flags=0x80000000|(0x00000001 if system or display else 0)|(0x00000002 if display else 0)
    result=ctypes.windll.kernel32.SetThreadExecutionState(flags)
    return bool(result and (system or display))


class Policy:
    def __init__(self,state,clock=time.time):
        self.path=state/'supervisor.policy.json';self.clock=clock;self.data=read_json(self.path)
    def permit(self,name):
        now=self.clock()
        recent=[t for t in self.data.get(name,[]) if isinstance(t,(int,float)) and 0<=now-t<3600]
        return len(recent)<3 and (not recent or now-recent[-1]>=60)
    def record(self,name):
        now=self.clock()
        self.data[name]=[t for t in self.data.get(name,[]) if isinstance(t,(int,float)) and 0<=now-t<3600]+[now]
        atomic_json(self.path,self.data)


def scor_probe():
    import httpx
    try:
        with httpx.Client(timeout=3,trust_env=False,follow_redirects=False) as client:
            runtime=client.get('http://127.0.0.1:5001/api/local-runtime')
            if runtime.status_code!=200:return 'attention'
            data=runtime.json()
            if data.get('app')!='ScorPred' or Path(data.get('root','')).resolve()!=SCOR_ROOT.resolve():return 'attention'
            r=client.get('http://127.0.0.1:5001/health')
            h=r.json() if r.status_code==200 else {}
            from .scorpred_health import assess
            pipeline=client.get('http://127.0.0.1:5001/api/health')
            state,detail=assess(h,pipeline.json() if pipeline.status_code in (200,503) else {})
            return 'healthy' if state=='healthy' else state+': '+detail
    except httpx.ConnectError:return 'offline'
    except Exception:return 'attention'


OLLAMA_APP=Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Ollama'/'ollama app.exe'


def ollama_probe():
    """Only a refused connection counts as stopped; a slow or busy server is left alone."""
    import httpx
    try:
        with httpx.Client(timeout=3,trust_env=False,follow_redirects=False) as client:
            return 'healthy' if client.get('http://127.0.0.1:11434/api/version').status_code==200 else 'attention'
    except httpx.ConnectError:return 'offline'
    except Exception:return 'attention'


class Supervisor:
    def __init__(self,state,clock=time.time):
        self.state=state;self.clock=clock;self.policy=Policy(state,clock)
        self.children={};self.offline=0;self.ollama_offline=0;self.errors={}

    def launch(self,name,args,cwd):
        old=self.children.get(name)
        if old and old.poll() is None:return 'starting'
        if not self.policy.permit(name):return 'attention: recovery rate limit (3/hour, 60s apart)'
        self.policy.record(name)
        log=self.state/'supervisor-logs';log.mkdir(exist_ok=True)
        with (log/(name+'.log')).open('ab') as output:
            try:
                self.children[name]=subprocess.Popen(args,cwd=str(cwd),stdin=subprocess.DEVNULL,
                    stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                return 'starting'
            except OSError:return 'attention: fixed launcher failed'

    def tick(self):
        suspended=(self.state/'supervisor.suspended.json').exists()
        awake=keep_awake(not suspended,trading=trading_window())
        checks={};python=ROOT/'.venv'/'Scripts'/'pythonw.exe'
        pids=processes('ano')
        if suspended:checks['Ano']='suspended by user Quit; manual open resumes'
        elif pids:checks['Ano']='running'
        else:checks['Ano']=self.launch('ano',[str(python),'-m','ano','--tray'],ROOT)
        # Do not repair or start other components following an intentional Quit.
        if not suspended:
            probe=scor_probe();self.offline=self.offline+1 if probe=='offline' else 0
            checks['ScorPred']=probe
            if self.offline>=3:
                checks['ScorPred']=self.launch('scorpred',['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass',
                    '-File',str(SCOR_ROOT/'Start-ScorPred.ps1')],SCOR_ROOT)
            # Ano's local brain. Same rule as ScorPred: three refused probes in a row, installed app only.
            if OLLAMA_APP.is_file():
                probe=ollama_probe();self.ollama_offline=self.ollama_offline+1 if probe=='offline' else 0
                checks['Ollama']=probe
                if self.ollama_offline>=3:
                    checks['Ollama']=self.launch('ollama',[str(OLLAMA_APP)],OLLAMA_APP.parent)
            mail=read_json(self.state/'sports-mail/config.json')
            if mail.get('enabled'):
                checks['Email worker']='running' if processes('ano.sports_mail') else self.launch('sports-mail',
                    [str(python),'-m','ano.sports_mail','serve'],ROOT)
            for component,filename in (('Ano UI','ano-ui.health.json'),('ETF manager','paper-execution.health.json'),
                                       ('Forex manager','forex-execution.health.json'),('Email heartbeat','sports-mail/heartbeat.json')):
                health=read_json(self.state/filename)
                try:age=self.clock()-datetime.fromisoformat(health['at']).timestamp()
                except (KeyError,TypeError,ValueError):age=None
                fresh=age is not None and 0<=age<=120
                checks[component]='fresh' if fresh else 'attention: heartbeat stale/missing; no live process killed'
        atomic_json(self.state/'supervisor.health.json',{'at':datetime.now(timezone.utc).isoformat(),
            'pid':os.getpid(),'owner_started':psutil.Process().create_time(),'state':'suspended' if suspended else 'watching','checks':checks,
            'ac_sleep_inhibited':awake,'scope':'Fixed local recovery only; no orders, email sends, process kills or risk-limit changes.'})


def main():
    state=data_directory();state.mkdir(parents=True,exist_ok=True)
    if 'status' in sys.argv[1:]:
        print(json.dumps(observation(state),indent=2));return
    import msvcrt
    lock=(state/'supervisor.lock').open('a+b');lock.seek(0)
    if not lock.read(1):lock.write(b'0');lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:lock.close();return
    if '--sign-in' in sys.argv[1:]:resume(state)
    service=Supervisor(state)
    try:
        from . import autostart
        while True:
            try:
                if not autostart.enabled(strict=True):break
            except OSError:
                # A transient registry read failure is not a user disable.
                time.sleep(30);continue
            try:service.tick()
            except Exception as error:
                atomic_json(state/'supervisor.health.json',{'at':datetime.now(timezone.utc).isoformat(),
                    'pid':os.getpid(),'state':'attention','error_type':type(error).__name__})
            time.sleep(30)
    finally:
        atomic_json(state/'supervisor.exit.json',{'at':datetime.now(timezone.utc).isoformat(),
            'pid':os.getpid(),'reason':'unhandled_exception' if sys.exc_info()[0] else 'autostart_disabled',
            'exception':sys.exc_info()[0].__name__ if sys.exc_info()[0] else None})
        keep_awake(False);lock.close()


if __name__=='__main__':main()
