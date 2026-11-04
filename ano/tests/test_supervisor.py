from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch,Mock
from ano.supervisor import Policy,Supervisor,suspend,resume
from ano.reliability import atomic_json


class SupervisorTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.state=Path(self.tmp.name);self.now=10000
        ollama=patch('ano.supervisor.ollama_probe',return_value='healthy');self.ollama=ollama.start();self.addCleanup(ollama.stop)
    def test_only_three_refused_probes_restart_installed_ollama(self):
        app=self.state/'ollama app.exe';app.touch();self.ollama.return_value='offline'
        service=Supervisor(self.state);service.launch=Mock(return_value='starting')
        with patch('ano.supervisor.OLLAMA_APP',app),patch('ano.supervisor.keep_awake',return_value=False),patch('ano.supervisor.processes',return_value=[123]),patch('ano.supervisor.scor_probe',return_value='healthy'):
            service.tick();service.tick();service.launch.assert_not_called();service.tick()
            self.assertEqual(service.launch.call_args.args[:2],('ollama',[str(app)]))
            self.ollama.return_value='attention';service.launch.reset_mock()
            for _ in range(4):service.tick()
        service.launch.assert_not_called()  # a busy or slow server is never relaunched
    def test_trading_window_covers_every_session_but_not_the_weekend(self):
        from datetime import datetime,timezone
        from ano.supervisor import trading_window
        utc=lambda *a:datetime(*a,tzinfo=timezone.utc)
        self.assertTrue(trading_window(utc(2026,11,2,8,30)))    # London, 3:30 AM New York: frozen on Nov 2
        self.assertTrue(trading_window(utc(2026,11,2,0,30)))    # Tokyo
        self.assertTrue(trading_window(utc(2026,11,2,15,0)))    # New York stocks
        self.assertFalse(trading_window(utc(2026,11,7,12,0)))   # Saturday
        self.assertFalse(trading_window(utc(2026,11,2,22,30)))  # Monday 5:30 PM New York, between sessions
    def test_recovery_limits_survive_helper_restart(self):
        policy=Policy(self.state,lambda:self.now)
        self.assertTrue(policy.permit('ano'));policy.record('ano')
        self.assertFalse(policy.permit('ano'))
        self.now+=60;policy.record('ano');self.now+=60;policy.record('ano');self.now+=60
        self.assertFalse(Policy(self.state,lambda:self.now).permit('ano'))
        self.now+=3600;self.assertTrue(policy.permit('ano'))
    def test_live_owners_are_not_replaced_for_stale_heartbeats(self):
        service=Supervisor(self.state);service.launch=Mock()
        with patch('ano.supervisor.keep_awake',return_value=True),patch('ano.supervisor.processes',return_value=[123]),patch('ano.supervisor.scor_probe',return_value='healthy'):
            service.tick()
        service.launch.assert_not_called()
    def test_user_quit_prevents_all_launches_and_releases_sleep_request(self):
        service=Supervisor(self.state);service.launch=Mock();suspend(self.state)
        with patch('ano.supervisor.keep_awake',return_value=False) as awake,patch('ano.supervisor.processes',return_value=[]),patch('ano.supervisor.scor_probe') as probe:
            service.tick();awake.assert_called_once();self.assertIs(awake.call_args.args[0],False);probe.assert_not_called()
        service.launch.assert_not_called();resume(self.state)
        self.assertFalse((self.state/'supervisor.suspended.json').exists())
    def test_only_three_refused_probes_allow_scorpred_start(self):
        service=Supervisor(self.state);service.launch=Mock(return_value='starting')
        with patch('ano.supervisor.keep_awake',return_value=False),patch('ano.supervisor.processes',return_value=[123]),patch('ano.supervisor.scor_probe',return_value='offline'):
            service.tick();service.tick();service.launch.assert_not_called();service.tick()
        self.assertEqual(service.launch.call_args.args[0],'scorpred')
    def test_unknown_or_degraded_services_are_not_restarted(self):
        service=Supervisor(self.state);service.launch=Mock()
        with patch('ano.supervisor.keep_awake',return_value=False),patch('ano.supervisor.processes',return_value=[123]),patch('ano.supervisor.scor_probe',return_value='attention'):
            for _ in range(4):service.tick()
        service.launch.assert_not_called()
    def test_disabled_email_commands_are_not_started(self):
        atomic_json(self.state/'sports-mail/config.json',{'enabled':False})
        service=Supervisor(self.state);service.launch=Mock()
        with patch('ano.supervisor.keep_awake',return_value=False),patch('ano.supervisor.processes',side_effect=lambda name:[123] if name=='ano' else []),patch('ano.supervisor.scor_probe',return_value='healthy'):
            service.tick()
        service.launch.assert_not_called()
    def test_running_launcher_is_not_spawned_again(self):
        service=Supervisor(self.state);child=Mock();child.poll.return_value=None;service.children['scorpred']=child
        with patch('ano.supervisor.subprocess.Popen') as popen:
            self.assertEqual(service.launch('scorpred',['fixed'],self.state),'starting')
        popen.assert_not_called()
    def test_missing_ano_uses_only_fixed_windowless_tray_launch(self):
        service=Supervisor(self.state);service.launch=Mock(return_value='starting')
        with patch('ano.supervisor.keep_awake',return_value=False),patch('ano.supervisor.processes',return_value=[]),patch('ano.supervisor.scor_probe',return_value='healthy'):
            service.tick()
        args=service.launch.call_args.args
        self.assertEqual(args[0],'ano');self.assertEqual(args[1][-3:],['-m','ano','--tray'])
        self.assertTrue(args[1][0].endswith('pythonw.exe'))
    def test_fresh_file_with_dead_owner_cannot_claim_healthy(self):
        from datetime import datetime,timezone
        from ano.supervisor import observation
        now=datetime.now(timezone.utc)
        atomic_json(self.state/'supervisor.health.json',{'at':now.isoformat(),'state':'watching','pid':999999999,'owner_started':1})
        self.assertFalse(observation(self.state,now)['fresh'])
    def test_ui_recovers_missing_helper_but_not_live_stale_owner(self):
        from ano.supervisor import ensure_running
        with patch('ano.autostart.enabled',return_value=True),patch('ano.supervisor.processes',return_value=[123]),patch('ano.supervisor.subprocess.Popen') as start:
            ensure_running(self.state);start.assert_not_called()
        with patch('ano.autostart.enabled',return_value=True),patch('ano.supervisor.processes',return_value=[]),patch('ano.supervisor.subprocess.Popen') as start:
            ensure_running(self.state);start.assert_called_once()
