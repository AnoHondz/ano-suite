import json
from pathlib import Path
import re
import tempfile
import unittest

from ano.paper_analysis import analyze_candles
from ano.plugins.paper_trading import PaperTrading
from ano.plugins.workshop import Workshop


class PaperAnalysisTests(unittest.TestCase):
    def candles(self):
        return [dict(t=1000+i*60,o=10,h=h,l=l,c=10) for i,(h,l) in enumerate(
            [(11,9),(12,8),(15,5),(12,8),(11,9),(16,8)])]

    def test_swings_only_appear_after_confirmation_and_sweeps_afterward(self):
        rows = self.candles()
        self.assertEqual(analyze_candles(rows[:4], now=1240)['swings'], [])
        report = analyze_candles(rows, now=1360)
        high = next(s for s in report['swings'] if s['kind']=='high')
        self.assertEqual(high['confirmed_at'],1300)
        self.assertEqual(report['sweeps'][0]['confirmed_at'],1360)
        self.assertFalse(report['execution_authorized'])

    def test_fvg_orientation_and_forming_candle_exclusion(self):
        rows = [dict(t=1000,o=10,h=11,l=9,c=10), dict(t=1060,o=12,h=15,l=11,c=14),
                dict(t=1120,o=14,h=16,l=13,c=15), dict(t=1180,o=14,h=16,l=13,c=15)]
        result = analyze_candles(rows, now=1190)
        self.assertEqual(result['forming_candles_ignored'],1)
        self.assertEqual(result['fair_value_gaps'][0]['low'],'11')
        self.assertEqual(result['fair_value_gaps'][0]['high'],'13')
        self.assertEqual(result['fair_value_gaps'][0]['direction'],'bullish')
        reflected = [dict(t=r['t'],o=30-r['o'],h=30-r['l'],l=30-r['h'],c=30-r['c']) for r in rows]
        self.assertEqual(analyze_candles(reflected,now=1190)['fair_value_gaps'][0]['direction'],'bearish')

    def test_invalid_numeric_data_and_timestamp_gaps_are_rejected(self):
        for change in ({'h':float('nan')},{'t':True},{'h':1},{'t':3000},{'t':1010}):
            rows=self.candles(); rows[1]=dict(rows[1],**change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                analyze_candles(rows,now=1360)

    def test_a_close_beyond_a_level_breaks_it_instead_of_sweeping_it(self):
        rows=self.candles()
        rows[5]=dict(rows[5],c=15.5,o=10)  # wick to 16 and close above the 15 swing high
        report=analyze_candles(rows,now=1360)
        high=next(s for s in report['swings'] if s['kind']=='high')
        self.assertEqual(high['broken_at'],1360)
        self.assertEqual(report['sweeps'],[])
        self.assertNotIn(high,report['open_levels'])

    def test_gaps_fill_and_missing_bars_never_make_a_gap(self):
        rows=[dict(t=1000,o=10,h=11,l=9,c=10), dict(t=1060,o=12,h=15,l=11,c=14),
              dict(t=1120,o=14,h=16,l=13,c=15), dict(t=1180,o=14,h=14,l=10.5,c=11)]
        report=analyze_candles(rows,now=1300)
        self.assertEqual(report['fair_value_gaps'][0]['filled_at'],1240)
        self.assertEqual(report['open_fair_value_gaps'],[])
        # The same prices with a missing minute between the first two bars: no three back-to-back bars, no gap.
        holed=[dict(r,t=r['t']+(60 if i else 0)) for i,r in enumerate(rows[:3])]
        holed=[holed[0]]+[dict(r,t=r['t']) for r in holed[1:]]
        report=analyze_candles(holed,now=1400)
        self.assertEqual(report['missing_bars'],1)
        self.assertEqual(report['fair_value_gaps'],[])

    def test_ties_are_not_swings_and_stale_data_is_labeled(self):
        rows=[dict(t=1000+i*60,o=10,h=11,l=9,c=10) for i in range(6)]
        report=analyze_candles(rows,now=2000)
        self.assertEqual(report['swings'],[])
        self.assertTrue(report['stale'])

    def test_plugin_writes_actual_chart_and_evidence_without_order_capability(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin=PaperTrading(Workshop(Path(temp),None),clock=lambda:1360)
            text,_=plugin.run('analyze_paper_candles',dict(symbol='TEST:INDEX',source='synthetic fixture',candles=self.candles()))
            result=json.loads(text)
            self.assertTrue(Path(result['chart']).read_text().startswith('<svg'))
            self.assertEqual(json.loads(Path(result['evidence']).read_text())['symbol'],'TEST:INDEX')
            self.assertFalse(result['report']['source_verified'])
            # Model-supplied candles can never claim to be a verified feed.
            text,_=plugin.run('analyze_paper_candles',dict(symbol='TEST',source='x',candles=self.candles(),feed_verified=True))
            self.assertFalse(json.loads(text)['report']['source_verified'])
            self.assertEqual([d['name'] for d in plugin.definitions()],
                             ['smc_analysis','silver_bullet_setups','analyze_paper_candles','paper_connection_status','analyze_paper_watchlist',
                              'ict_setups','ict_backtest','ict_tradingview_indicator','forex_setups','analyze_paper_indicators'])
            self.assertFalse([d for d in plugin.definitions() if re.search(r'order|execute|submit|buy|sell', d['name'])])


if __name__=='__main__': unittest.main()
