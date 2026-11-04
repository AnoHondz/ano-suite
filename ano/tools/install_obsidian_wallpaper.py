"""Install the versioned, local Obsidian wallpaper and retain the old design."""
import ctypes,json,os,shutil
from datetime import datetime,timezone
from pathlib import Path
from ano.paths import data_directory
from ano.wallpaper import LIBRARY,ROOT

def main():
    state=data_directory();source=Path(os.environ['LOCALAPPDATA'])/'Lively Wallpaper/Library/wallpapers/Obsidian-Code/background.png'
    assert source.is_file()
    backup=state/'backups'/('wallpaper-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True)
    for name in ('wallpaper.json','Ano-Wallpaper.jpg'):
        if (state/name).is_file():shutil.copy2(state/name,backup/name)
    LIBRARY.mkdir(parents=True,exist_ok=True)
    if (LIBRARY/'index.html').exists():shutil.copy2(LIBRARY/'index.html',backup/'index.html')
    shutil.copy2(ROOT/'ano/assets/Obsidian-Code-Live.html',LIBRARY/'index.html')
    shutil.copy2(source,LIBRARY/'background.png')
    (LIBRARY/'LivelyInfo.json').write_text(json.dumps({'AppVersion':'2.2.1.0','Title':'Ano / Obsidian Code',
        'Desc':'Live local stations, original-risk evidence and an observation crawler. No AI requests.',
        'Author':'Ano','Type':1,'FileName':'index.html','IsAbsolutePath':False}),encoding='utf-8')
    fallback=state/'Ano-Wallpaper.png';shutil.copy2(source,fallback)
    (state/'wallpaper.json').write_text(json.dumps({'enabled':True,'name':'Ano / Obsidian Code','fallback':str(fallback),'library':str(LIBRARY)}),encoding='utf-8')
    assert ctypes.windll.user32.SystemParametersInfoW(20,0,str(fallback),3)
    print('Installed:',LIBRARY,'Backup:',backup)

if __name__=='__main__':main()
