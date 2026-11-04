"""Keep mocked model tests independent of the user's live RAM and inference lock."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

def isolate_inference(test):
    from ano.inference_gate import acquire
    folder=TemporaryDirectory();test.addCleanup(folder.cleanup)
    lock=Path(folder.name)/'inference.lock'
    fixture=patch('ano.inference_gate.acquire',side_effect=lambda *args,**kwargs:
        acquire(lock,sample=lambda:(50,8*1024**3)))
    fixture.start();test.addCleanup(fixture.stop)
