from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sqlite3,unittest
from ano.reliability import Reliability,ReliabilityMonitor,render


class ReliabilityTests(unittest.TestCase):
    def service(self,tmp):
        now=datetime(2026,10,29,12,tzinfo=timezone.utc)
        (Path(tmp)/'paper-execution.health.json').write_text(json.dumps({'at':now.isoformat(),'state':'running',
            'enabled':True,'recovery_ready':True,'symbols':['QQQ'],'errors':[],'safety_version':'paper_safety_v2'}))
        with sqlite3.connect(Path(tmp)/'paper-execution.sqlite3') as db:
            db.execute('CREATE TABLE gap_decisions(day,symbol)');db.execute('CREATE TABLE gap_trades(day,symbol,side,state,filled_qty)')
        db.close()
        def get(path):return {'app':'ScorPred','root':str(Path(tmp)/'ScorPred')} if path.endswith('runtime') else {'status':'ok','db':'connected'}
        def broker(**kw):return {'at':now.isoformat(),'broker_verified':True,'account_active':True,'trading_blocked':False,
                                'open_orders_complete':True,'positions':[],'other_position_count':0,'open_order_count':0,'issues':[]}
        return Reliability(tmp,Path(tmp)/'ScorPred',getter=get,broker=broker,clock=lambda:now)

    def test_verified_infrastructure_is_not_live_or_dispatch_authorization(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);report=s.snapshot(verify_broker=True)
            self.assertTrue(report['readiness']['infrastructure_ready'])
            self.assertFalse(report['readiness']['live_ready']);self.assertFalse(report['readiness']['execution_ready_now'])
            self.assertEqual(report['soak']['sample_count'],1)
            self.assertIsNone(report['forecasts']['brier_score'])
            self.assertIn('No accuracy claim',next(c['detail'] for c in report['checks'] if c['component']=='Forecast evidence'))

    def test_stale_cached_broker_and_runner_cannot_look_ready(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);s.snapshot(verify_broker=True)
            s.clock=lambda:datetime(2026,10,29,13,tzinfo=timezone.utc)
            report=s.snapshot()
            self.assertFalse(report['readiness']['infrastructure_ready'])

    def test_identity_and_management_errors_are_explicit_blockers(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);s.getter=lambda path:{'app':'Other'}
            report=s.snapshot(verify_broker=True)
            self.assertEqual(report['checks'][0]['state'],'blocked')
            # ScorPred health is independent of paper infrastructure readiness.
            self.assertIn('identity',report['checks'][0]['detail'])

    def test_monitor_notifies_only_changes_and_never_claims_unobserved_uptime(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);s.getter=lambda path:(_ for _ in ()).throw(OSError())
            monitor=ReliabilityMonitor(s)
            first=monitor.sample();messages=monitor.poll();self.assertTrue(messages)
            monitor.sample();self.assertEqual(monitor.poll(),[])
            self.assertIn('no unobserved uptime',first['soak']['status'])

    def test_dashboard_escapes_provider_errors(self):
        with TemporaryDirectory() as tmp:
            report=self.service(tmp).snapshot(verify_broker=True)
            report['checks'][0]['detail']='<script>fixture</script>'
            html=render(report)
            self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)

    def test_heartbeat_updated_during_remote_check_is_not_mistaken_for_future_evidence(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);start=s.clock()
            path=Path(tmp)/'paper-execution.health.json';heartbeat=json.loads(path.read_text())
            heartbeat['at']=(start+timedelta(seconds=1)).isoformat();path.write_text(json.dumps(heartbeat))
            mail=Path(tmp)/'sports-mail';mail.mkdir()
            (mail/'config.json').write_text(json.dumps({'enabled':True}))
            (mail/'heartbeat.json').write_text(json.dumps({'at':(start+timedelta(seconds=3)).isoformat(),'state':'checked'}))
            calls=[0]
            def clock():
                value=start+timedelta(seconds=calls[0]*2);calls[0]+=1;return value
            s.clock=clock
            report=s.snapshot(verify_broker=True)
            checks={c['component']:c['state'] for c in report['checks']}
            self.assertEqual(checks['Paper runner'],'healthy');self.assertEqual(checks['Phone command worker'],'healthy')

    def test_unreadable_email_receipts_do_not_imply_success_or_zero_uncertainty(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);mail=Path(tmp)/'sports-mail';mail.mkdir()
            (mail/'receipts.sqlite3').write_bytes(b'fixture invalid database')
            report=s.snapshot(verify_broker=True)
            email=next(c for c in report['checks'] if c['component']=='Email')
            self.assertEqual(email['state'],'blocked');self.assertIn('cannot be verified',email['detail'])

    def test_active_strategy_modes_have_their_own_windows_and_require_new_safety_version(self):
        with TemporaryDirectory() as tmp:
            s=self.service(tmp);path=Path(tmp)/'paper-execution.health.json';heartbeat=json.loads(path.read_text())
            heartbeat.update(enabled=False,strategy_execution=['silver bullet'],safety_version='paper_safety_v2')
            path.write_text(json.dumps(heartbeat));self.assertFalse(s.snapshot(verify_broker=True)['readiness']['infrastructure_ready'])
            heartbeat['safety_version']='paper_safety_v3';path.write_text(json.dumps(heartbeat))
            r=s.snapshot(verify_broker=True)
            self.assertTrue(r['readiness']['entry_enabled']);self.assertEqual(r['readiness']['authorized_execution'],['silver_bullet_v1'])
            self.assertNotIn('silver bullet',r['readiness']['analysis_only'])
