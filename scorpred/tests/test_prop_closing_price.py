from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from services.prop_evidence_store import EvidenceStore
from services.prop_closing_price import last_observed_price


class ClosingTests(unittest.TestCase):
    def test_exact_identity_line_and_before_tipoff_receipt_are_required(self):
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'prices.sqlite3')
            row={'quote':{'provider_event_id':'book-event','provider_player':'player','bookmaker_key':'book',
                         'market':'player_points','line':20.5,'side':'over','decimal_odds':2,
                         'fetched_at':'2026-10-28T19:00:00Z'}}
            quote={'player':'player','bookmaker_key':'book','market':'player_points','line':20.5,'over_decimal':1.8}
            def save(q,published,received):store.append('basketball_nba:book-event','sportsbook_quote','fixture',published,q,received_at=received)
            save(quote,'2026-10-28T19:50:00Z','2026-10-28T19:51:00Z')
            save({**quote,'over_decimal':1.1},'2026-10-28T19:58:00Z','2026-10-28T20:01:00Z')
            save({**quote,'line':21.5},'2026-10-28T19:59:00Z','2026-10-28T19:59:30Z')
            result=last_observed_price(store,row,'2026-10-28T20:00:00Z')
            self.assertEqual(result['last_observed_decimal_odds'],1.8)
            self.assertAlmostEqual(result['initial_to_last_price_ratio'],2/1.8-1)
            row['quote']['bookmaker_key']='other'
            self.assertEqual(last_observed_price(store,row,'2026-10-28T20:00:00Z')['status'],'unavailable')

    def test_stale_prices_and_missing_provider_link_do_not_become_closing_prices(self):
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'prices.sqlite3')
            self.assertEqual(last_observed_price(store,{},'2026-10-28T20:00:00Z')['status'],'unavailable')
            quote={'player':'p','bookmaker_key':'b','market':'player_points','line':10.5,'over_decimal':1.8}
            store.append('basketball_nba:e','sportsbook_quote','fixture','2026-10-28T19:00:00Z',quote,
                         received_at='2026-10-28T19:01:00Z')
            row={'quote':{'provider_event_id':'e','provider_player':'p','bookmaker_key':'b','market':'player_points',
                          'line':10.5,'side':'over','decimal_odds':2,'fetched_at':'2026-10-28T18:00:00Z'}}
            self.assertEqual(last_observed_price(store,row,'2026-10-28T20:00:00Z')['status'],'unavailable')
