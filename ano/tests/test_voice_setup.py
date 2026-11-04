from pathlib import Path
import tempfile
import unittest
import zipfile

from ano.voice_setup import MODEL_NAME, extract_model, model_ready


class ModelSetupTests(unittest.TestCase):
    def test_valid_model_and_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'model.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                for file in ('am/final.mdl', 'conf/model.conf', 'graph/HCLr.fst'):
                    package.writestr(f'{MODEL_NAME}/{file}', b'model fixture')
            self.assertTrue(model_ready(extract_model(archive, root / 'valid')))
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr(f'{MODEL_NAME}/../../outside.txt', b'bad')
            with self.assertRaises(ValueError):
                extract_model(archive, root / 'invalid')
            self.assertFalse((root / 'outside.txt').exists())
