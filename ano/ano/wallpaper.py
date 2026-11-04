"""Opted-in local Ano wallpaper startup, bounded recovery and telemetry."""
import json, os, subprocess, sys, time
from pathlib import Path
import psutil
from .paths import data_directory

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = Path(os.environ.get('LOCALAPPDATA', ''))/'Lively Wallpaper/Library/wallpapers/Ano-Galaxy-Watch'
EXE = Path(os.environ.get('LOCALAPPDATA', ''))/'Programs/Lively Wallpaper/Lively.exe'

def enabled():
    try:return json.loads((data_directory()/'wallpaper.json').read_text()).get('enabled') is True
    except (OSError, ValueError):return False

def ensure_running():
    if not enabled():return
    for p in psutil.process_iter(['cmdline']):
        args=p.info.get('cmdline') or []
        if '-m' in args and args[args.index('-m')+1:args.index('-m')+2]==['ano.wallpaper']:return
    subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','ano.wallpaper'],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def main():
    import msvcrt
    from .reliability import atomic_json
    from datetime import datetime, timezone
    state=data_directory();state.mkdir(parents=True,exist_ok=True)
    lock=(state/'wallpaper.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):lock.write(b'0');lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:return
    # Stations come from Ano itself (ano/wallpaper_status.py); add a feature's station there, not in the wallpaper.
    from .wallpaper_status import sample
    from .wallpaper_guard import AssetGuard
    asset_guard=AssetGuard(state,LIBRARY)
    last_launch=0;attempts=[];first=True;refresh_needed=False
    while enabled():
        try:
            now=time.time()
            if asset_guard.check():refresh_needed=True
            running=any((p.info.get('name') or '').lower()=='lively.exe' for p in psutil.process_iter(['name']))
            attempts=[t for t in attempts if now-t<3600]
            if EXE.is_file() and (first or not running or refresh_needed) and now-last_launch>=60 and len(attempts)<3:
                subprocess.Popen([str(EXE),'setwp','--file',str(LIBRARY)],cwd=EXE.parent,
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                last_launch=now;attempts.append(now);first=False;refresh_needed=False
            payload=sample();tmp=LIBRARY/'telemetry.ano.tmp'
            # the crawler's flags, for Ano's repairs (ano/repairs.py answers them from a fixed list of safe fixes)
            atomic_json(state/'crawler-signals.json',{'at':datetime.now(timezone.utc).isoformat(),
                'flags':[{'station':s['id'],'state':s['state'],'headline':s['headline']} for s in payload['stations'] if s['state']!='healthy']})
            tmp.write_text('window.receiveAnoStatus && window.receiveAnoStatus('+json.dumps(payload)+');',encoding='utf-8')
            os.replace(tmp,LIBRARY/'telemetry.js')
            atomic_json(state/'wallpaper.health.json',{'at':datetime.now(timezone.utc).isoformat(),
                'pid':os.getpid(),'lively_running':running,'enabled':True,'recovery_attempts_hour':len(attempts)})
        except Exception as error:
            # Preserve the last successful telemetry timestamp; the page marks it stale.
            try:atomic_json(state/'wallpaper.health.json',{'at':datetime.now(timezone.utc).isoformat(),
                'pid':os.getpid(),'enabled':True,'state':'attention','error_type':type(error).__name__})
            except OSError:pass
        time.sleep(5)

if __name__=='__main__':main()
