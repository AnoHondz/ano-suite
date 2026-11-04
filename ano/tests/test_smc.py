from datetime import datetime, timezone
import unittest

from ano.smc import analyze, higher_bias


BASE=int(datetime(2026,10,29,13,30,tzinfo=timezone.utc).timestamp())


def fixture():
    values=[(100,102,99,101),(101,103,100,102),(102,105,101,103),
            (103,104,100,101),(101,103,99,100),(101,102,99,100),
            (100,108,100,107),(107,108,97,98),(98,102,97,100)]
    return [dict(t=BASE+i*60,o=o,h=h,l=l,c=c) for i,(o,h,l,c) in enumerate(values)]


class SmcTests(unittest.TestCase):
    def test_order_block_confirms_only_after_break_and_breaker_after_invalidation(self):
        bars=fixture()
        before=analyze(bars,now=BASE+6*60)
        self.assertEqual(before['order_blocks'],[])
        at_break=analyze(bars,now=BASE+7*60)
        zone=at_break['order_blocks'][0]
        self.assertEqual((zone['origin'],zone['low'],zone['high']), (BASE+5*60,99,102))
        self.assertEqual(zone['status'],'active')
        self.assertEqual(at_break['breaker_blocks'],[])
        failed=analyze(bars,now=BASE+8*60)
        self.assertEqual(failed['order_blocks'][0]['status'],'invalidated')
        breaker=failed['breaker_blocks'][0]
        self.assertEqual((breaker['direction'],breaker['status']),('bearish','active'))
        self.assertNotIn('retested_at',breaker)
        retested=analyze(bars,now=BASE+9*60)
        self.assertEqual(retested['breaker_blocks'][0]['status'],'retested')
        self.assertFalse(retested['execution_authorized'])

    def test_prefix_results_do_not_change_when_future_bars_are_supplied(self):
        bars=fixture()
        self.assertEqual(analyze(bars,now=BASE+7*60),analyze(bars[:7],now=BASE+7*60))

    def test_missing_minute_never_bridges_displacement_to_origin(self):
        bars=fixture()
        bars[6]['t']+=60
        report=analyze(bars[:7],now=BASE+9*60)
        self.assertEqual(report['order_blocks'],[])

    def test_higher_timeframes_exclude_forming_and_missing_data_is_unavailable(self):
        now=BASE+10*60
        higher={}
        for name,interval in [('daily',86400),('4h',14400),('1h',3600)]:
            higher[name]=[dict(t=now-3*interval,o=100,h=102,l=99,c=100),
                          dict(t=now-2*interval,o=100,h=104,l=99,c=103),
                          dict(t=now-interval+1,o=103,h=104,l=90,c=91)]
        result=higher_bias(higher,now)
        self.assertEqual(result['alignment'],'bullish')
        self.assertTrue(all(result[k]['closed_bars']==2 for k in higher))
        self.assertEqual(higher_bias({},now)['alignment'],'unavailable')

    def test_stale_and_bad_input_never_create_execution_authority(self):
        result=analyze(fixture(),now=BASE+3600)
        self.assertTrue(result['stale'])
        self.assertFalse(result['execution_authorized'])
        bad=fixture();bad[0]['h']=float('nan')
        with self.assertRaises(ValueError):analyze(bad,now=BASE+3600)
        bad=fixture();bad[1]['t']=bad[0]['t']
        with self.assertRaises(ValueError):analyze(bad,now=BASE+3600)
