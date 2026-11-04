import json
from pathlib import Path
import tempfile
import unittest

from pptx import Presentation

from ano.local_profile import requested_slide_total
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop
from ano.tools import ToolError

SLIDE = {'title': 'Point', 'bullets': ['a']}


class PresentationTotalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workshop = Workshop(Path(self.tmp.name) / 'AnoWorkspace', None)
        self.workshop.root.mkdir(parents=True)
        self.artifacts = Artifacts(self.workshop)

    def slides(self, name):
        return len(Presentation(str(self.workshop.root / name)).slides)

    def test_too_many_slides_are_trimmed_to_the_total_and_reported(self):
        slides = [dict(SLIDE, title=f'Point {n}') for n in range(1, 4)]
        receipt, _ = self.artifacts.create_presentation('Pitch', 'Ano', slides, total_slides=3)
        self.assertEqual(self.slides('Pitch.pptx'), 3)
        self.assertIn('Point 3', json.loads(receipt)['trimmed_to_requested_total'])

    def test_too_few_are_refused_with_the_count_to_use(self):
        with self.assertRaises(ToolError) as caught:
            self.artifacts.create_presentation('Pitch', 'Ano', [SLIDE], total_slides=4)
        self.assertIn('Supply exactly 3 content slide(s)', str(caught.exception))
        self.assertFalse(list(self.workshop.root.glob('*.pptx')))

    def test_the_right_total_is_exactly_what_is_saved(self):
        self.artifacts.create_presentation('Pitch', 'Ano', [SLIDE] * 2, total_slides=3)
        self.assertEqual(self.slides('Pitch.pptx'), 3)

    def test_near_miss_slides_are_tidied_not_refused(self):
        slides = [{'title': 'Many points', 'bullets': ['one', 'two', '', 'three', 'four', 'five', 'six']},
                  {'title': 'Block of text', 'bullets': '- first line\n- second line'},
                  {'title': 'Long point', 'bullets': ['word ' * 40]}]
        receipt, _ = self.artifacts.create_presentation('Tidy', 'Ano', slides)
        self.assertEqual(self.slides('Tidy.pptx'), 4)
        self.assertIn('slide 1: kept the first 4 of 6 points', json.loads(receipt)['adjusted'])
        with self.assertRaises(ToolError):
            self.artifacts.create_presentation('Empty', 'Ano', [{'title': 'Nothing', 'bullets': []}])

    def test_the_total_is_read_from_the_users_words(self):
        self.assertEqual(requested_slide_total('make a deck with exactly 3 slides in total including the title slide'), 3)
        self.assertEqual(requested_slide_total('a five-slide pitch'), 5)
        self.assertEqual(requested_slide_total('make a 10 slide deck'), 10)
        self.assertIsNone(requested_slide_total('3 slides plus a title slide'))
        self.assertIsNone(requested_slide_total('make a pitch deck about Ano'))


if __name__ == '__main__':
    unittest.main()
