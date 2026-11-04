from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from lxml import etree
from ano.decks import build_deck, slide_texts
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop
from ano.tools import ToolError


class DeckTests(unittest.TestCase):
    def test_arbitrary_slide_count_and_exact_xml_safe_text(self):
        slides = [{'title': f'Step {i}', 'bullets': ['A < B & C', 'Exact point']} for i in range(7)]
        payload = build_deck('Supplied <Title>', slides, 'Exact subtitle')
        texts = slide_texts(payload)
        self.assertEqual(len(texts), 8)
        self.assertIn('Supplied <Title>', texts[0])
        for i, text in enumerate(texts[1:]):
            self.assertIn(f'Step {i}', text)
            self.assertIn('A < B & C', text)
            self.assertNotIn('{{', ' '.join(text))
        with ZipFile(BytesIO(payload)) as archive:
            for name in archive.namelist():
                if name.endswith(('.xml', '.rels')):
                    etree.fromstring(archive.read(name))
            self.assertFalse(any('notesSlides/' in n for n in archive.namelist()))

    def test_oversized_content_refused_instead_of_silent_overflow(self):
        for slides in ([], [{'title': 'Title', 'bullets': ['x'] * 5}],
                       [{'title': 'Title', 'bullets': ['x' * 121]}],
                       [{'title': 'Title', 'bullets': ['Text'], 'url': 'https://example.com'}]):
            with self.assertRaises(ToolError):
                build_deck('Title', slides)

    def test_permission_and_existing_file_preserve_user_content(self):
        with TemporaryDirectory() as directory:
            workshop = Workshop(Path(directory), None)
            plugin = Artifacts(workshop)
            result, _ = plugin.create_presentation('Deck', 'Supplied deck', [{'title': 'First', 'bullets': ['One fact']}])
            path = Path(json.loads(result)['path'])
            original = path.read_bytes()
            with self.assertRaises(ToolError):
                plugin.create_presentation('Deck', 'Other', [{'title': 'Other', 'bullets': ['New']}])
            self.assertEqual(original, path.read_bytes())
            workshop.allowed = lambda: False
            with self.assertRaises(ToolError):
                plugin.create_presentation('Blocked', 'Other', [{'title': 'Other', 'bullets': ['New']}])
            self.assertFalse((Path(directory) / 'Blocked.pptx').exists())
