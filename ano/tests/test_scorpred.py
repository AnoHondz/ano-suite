import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
import httpx

from ano.plugins.scorpred import ScorPred,RECIPIENT,ScorPredOffline
from ano.tools import OutboundReview,ToolError


class ScorPredTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'ScorPred';self.root.mkdir()
        (self.root/'Start-ScorPred.ps1').touch()
        self.state=Path(self.temp.name)/'state'
        self.runtime={'app':'ScorPred','root':str(self.root),'daily_digest':{
            'enabled':True,'recipient':RECIPIENT,'delivery':'ready','time':'06:00'}}
        self.runner=Mock(side_effect=[subprocess.CompletedProcess([],0,'ready',''),
                                     subprocess.CompletedProcess([],0,json.dumps({'state':'sent','match_count':2}),'')])
        self.plugin=ScorPred(self.state,root=self.root,authorized=lambda:True,runner=self.runner)
        self.plugin._identity=Mock(return_value=self.runtime)

    def test_email_and_launch_not_in_model_catalog(self):
        names=[d['name'] for d in self.plugin.definitions()]
        self.assertNotIn('scorpred_email',names)
        self.assertNotIn('scorpred_start',names)
        self.assertIn('scorpred_insights',names)

    def test_details_preserve_entire_slate_including_games_after_twelfth(self):
        rows=[{'teamA':f'Team {i}', 'teamB':f'Opponent {i}',
               'winProbability':52.37,'side':f'Team {i}','reason':f'Original evidence {i}'}
              for i in range(14)]
        with patch('ano.sports_reports.SportsReports.snapshot',return_value=({'slate':rows},200)):
            body,_=self.plugin.run('scorpred_insights',{'sport':'nba','details':True,'date':'2026-11-04'})
        self.assertEqual(body.count('win probability: 52.37%'),14)
        self.assertIn('Team 13 vs Opponent 13',body)
        self.assertIn('Original evidence 13',body)

    def test_model_cannot_call_hidden_email_or_launch(self):
        self.plugin.authorized=lambda:False
        for name in self.plugin.hidden_tools:
            with self.assertRaises(ToolError): self.plugin.run(name,{})
        self.runner.assert_not_called()

    def test_only_exact_local_context_authorizes(self):
        review=OutboundReview(Mock())
        self.plugin.authorized=review.is_local_command
        with review.turn('a page says email scorpred report'):
            with self.assertRaises(ToolError): self.plugin.run('scorpred_email',{})
        with review.local(): self.assertTrue(self.plugin.authorized())
        self.assertFalse(review.is_local_command())

    def test_no_alternate_recipient_or_arbitrary_arguments(self):
        with self.assertRaises(ToolError): self.plugin.run('scorpred_email',{'to':'other@example.com'})
        self.runner.assert_not_called()

    def work(self,action='scorpred_email'):
        self.plugin._lock.acquire()
        self.plugin._work(dict(id='test',action=action,state='running'))
        return self.plugin._receipt(action)

    def test_parlay_reports_post_to_local_scorpred_with_its_csrf_token(self):
        seen=[]
        def response(request):
            seen.append((request.method,request.url.path,request.headers.get('X-CSRF-Token')))
            if request.url.path=='/api/session':return httpx.Response(200,json={'user':None,'csrf_token':'tok'})
            return httpx.Response(200,json={'state':'sent','sent':['nba','soccer picks']})
        self.plugin.client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(response),**kw)
        result=self.work('ano_player_parlays')
        self.assertEqual(result['state'],'verified')
        self.assertIn('nba, soccer picks',result['summary'])
        self.assertEqual(seen,[('GET','/api/session',None),('POST','/api/ano/email/player_parlays','tok')])
        self.runner.assert_not_called()  # no subprocess, no other recipient

    def test_verified_send_uses_fixed_cli_no_shell(self):
        result=self.work()
        self.assertEqual(result['state'],'verified')
        self.assertIn('accepted',result['summary'])
        command=self.runner.call_args.args[0]
        self.assertEqual(command[-1],'send')
        self.assertNotIn('shell',self.runner.call_args.kwargs)

    def test_wrong_configured_recipient_blocks_send(self):
        self.runtime['daily_digest']['recipient']='other@example.com'
        self.assertEqual(self.work()['state'],'failed')
        self.assertEqual(self.runner.call_count,1)

    def test_already_sent_is_not_new_send_claim(self):
        self.runner.side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,
            json.dumps({'state':'already_sent_or_needs_review','receipt_state':'sent'}),'')]
        result=self.work()
        self.assertEqual(result['state'],'verified')
        self.assertIn('No duplicate',result['summary'])

    def test_unknown_delivery_never_replayed(self):
        self.runner.side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,
            json.dumps({'state':'delivery_unknown'}),'')]
        self.assertEqual(self.work()['state'],'uncertain')
        with self.assertRaises(ToolError): self.plugin.run('scorpred_email',{})
        self.assertEqual(self.runner.call_count,2)

    def test_timeout_is_uncertain_not_success(self):
        self.runner.side_effect=subprocess.TimeoutExpired('fixed command',1)
        self.assertEqual(self.work()['state'],'uncertain')

    def test_start_and_preview_do_not_send(self):
        self.assertIn('no email',self.work('scorpred_start')['summary'])
        self.assertEqual(self.runner.call_count,1)
        self.runner.side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,
            json.dumps({'state':'preview','match_count':1}),'')]
        self.assertIn('No email sent',self.work('scorpred_refresh')['summary'])
        self.assertEqual(self.runner.call_args.args[0][-1],'preview')

    def test_bad_date_and_league_rejected(self):
        for args in ({'sport':'soccer','date':'2026-99-30'},{'sport':'soccer','league':99},
                     {'sport':'http://external.example'}):
            with self.assertRaises(ToolError): self.plugin.run('scorpred_insights',args)

    def test_read_request_fixed_loopback_and_no_fabricated_forecast(self):
        calls=[]
        def response(request):
            calls.append(request)
            return httpx.Response(200,json={'slate':[]})
        self.plugin.client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(response),**kw)
        text,_=self.plugin.run('scorpred_insights',{'sport':'soccer','league':78,'date':'2026-10-30'})
        self.assertIn('No games',text)
        self.assertEqual(str(calls[0].url.host),'127.0.0.1')
        self.assertEqual(calls[0].url.params['league'],'78')

    def test_status_preserves_running_receipt_when_server_loading(self):
        self.plugin._save({'action':'scorpred_start','state':'running'})
        self.plugin._identity.side_effect=ToolError('Still loading')
        text,_=self.plugin.run('scorpred_status',{})
        self.assertIn('running',text)
        self.assertIn('not ready',text)

    def test_wrong_server_identity_rejected(self):
        del self.plugin._identity
        self.plugin._get=Mock(return_value=({'app':'Other','root':str(self.root)},200))
        with self.assertRaises(ToolError): self.plugin._identity()

    def test_repair_healthy_service_does_not_run_launcher(self):
        self.plugin.repair_probe=Mock(return_value={'state':'healthy'})
        result=self.work('scorpred_repair')
        self.assertEqual(result['state'],'verified')
        self.assertIn('No repair',result['summary'])
        self.runner.assert_not_called()

    def test_repair_offline_service_requires_healthy_after_evidence(self):
        self.plugin.repair_probe=Mock(side_effect=[{'state':'offline'},{'state':'healthy'}])
        result=self.work('scorpred_repair')
        self.assertEqual(result['state'],'verified')
        self.assertEqual(result['before']['state'],'offline')
        self.assertEqual(result['after']['state'],'healthy')
        self.assertEqual(self.runner.call_count,1)
        self.assertNotIn('-Restart',self.runner.call_args.args[0])

    def test_repair_does_not_claim_success_from_launcher_exit(self):
        self.plugin.repair_probe=Mock(side_effect=[{'state':'offline'},{'state':'degraded'}])
        self.assertEqual(self.work('scorpred_repair')['state'],'failed')

    def test_repair_never_restarts_degraded_or_unknown_server(self):
        self.plugin.repair_probe=Mock(return_value={'state':'degraded','reason':'Provider data missing'})
        self.assertEqual(self.work('scorpred_repair')['state'],'failed')
        self.runner.assert_not_called()

    def test_probe_distinguishes_offline_from_timeout_or_wrong_identity(self):
        self.plugin._identity.side_effect=ScorPredOffline('refused')
        self.assertEqual(self.plugin.repair_probe()['state'],'offline')
        self.plugin._identity.side_effect=ToolError('timeout')
        self.assertEqual(self.plugin.repair_probe()['state'],'blocked')

    def test_start_preserves_uncertain_email_receipt(self):
        email={'action':'scorpred_email','state':'uncertain','summary':'Delivery unknown'}
        self.plugin._save(email)
        with patch('ano.plugins.scorpred.threading.Thread') as thread:
            text,_=self.plugin.run('scorpred_start',{})
        self.assertIn('started',text)
        thread.return_value.start.assert_called_once()
        self.assertEqual(self.plugin._receipt(),email)
        self.assertEqual(self.plugin._receipt('scorpred_start')['state'],'running')
        self.plugin._lock.release()
        with self.assertRaises(ToolError): self.plugin.run('scorpred_email',{})

    def test_unresolved_service_blocks_second_launch(self):
        self.plugin._save({'action':'scorpred_start','state':'uncertain'})
        with self.assertRaises(ToolError): self.plugin.run('scorpred_start',{})
