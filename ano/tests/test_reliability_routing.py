from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ano.sports_query import parse
from ano.inference_gate import acquire,InferenceBusy


class ReliabilityRoutingTests(unittest.TestCase):
    def test_many_read_phrasings_preserve_explicit_date(self):
        for prefix in ('show me ','get ','check ','pull up ','give me ','please show me '):
            for sport in ('nba','soccer','nfl'):
                for suffix in (' picks for tomorrow'," predictions tomorrow",' slate on 2026-10-29'):
                    with self.subTest(text=prefix+sport+suffix):
                        self.assertEqual(parse(prefix+sport+suffix,date(2026,10,28)),{'sport':sport,'date':'2026-10-29'})

    def test_relative_dates_roll_over_year_and_leap_day(self):
        self.assertEqual(parse('tomorrow nba picks',date(2026,12,31))['date'],'2027-01-01')
        self.assertEqual(parse("show me tomorrow's nba games",date(2028,2,28))['date'],'2028-02-29')

    def test_no_partial_match_embedded_action_or_bad_date(self):
        for text in ('email nba picks tomorrow','trade nba picks','show nba and delete files',
                     'nba picks today tomorrow','nba picks 2026-02-30','why do you like nba','nba picks if useful'):
            self.assertIsNone(parse(text,date(2026,10,28)),text)

    def test_read_only_typo_alias_does_not_touch_payload(self):
        self.assertEqual(parse('get scopred nba 2026-10-29')['date'],'2026-10-29')
        self.assertIsNone(parse('email scopred nba'))

    def test_memory_pressure_refuses_before_lock(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'inference.lock'
            with self.assertRaises(InferenceBusy):acquire(path,lambda:(97,3*1024**3))
            self.assertFalse(path.exists())
            with self.assertRaises(InferenceBusy):acquire(path,lambda:(80,500*1024**2))

    def test_duplicate_request_blocked_until_actual_lease_release(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'inference.lock'
            first=acquire(path,lambda:(80,2*1024**3))
            try:
                with self.assertRaises(InferenceBusy):acquire(path,lambda:(80,2*1024**3))
            finally:first.close()
            with acquire(path,lambda:(80,2*1024**3)):pass

    def test_cross_process_duplicate_is_rejected(self):
        import subprocess,sys
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'inference.lock'
            code='from ano.inference_gate import acquire,InferenceBusy; import sys\ntry:\n acquire(sys.argv[1],lambda:(80,2*1024**3)); print("acquired")\nexcept InferenceBusy: print("busy")'
            with acquire(path,lambda:(80,2*1024**3)):
                result=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'busy')

    def test_no_model_used_by_routine_for_dated_read(self):
        from ano.routines import LocalRoutines,RoutineStore
        with TemporaryDirectory() as tmp:
            model=Mock(side_effect=AssertionError('No model allowed'))
            local=LocalRoutines(Mock(),RoutineStore(Path(tmp)/'routines.sqlite3'))
            plan=local.plan('please show me nba picks tomorrow')
            self.assertEqual(plan.steps[0][0],'scorpred_insights')
            self.assertEqual(plan.steps[0][1]['sport'],'nba')
            model.assert_not_called()

    def test_interrupted_job_without_owner_is_uncertain_not_replayed(self):
        import json
        from ano.plugins.scorpred import ScorPred
        with TemporaryDirectory() as tmp:
            state=Path(tmp);(state/'last-job.json').write_text(json.dumps({'state':'running','action':'scorpred_email','id':'legacy'}))
            runner=Mock()
            plugin=ScorPred(state,runner=runner)
            self.assertEqual(plugin._receipt()['state'],'uncertain')
            self.assertEqual(len(list(state.glob('*.before-recovery-*.json'))),1)
            runner.assert_not_called()

    def test_verified_live_owner_is_not_marked_interrupted(self):
        import json,os,psutil
        from ano.plugins.scorpred import ScorPred
        with TemporaryDirectory() as tmp:
            state=Path(tmp);(state/'last-job.json').write_text(json.dumps({'state':'running','action':'scorpred_email',
                'owner_pid':os.getpid(),'owner_started':psutil.Process().create_time()}))
            self.assertEqual(ScorPred(state)._receipt()['state'],'running')
            self.assertFalse(list(state.glob('*.before-recovery-*.json')))


if __name__=='__main__':unittest.main()
