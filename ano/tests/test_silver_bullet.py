from datetime import datetime
import unittest
from unittest.mock import patch
from ano import ict, silver_bullet as sb


def at(h, m):
    return int(datetime(2026,10,28,h,m,tzinfo=ict.NY).timestamp())


def bars():
    return [dict(t=at(9,30)+i*60,o=110-i*.01,h=110.1-i*.01,
                 l=109.9-i*.01,c=110-i*.01) for i in range(90)]


def candidate():
    return dict(direction='short',liquidity='pre-market high',swept_at=ict.stamp(at(10,3)),
                decided_at=ict.stamp(at(10,6)),mss_at=ict.stamp(at(10,5)),
                fvg_at=ict.stamp(at(10,6)),entry=109.4,stop=110,target=108,rr=2.33,
                status='win',r=2.33,entered_at='old',_entered_t=1)


class SilverBulletTests(unittest.TestCase):
    def analyze(self, rows=None, setup=None, now=None):
        day=dict(date='2026-10-28',setups=[setup or candidate()],amd=None,rejected={})
        with patch.object(ict,'analyze',return_value=[day]):
            return sb.analyze(rows if rows is not None else bars(),now=now or at(10,10))

    def test_closed_15m_bias_and_no_future_bars(self):
        rows=bars()
        self.assertEqual(sb.direction(rows,at(10,3)),'short')
        with patch.object(ict,'analyze',return_value=[]) as scan:
            sb.analyze(rows,now=at(10,10))
        self.assertTrue(all(b['t']+60<=at(10,10) for b in scan.call_args.args[0]))

    def test_missing_bias_bar_means_reject(self):
        report=self.analyze(rows=[b for b in bars() if b['t']!=at(9,40)])
        self.assertFalse(report['days'][0]['setups'])

    def test_sweep_before_window_rejected(self):
        s=candidate();s['swept_at']=ict.stamp(at(9,59))
        self.assertFalse(self.analyze(setup=s)['days'][0]['setups'])

    def test_decision_at_last_minute_has_no_entry_window(self):
        s=candidate();s['decided_at']=ict.stamp(at(10,59))
        self.assertFalse(self.analyze(setup=s)['days'][0]['setups'])

    def test_unnamed_swing_not_accepted(self):
        s=candidate();s['liquidity']='swing high'
        self.assertFalse(self.analyze(setup=s)['days'][0]['setups'])

    def test_old_outcome_is_recomputed(self):
        s=self.analyze()['days'][0]['setups'][0]
        self.assertNotEqual(s.get('_entered_t'),1)
        self.assertNotEqual(s.get('entered_at'),'old')

    def test_no_entry_on_confirmation_bar_or_after_window(self):
        s=candidate();s['decided_at']=ict.stamp(at(10,58))
        rows=[dict(t=at(10,58),o=109.4,h=110,l=108,c=109),
              dict(t=at(11,0),o=109.4,h=110,l=108,c=109)]
        result=sb.outcome(s,rows,at(11,0),at(11,1))
        self.assertEqual(result['status'],'expired')
        self.assertNotIn('_entered_t',result)

    def test_same_bar_stop_and_target_is_loss(self):
        s=candidate()
        r=sb.outcome(s,[dict(t=at(10,7),o=109.4,h=111,l=107,c=109)],at(11,0),at(10,8))
        self.assertEqual(r['r'],-1)

    def test_entry_bar_target_not_assumed_after_fill(self):
        r=sb.outcome(candidate(),[dict(t=at(10,7),o=108.2,h=109.5,l=107,c=109)],at(11,0),at(10,8))
        self.assertIsNone(r['r'])

    def test_missing_window_close_is_not_invented_fill(self):
        r=sb.outcome(candidate(),[dict(t=at(10,7),o=109.3,h=109.5,l=109.2,c=109.4)],at(11,0),at(11,1))
        self.assertIn('unverified',r['status'])
        self.assertIsNone(r['r'])

    def test_stale_and_previous_day_never_presented_as_current(self):
        r=self.analyze(now=at(15,0));r['symbol']='QQQ'
        self.assertIn('stale',sb.describe(r))
        self.assertFalse(r['execution_authorized'])

