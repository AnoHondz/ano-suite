import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QImage

from ano.local_images import RELEASE, download, ready
from ano.plugins.local_images import LocalImages
from ano.tools import ToolError, Toolbox


class LocalImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / RELEASE
        self.runtime.mkdir()
        (self.runtime / 'sd-cli.exe').write_bytes(b'fixture')
        (self.root / 'sdxs.safetensors').write_bytes(b'fixture')
        self.output = self.root / 'pictures'
        self.launch = Mock()
        self.process = Mock(returncode=0)
        self.process.poll.return_value = 0
        self.launch.return_value = self.process
        self.plugin = LocalImages(root=lambda: self.root, folder=lambda: self.output,
                                  open_path=None, launch=self.launch, sample=lambda:(60,4*1024**3))

    def test_high_memory_defers_generation_without_launching_or_paid_fallback(self):
        self.plugin.sample=lambda:(94,700*1024**2)
        with self.assertRaisesRegex(ToolError,'waiting for free RAM'):
            self.plugin.generate_image('Synthetic fixture')
        self.launch.assert_not_called()

    def create_image(self, command, **kwargs):
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0xff336699)
        self.assertTrue(image.save(command[command.index('-o') + 1]))
        return self.process

    def test_generation_saves_verified_png_and_passes_prompt_as_one_argument(self):
        self.launch.side_effect = self.create_image
        prompt = 'a fox; $(do not run this) "with trees"'
        text, note = self.plugin.generate_image(prompt, 'portrait')
        command = self.launch.call_args.args[0]
        self.assertEqual(command[command.index('-p') + 1], prompt)
        self.assertEqual(command[command.index('-W') + 1], '384')
        self.assertEqual(command[command.index('--steps') + 1], '1')
        self.assertNotIn('shell', self.launch.call_args.kwargs)
        self.assertEqual(len(list(self.output.glob('*.png'))), 1)
        self.assertIn('no API credits', text)
        self.assertIn('local image', note)
        self.assertEqual(list(self.root.glob('image-job-*')), [])

    def test_failure_and_missing_png_cannot_claim_success(self):
        for code in (0, 1):
            with self.subTest(code=code):
                self.process.returncode = code
                self.process.poll.return_value = code
                with self.assertRaises(ToolError):
                    self.plugin.generate_image('fox')
                self.assertFalse(self.output.exists())

    def test_pause_terminates_process_and_removes_temporary_output(self):
        self.process.poll.return_value = None
        self.plugin.allowed = Mock(side_effect=[True, False])
        with self.assertRaisesRegex(ToolError, 'cancelled'):
            self.plugin.generate_image('fox')
        self.process.kill.assert_called_once()
        self.process.wait.assert_called_once()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('image-job-*')), [])

    def test_timeout_terminates_process_without_fallback(self):
        self.process.poll.return_value = None
        self.plugin.timeout = 0
        with self.assertRaisesRegex(ToolError, 'timed out'):
            self.plugin.generate_image('fox')
        self.process.kill.assert_called_once()
        self.assertFalse(self.output.exists())

    def test_missing_weights_or_invalid_input_never_launches_runtime(self):
        (self.root / 'sdxs.safetensors').unlink()
        self.assertFalse(ready(self.root))
        for prompt, shape in [('fox', 'square'), ('', 'square'), ('fox', 'giant')]:
            with self.subTest(prompt=prompt, shape=shape), self.assertRaises(ToolError):
                self.plugin.generate_image(prompt, shape)
        self.launch.assert_not_called()

    def test_toolbox_pause_blocks_local_image_tool(self):
        tools = Toolbox(lambda: [], allowed=lambda: False, plugins=[self.plugin])
        self.assertTrue(tools.run('generate_image', {'prompt': 'fox'})[2])
        self.launch.assert_not_called()

    def test_failed_checksum_does_not_install_partial_model(self):
        destination = self.root / 'download.safetensors'
        with patch('ano.local_images.urllib.request.urlopen', return_value=io.BytesIO(b'bad download')):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                download('https://example.org/model', destination, '0' * 64, progress=lambda text: None)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_valid_download_is_verified_and_reused_without_network(self):
        destination = self.root / 'download.safetensors'
        expected = hashlib.sha256(b'fixture model').hexdigest()
        with patch('ano.local_images.urllib.request.urlopen', return_value=io.BytesIO(b'fixture model')):
            download('https://example.org/model', destination, expected, progress=lambda text: None)
        with patch('ano.local_images.urllib.request.urlopen') as fetch:
            download('https://example.org/model', destination, expected)
        fetch.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'fixture model')
