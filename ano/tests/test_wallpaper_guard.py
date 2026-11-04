import hashlib,json,tempfile
from pathlib import Path
from unittest import TestCase
from ano.wallpaper_guard import AssetGuard,FILES

class WallpaperGuardTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'state';self.source=self.root/'source';self.target=self.root/'target'
        for path in (self.state,self.source,self.target):path.mkdir()
        self.now=1000;hashes={}
        for name,source in FILES.items():
            body=('Trusted '+name).encode();(self.source/source).write_bytes(body);(self.target/name).write_bytes(body)
            hashes[name]=hashlib.sha256(body).hexdigest()
        (self.state/'wallpaper-assets.json').write_text(json.dumps({'sha256':hashes}))
        self.guard=AssetGuard(self.state,self.target,self.source,clock=lambda:self.now)
    def test_corrupt_owned_asset_restored_with_backup(self):
        (self.target/'index.html').write_text('corrupt')
        self.assertEqual(self.guard.check(),['index.html'])
        self.assertEqual((self.target/'index.html').read_bytes(),(self.source/FILES['index.html']).read_bytes())
        backups=list((self.state/'backups/wallpaper-integrity').glob('*'))
        self.assertEqual(backups[0].read_text(),'corrupt')
    def test_corrupt_source_never_overwrites_target(self):
        (self.source/FILES['index.html']).write_text('untrusted changed source')
        (self.target/'index.html').write_text('retained last file')
        self.guard.check()
        self.assertEqual((self.target/'index.html').read_text(),'retained last file')
        self.assertEqual(json.loads((self.state/'wallpaper-integrity.health.json').read_text())['state'],'attention')
    def test_three_restorations_limit_survives_new_guard_instance(self):
        for _ in range(3):
            (self.target/'index.html').write_text('corrupt');self.guard.check();self.now+=31
        (self.target/'index.html').write_text('fourth change')
        guard=AssetGuard(self.state,self.target,self.source,clock=lambda:self.now);guard.check()
        self.assertEqual((self.target/'index.html').read_text(),'fourth change')
        self.assertEqual(json.loads((self.state/'wallpaper-integrity.health.json').read_text())['state'],'attention')
    def test_manifest_cannot_select_arbitrary_paths(self):
        other=self.root/'untouched.txt';other.write_text('keep')
        (self.state/'wallpaper-assets.json').write_text(json.dumps({'sha256':{'../untouched.txt':'x'}}))
        self.guard.check();self.assertEqual(other.read_text(),'keep')
