"""Stage or activate the recovered galaxy wallpaper; retain existing designs."""
import ctypes,hashlib,json,os,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import psutil
from ano.paths import data_directory
from ano.wallpaper import LIBRARY,ROOT,EXE
from ano.wallpaper_guard import FILES

def main():
    state=data_directory();backup=state/'backups'/('galaxy-install-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True,exist_ok=True);LIBRARY.mkdir(parents=True,exist_ok=True)
    for name in ('wallpaper.json','wallpaper-assets.json'):
        if (state/name).exists():shutil.copy2(state/name,backup/name)
    hashes={}
    for name,source in FILES.items():
        src=ROOT/'ano/assets'/source
        if (LIBRARY/name).exists():shutil.copy2(LIBRARY/name,backup/name)
        shutil.copy2(src,LIBRARY/name);hashes[name]=hashlib.sha256(src.read_bytes()).hexdigest()
    # The preview can use the current local observations without waiting for a service restart.
    previous=Path(os.environ['LOCALAPPDATA'])/'Lively Wallpaper/Library/wallpapers/Obsidian-Ano/telemetry.js'
    if previous.exists() and not (LIBRARY/'telemetry.js').exists():shutil.copy2(previous,LIBRARY/'telemetry.js')
    (state/'wallpaper-assets.json').write_text(json.dumps({'sha256':hashes,'installed_at':datetime.now(timezone.utc).isoformat()},indent=2),encoding='utf-8')
    if '--stage-only' in sys.argv:
        print('Staged:',LIBRARY);return
    # Reload only the wallpaper collector. Ano and trading workers remain untouched.
    health=json.loads((state/'wallpaper.health.json').read_text());p=psutil.Process(health['pid']);args=p.cmdline()
    assert Path(p.cwd()).resolve()==ROOT and '-m' in args and args[args.index('-m')+1]=='ano.wallpaper'
    p.terminate();p.wait(15)
    layout=Path(os.environ['LOCALAPPDATA'])/'Lively Wallpaper/WallpaperLayout.json'
    shutil.copy2(layout,backup/'WallpaperLayout.json');rows=json.loads(layout.read_text(encoding='utf-8-sig'))
    for row in rows:row['LivelyInfoPath']=str(LIBRARY)
    for proc in psutil.process_iter(['name','exe']):
        if (proc.info.get('name') or '').lower()=='lively.exe' and Path(proc.info.get('exe') or '').resolve()==EXE.resolve():
            proc.terminate();proc.wait(15)
    layout.write_text(json.dumps(rows,indent=2),encoding='utf-8')
    fallback=state/'Ano-Galaxy-Wallpaper.png';preview=ROOT/'.runtime/galaxy-desktop.png'
    if preview.exists():
        shutil.copy2(preview,fallback);assert ctypes.windll.user32.SystemParametersInfoW(20,0,str(fallback),3)
    (state/'wallpaper.json').write_text(json.dumps({'enabled':True,'name':'Ano / Galaxy Watch','library':str(LIBRARY),'fallback':str(fallback)}),encoding='utf-8')
    for exe,args,cwd in [(EXE,['--showApp','false'],EXE.parent),
                         (ROOT/'.venv/Scripts/pythonw.exe',['-m','ano.wallpaper'],ROOT)]:
        subprocess.Popen([str(exe),*args],cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    print('Activated the recovered galaxy wallpaper:',LIBRARY,'Backup:',backup)

if __name__=='__main__':main()
