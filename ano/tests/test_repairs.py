from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from ano.repairs import RepairPolicy, CrawlerRepairs, free_memory, restart_mail


class RepairPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'repairs.json';self.now=10000
        self.p=RepairPolicy(self.path,clock=lambda:self.now);self.p.data['enabled']=True

    def ready(self,state='offline',receipt=None):
        return self.p.ready({'state':state},receipt or {})

    def test_only_three_confirmed_offline_checks_allow_start(self):
        self.assertFalse(self.ready());self.assertFalse(self.ready());self.assertTrue(self.ready())

    def test_timeout_unknown_and_degraded_never_trigger_restart(self):
        for state in ('blocked','degraded','healthy','unknown'):
            for _ in range(4):self.assertFalse(self.ready(state))

    def test_normal_probe_breaks_offline_streak(self):
        self.ready();self.ready();self.ready('healthy')
        self.assertFalse(self.ready())

    def test_busy_and_uncertain_jobs_block_recovery(self):
        for state in ('running','uncertain'):
            for _ in range(4):self.assertFalse(self.ready(receipt={'state':state}))

    def test_disabled_does_not_repair(self):
        self.p.data['enabled']=False
        for _ in range(4):self.assertFalse(self.ready())

    def test_cooldown_limit_and_persistence(self):
        self.p.started('one');self.p.completion({'id':'one','state':'failed','summary':'No healthy server'})
        for _ in range(4):self.assertFalse(self.ready())
        self.now+=3601
        self.assertTrue(self.ready())
        self.p.started('two');self.p.completion({'id':'two','state':'failed'})
        self.now+=3601
        self.p=RepairPolicy(self.path,clock=lambda:self.now)
        for _ in range(4):self.assertFalse(self.ready())

    def test_ambiguous_completion_blocks_retry_and_notifies_once(self):
        self.p.started('one')
        receipt={'id':'one','state':'uncertain','summary':'Unknown outcome'}
        self.assertIn('uncertain',self.p.completion(receipt))
        self.assertIsNone(self.p.completion(receipt))
        self.now+=90000
        for _ in range(4):self.assertFalse(self.ready())

    def test_only_matching_final_receipt_completes(self):
        self.p.started('one')
        self.assertIsNone(self.p.completion({'id':'other','state':'verified'}))
        self.assertIsNone(self.p.completion({'id':'one','state':'running'}))
        self.assertIn('verified',self.p.completion({'id':'one','state':'verified','summary':'Observed healthy'}))
        self.assertEqual(self.p.data['pending_id'],'')


class CrawlerRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.now=datetime.now(timezone.utc).timestamp()
        self.policy=RepairPolicy(root/'repairs.json',clock=lambda:self.now);self.policy.data['enabled']=True
        self.signals=root/'crawler-signals.json'
        self.fixes={'system':lambda:'unloaded big-model','mail':lambda:None}
        self.c=CrawlerRepairs(self.policy,self.signals,self.fixes,clock=lambda:self.now)

    def flag(self,*stations,age=0):
        at=datetime.fromtimestamp(self.now-age,timezone.utc).isoformat()
        self.signals.write_text(json.dumps({'at':at,'flags':[{'station':s,'state':'attention'} for s in stations]}),encoding='utf-8')

    def test_only_fresh_flags_with_a_listed_fix_are_due(self):
        self.flag('system','forex','stocks','mail')
        self.assertEqual(self.c.due(),['system','mail'])  # trading stations are never repaired
        self.flag('system',age=60)
        self.assertEqual(self.c.due(),[])  # a stale signal file means the wallpaper stopped: do nothing

    def test_a_fix_runs_once_at_a_time_then_waits_ten_minutes_max_three_an_hour(self):
        self.flag('system')
        row=self.c.start('system');self.assertEqual(self.c.due(),[])  # still working
        self.c.finish(row['id'],CrawlerRepairs.run(self.fixes['system']))
        self.assertEqual(self.c.records()[-1]['state'],'done');self.assertEqual(self.c.records()[-1]['detail'],'unloaded big-model')
        self.assertEqual(self.c.due(),[])
        for _ in range(2):
            self.now+=601;self.flag('system');self.c.finish(self.c.start('system')['id'],('done','ok'))
        self.now+=601;self.flag('system');self.assertEqual(self.c.due(),[])  # three in the last hour

    def test_a_fix_that_no_longer_applies_leaves_no_record_and_refunds_the_attempt(self):
        self.flag('mail');row=self.c.start('mail')
        self.c.finish(row['id'],CrawlerRepairs.run(self.fixes['mail']))
        self.assertEqual(self.c.records(),[]);self.assertEqual(self.policy.data['crawler_attempts']['mail'],[])
        def broken():raise OSError('no')
        self.assertEqual(CrawlerRepairs.run(broken)[0],'failed')

    def test_free_memory_only_acts_under_pressure(self):
        class Manager:
            calls=0
            def sweep(self,force):Manager.calls+=1;return {'unloaded':['writer-model']}
        self.assertIsNone(free_memory(Manager(),sample=lambda:(80,4*1024**3)))
        self.assertEqual(free_memory(Manager(),sample=lambda:(95,500*1024**2)),'unloaded writer-model')
        self.assertEqual(Manager.calls,1)

    def test_restart_mail_never_replaces_a_live_worker(self):
        started=[]
        self.assertIsNone(restart_mail('x',running=lambda:[123],resume=lambda s:started.append(s) or True))
        self.assertEqual(started,[])
        self.assertEqual(restart_mail('x',running=lambda:[],resume=lambda s:True),'email worker started again')
        self.assertIsNone(restart_mail('x',running=lambda:[],resume=lambda s:False))  # not opted in
