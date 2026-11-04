import unittest
from ano.trading_notifications import friendly,should_notify
class NotificationTests(unittest.TestCase):
    def test_receipts_read_as_plain_sentences(self):
        self.assertEqual(friendly('QQQ smc_retest_v1: 106 shares long FILLED at 695.868773 (verified; order filled).'),
            'Paper trade opened: bought 106 QQQ shares at $695.87 (about $73,762 of practice money), using the SMC retest setup. Alpaca confirmed it.')
        self.assertIn('sold short 50 QQQ shares',friendly('QQQ ict_2022_v3: 50 shares short FILLED at 700 (verified; order filled).'))
        self.assertIn('bought 263 QQQ shares',friendly('QQQ ema_cross_v1: 263 shares FILLED at 713.66 (verified; order filled).'))  # older receipts
        self.assertEqual(friendly('QQQ smc_retest_v1: CLOSED by its target at 700.1234 (verified).'),
            'Paper trade closed: the QQQ SMC retest trade hit its profit target at $700.12.')
        self.assertEqual(friendly('EUR_USD / turtle soup [new_york]: 1000 units SHORT FILLED at 1.08452 (verified; OANDA demo trade 77).'),
            'Forex practice trade opened: sold 1,000 units of EUR/USD at 1.08452, using the turtle soup setup in the New York session. OANDA confirmed it.')
        self.assertEqual(friendly('Paper session summary 2026-10-29'),'Paper session summary 2026-10-29')
    def test_verified_entry_and_summary_visible(self):
        for text in ('Paper session summary 2026-10-29','QQQ ema_cross_v1: 263 shares FILLED at 713.66 (verified; order filled).'):
            self.assertTrue(should_notify(text),text)
    def test_other_events_remain_in_journal(self):
        for text in ('QQQ: NO TRADE - stale','QQQ: PAPER bracket acknowledged','QQQ: CLOSED - verified','QQQ: FILLED at 100','Paper management failed','Daily risk halt'):
            self.assertFalse(should_notify(text),text)
