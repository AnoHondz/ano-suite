import unittest
from datetime import datetime
from ano.ict import NY
from ano.turtle_soup import analyze


class TurtleSoupTests(unittest.TestCase):
    def bars(self):
        start=int(datetime(2026,10,29,9,30,tzinfo=NY).timestamp())
        rows=[dict(t=start+i*60,o=100,h=101,l=99,c=100) for i in range(21)]
        rows[10]['h']=102;rows[10]['l']=98
        rows[-1].update(h=103,c=101.5)
        return rows,start+21*60

    def test_closed_failed_high_breakout_is_short_with_explicit_research_plan(self):
        rows,now=self.bars();result=analyze(rows,now)
        self.assertEqual(result['setups'][0]['direction'],'short')
        self.assertAlmostEqual(result['setups'][0]['stop'],103.01)
        self.assertLess(result['setups'][0]['target'],101.5)

    def test_missing_forming_stale_and_recent_extreme_never_signal(self):
        rows,now=self.bars()
        self.assertEqual(analyze(rows,now-1)['setups'],[])
        self.assertEqual(analyze(rows[:8]+rows[9:],now)['setups'],[])
        self.assertEqual(analyze(rows,now+120)['setups'],[])
        rows[-2]['h']=102
        self.assertEqual(analyze(rows,now)['setups'],[])

    def test_failed_low_breakout_is_long_and_two_sided_sweep_is_ambiguous(self):
        rows,now=self.bars();rows[-1].update(h=101,l=97,c=98.5,o=100)
        self.assertEqual(analyze(rows,now)['setups'][0]['direction'],'long')
        rows[-1].update(h=103,c=100)
        self.assertEqual(analyze(rows,now)['setups'],[])

    def test_nonfinite_or_inconsistent_prices_cannot_be_a_setup(self):
        rows,now=self.bars();rows[-1]['c']=float('nan')
        self.assertEqual(analyze(rows,now)['setups'],[])
