from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import tempfile
import unittest
from unittest.mock import Mock

from ano.chart_analysis import indicator_snapshot, ema, describe_snapshot
from ano.plugins.paper_trading import PaperTrading
from ano.plugins.workshop import Workshop


class ChartAnalysisTests(unittest.TestCase):
    def rows(self):
        start=int(datetime(2026,10,28,9,30,tzinfo=ZoneInfo('America/New_York')).timestamp())
        return [dict(t=start+i*60,o=100,h=101,l=99,c=100,v=10) for i in range(60)]

    def snapshot(self,rows):
        return indicator_snapshot(rows,now=self.rows()[-1]['t']+60)

    def test_known_constant_series_and_rth_volume(self):
        result=self.snapshot(self.rows())
        self.assertEqual((result['ema20'],result['ema50'],result['atr14'],result['rsi14']),(100,100,2,50))
        self.assertEqual(result['rth_vwap'],100)
        self.assertEqual((result['opening_range_high'],result['opening_range_low']),(101,99))
        self.assertFalse(result['execution_authorized'])

    def test_directional_rsi_and_ema_recurrence(self):
        self.assertEqual(ema([1,2,3],3),2.25)
        for sign,expected in [(1,100),(-1,0)]:
            rows=self.rows()
            for i,row in enumerate(rows):
                row.update(o=100+sign*i,c=100+sign*i,h=101+sign*i,l=99+sign*i)
            self.assertEqual(self.snapshot(rows)['rsi14'],expected)

    def test_human_report_names_time_and_never_presents_stale_data_as_live(self):
        report=self.snapshot(self.rows())|{'symbol':'QQQ','stale':True,'rth_vwap':None}
        text=describe_snapshot(report)
        self.assertIn('2026-10-28 10:30 New York',text)
        self.assertIn('Historical/stale',text)
        self.assertIn('RTH VWAP: unavailable',text)
        self.assertIn('RSI 14: 50.0',text)
        self.assertNotIn('"ema20"',text)

    def test_volume_and_session_start_are_required_for_vwap(self):
        rows=self.rows();del rows[1]['v']
        self.assertIsNone(self.snapshot(rows)['rth_vwap'])
        self.assertIsNone(self.snapshot(self.rows()[1:])['rth_vwap'])
        rows=self.rows();rows[0]['v']=-10
        with self.assertRaises(ValueError):self.snapshot(rows)

    def test_gapped_opening_range_is_not_claimed_complete_and_forming_bar_excluded(self):
        rows=self.rows();del rows[4]
        self.assertIsNone(self.snapshot(rows)['opening_range_high'])
        self.assertEqual(self.snapshot(rows)['missing_minutes'],1)
        rows=self.rows()+[dict(t=self.rows()[-1]['t']+60,o=1000,h=1001,l=999,c=1000,v=100)]
        self.assertEqual(self.snapshot(rows)['ema20'],100)
        with self.assertRaises(ValueError):self.snapshot(self.rows()[:30])

    def test_plugin_uses_actual_feed_path_and_exports_the_compiled_indicator(self):
        with tempfile.TemporaryDirectory() as temp:
            feed=Mock();feed.candles.return_value={'candles':self.rows()}
            plugin=PaperTrading(Workshop(Path(temp),None),clock=lambda:self.rows()[-1]['t']+60,
                key_loader=lambda:('fixture','secret'),feed_factory=lambda *keys:feed)
            text,note=plugin.run('analyze_paper_indicators',{'symbol':'QQQ'})
            result=json.loads(text)
            self.assertEqual(result['rsi14'],50)
            feed.candles.assert_called_once_with('QQQ')
            saved=json.loads(plugin.run('ict_tradingview_indicator',{'strategy':'chart assistant'})[0])
            self.assertIn('Ano Chart Assistant',Path(saved['path']).read_text())
            self.assertIn('no orders',note)
            setups=json.loads(plugin.run('ict_tradingview_indicator',{'strategy':'ano setups'})[0])
            pine=Path(setups['path']).read_text(encoding='utf-8')
            # Same sessions and kill zones as the forex and stock bots.
            for clock in ('"0900-1450", "Asia/Tokyo"','"0800-1650", "Europe/London"','"0930-1510"','"0930-1600"','"1330-1500"'):
                self.assertIn(clock,pine)
            self.assertIn('alertcondition(ictLongNow',pine);self.assertIn('1-minute',setups['how_to_add'])


if __name__=='__main__':unittest.main()
