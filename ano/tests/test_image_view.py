from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, PngImagePlugin
from ano.tools import Toolbox
from ano.tool_media import ToolImage


class ImageViewTests(unittest.TestCase):
    def test_actual_pixels_bounded_metadata_removed_source_preserved(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            image = Image.new('RGBA', (2000, 1000), (255, 0, 0, 128))
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('Synthetic-private-fixture', 'Metadata must not leave decoder')
            path = root / 'fixture.png'
            image.save(path, pnginfo=metadata)
            before = path.read_bytes()
            toolbox = Toolbox(lambda: [root])
            output, _, failed = toolbox.run('view_image_file', {'path': str(path)})
            self.assertFalse(failed)
            self.assertIsInstance(output, ToolImage)
            self.assertEqual(path.read_bytes(), before)
            with Image.open(BytesIO(output.data)) as viewed:
                self.assertEqual(viewed.size, (1536, 768))
                self.assertEqual(viewed.mode, 'RGB')
                self.assertTrue(all(abs(a-b) < 5 for a,b in zip(viewed.getpixel((500,300)), (255,127,127))))
                self.assertNotIn('Synthetic-private-fixture', viewed.info)
            self.assertNotIn(b'Metadata must not leave decoder', output.data)

    def test_outside_allowed_folder_and_wrong_types_do_not_decode(self):
        with TemporaryDirectory() as folder, TemporaryDirectory() as outside:
            root = Path(folder)
            secret = Path(outside) / 'outside.png'
            Image.new('RGB', (5, 5)).save(secret)
            toolbox = Toolbox(lambda: [root])
            self.assertTrue(toolbox.run('view_image_file', {'path': str(secret)})[2])
            (root / 'file.txt').write_text('not an image')
            self.assertTrue(toolbox.run('view_image_file', {'path': 'file.txt'})[2])
            (root / 'bad.png').write_bytes(b'not valid image bytes')
            self.assertTrue(toolbox.run('view_image_file', {'path': 'bad.png'})[2])

    def test_oversized_pixel_image_refused(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('1', (4001, 4000)).save(root / 'large.png')
            result, _, failed = Toolbox(lambda: [root]).run('view_image_file', {'path': 'large.png'})
            self.assertTrue(failed)
            self.assertIn('16-million-pixel', result)


if __name__ == '__main__':
    unittest.main()
