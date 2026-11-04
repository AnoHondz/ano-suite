from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from services.prop_evidence_store import EvidenceStore
from services.prop_markets import _preserve_quotes


class QuoteEvidenceTests(TestCase):
    def test_real_adapter_contract_preserves_price_book_and_original_receipt(self):
        with TemporaryDirectory() as folder:
            store = EvidenceStore(Path(folder) / 'quotes.sqlite3')
            quote = {'player': 'exampleplayer', 'market': 'player_points', 'line': 20.5,
                     'over_decimal': 1.91, 'under_decimal': 1.95,
                     'bookmaker': 'Example Book', 'bookmaker_key': 'example',
                     'updated_at': '2026-10-29T15:00:00Z', 'fetched_at': '2026-10-29T15:01:00Z',
                     'expires_at': '2026-10-29T15:06:00Z'}
            _preserve_quotes('basketball_nba', 'g1', datetime(2026,10,29,20,tzinfo=UTC), [quote], store)
            before = store.as_of('basketball_nba:g1', '2026-10-29T15:00:30Z')
            self.assertEqual(before, [])
            after = store.as_of('basketball_nba:g1', '2026-10-29T15:02:00Z')
            self.assertEqual(after[0]['payload']['over_decimal'], 1.91)
            self.assertEqual(after[0]['payload']['under_decimal'], 1.95)
            self.assertEqual(after[0]['received_at'], '2026-10-29T15:01:00+00:00')
            self.assertEqual(after[0]['payload']['bookmaker_key'], 'example')
