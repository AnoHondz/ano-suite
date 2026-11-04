from datetime import datetime, timezone
from pathlib import Path
import unittest

from ano.email_theme import premium_frame, THEME_VERSION
from ano.sports_reports import frame


class EmailThemeTests(unittest.TestCase):
    def test_premium_markup_is_responsive_escaped_and_preserves_evidence(self):
        html=premium_frame('<script>', '<h2>NBA</h2><table><tr><th>Game</th></tr>'
                           '<tr><td colspan="3">SKIP — no qualified pick. Source observed 12:00.</td></tr></table>',
                           when=datetime(2026,10,29,15,tzinfo=timezone.utc),token='fixture')
        self.assertIn(THEME_VERSION,html)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('colspan="3"',html)
        self.assertIn('SKIP — no qualified pick',html)
        self.assertIn('Source observed 12:00',html)
        self.assertIn('table-layout:fixed',html)
        self.assertIn('max-width:520px',html)
        self.assertIn('Leave the subject unchanged',html)
        self.assertEqual(html.count('cid:ano-logo'),1)

    def test_ano_report_path_uses_premium_theme(self):
        self.assertIn(THEME_VERSION,frame('your picks','<p>No qualified picks</p>'))

    def test_scorpred_theme_copy_is_identical_when_installed(self):
        path=Path.home()/'Documents/ScorPred-Surface-2026-10-24/ScorPred/services/ano_email_theme.py'
        if not path.exists():self.skipTest('Sibling ScorPred not installed')
        self.assertEqual(path.read_bytes(),(Path(__file__).parents[1]/'ano/email_theme.py').read_bytes())
