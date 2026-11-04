"""Restore only Ano's own two wallpaper assets from pinned, verified local copies."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,time
from .reliability import atomic_json,read_json

FILES={'index.html':'Galaxy-Watch-Live.html','LivelyInfo.json':'Galaxy-Watch-LivelyInfo.json'}

class AssetGuard:
    def __init__(self,state,library,source=None,clock=time.time):
        self.state,self.library=Path(state),Path(library)
        self.source=Path(source or Path(__file__).parent/'assets');self.clock=clock;self.last=None
    def check(self):
        now=self.clock()
        if self.last is not None and 0<=now-self.last<30:return
        self.last=now;status='healthy';detail='Owned wallpaper assets verified.';restored=[]
        manifest=read_json(self.state/'wallpaper-assets.json')
        history=read_json(self.state/'wallpaper-integrity.policy.json')
        attempts=[t for t in history.get('attempts',[]) if isinstance(t,(int,float)) and 0<=now-t<3600]
        try:
            if set(manifest.get('sha256',{}))!=set(FILES):raise ValueError('Pinned asset manifest unavailable')
            for name,source in FILES.items():
                if (self.source/source).stat().st_size>1_000_000:raise ValueError('Canonical asset too large')
                trusted=(self.source/source).read_bytes()
                if hashlib.sha256(trusted).hexdigest()!=manifest['sha256'][name]:raise ValueError('Canonical asset differs from pinned version')
                target=self.library/name
                if target.exists() and target.stat().st_size<=1_000_000 and hashlib.sha256(target.read_bytes()).hexdigest()==manifest['sha256'][name]:continue
                if len(attempts)>=3:raise ValueError('Asset recovery limit reached; inspect repeated changes')
                attempts.append(now);atomic_json(self.state/'wallpaper-integrity.policy.json',{'attempts':attempts})
                if target.exists():
                    backup=self.state/'backups'/'wallpaper-integrity';backup.mkdir(parents=True,exist_ok=True)
                    # Preserve only small, known-owned assets; never copy arbitrary files.
                    if target.stat().st_size<=1_000_000:(backup/(str(int(now))+'-'+name)).write_bytes(target.read_bytes())
                temp=self.library/(name+'.ano-recovery.tmp');temp.write_bytes(trusted);os.replace(temp,target);restored.append(name)
            if restored:status='restored';detail='Restored verified local wallpaper assets: '+', '.join(restored)
        except (OSError,ValueError,TypeError,KeyError):
            status='attention';detail='Wallpaper integrity needs inspection; no unverified source was applied.'
        atomic_json(self.state/'wallpaper-integrity.health.json',{'at':datetime.now(timezone.utc).isoformat(),
            'state':status,'detail':detail,'restored':restored,'scope':'Owned wallpaper HTML and metadata only; three restores/hour.'})
        return restored
