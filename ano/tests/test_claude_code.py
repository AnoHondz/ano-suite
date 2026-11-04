import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from ano.claude_code import ClaudeJobs, job_task


class FakeChild:
    pid = 4242


class ClaudeJobTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.now = [1000.0]; self.posted = []; self.launched = []
        def launch(args, **kwargs):
            self.launched.append((args, kwargs)); return FakeChild()
        self.jobs = ClaudeJobs(Path(tmp.name), notify=self.posted.append, launcher=launch,
                               binary=lambda: 'claude.exe', clock=lambda: self.now[0])
        self.alive = patch('ano.claude_code.alive', return_value=True); self.alive.start(); self.addCleanup(self.alive.stop)

    def test_work_requests_go_to_claude_and_chat_does_not(self):
        self.assertEqual(job_task('hey Ano, I need you to enable the trading bot'), ('hey Ano, I need you to enable the trading bot', False))
        self.assertEqual(job_task('claude: why did the email not send'), ('why did the email not send', False))
        self.assertEqual(job_task('fix the soccer email'), ('fix the soccer email', False))
        self.assertEqual(job_task('claude continue: also add corners'), ('also add corners', True))
        for chat in ('how are you', 'what time is it', 'make me laugh', 'tell me a joke', 'why is the sky blue'):
            self.assertIsNone(job_task(chat), chat)

    def test_job_runs_hidden_with_hard_rules_and_reports_its_final_answer(self):
        reply = self.jobs.command('fix the soccer email')
        self.assertIn('On it', reply); self.assertEqual(self.launched, [])      # started by the main-thread poll, not here
        self.jobs.poll()
        args, kwargs = self.launched[0]
        self.assertIn('-p', args); self.assertIn('fix the soccer email', args)
        self.assertIn('you@example.com', args[args.index('--append-system-prompt') + 1])
        self.assertNotIn('ANTHROPIC_API_KEY', kwargs['env'])                       # the user's plan login, not an API key
        self.assertTrue(kwargs['creationflags'])
        job = self.jobs.jobs()[0]; self.assertEqual(job['state'], 'running')
        (self.jobs.dir / (job['id'] + '.out')).write_text(json.dumps({'result': 'Fixed it; the 9 AM email is live.', 'session_id': 's1'}))
        self.now[0] += 300
        with patch('ano.claude_code.alive', return_value=False): self.jobs.poll()
        self.assertIn('Claude finished', self.posted[0]); self.assertIn('9 AM email is live', self.posted[0])
        with patch('ano.claude_code.alive', return_value=False): self.jobs.poll()
        self.assertEqual(len(self.posted), 1)                                      # reported once
        self.jobs.command('claude continue: also add corners'); self.jobs.poll()
        args = self.launched[-1][0]; self.assertEqual(args[args.index('--resume') + 1], 's1')

    def test_one_job_at_a_time_and_a_crash_without_answer_is_reported_as_failed(self):
        self.jobs.command('fix the soccer email'); self.jobs.command('build the matchup engine')
        self.assertIn('2 jobs ahead', self.jobs.command('add corners to soccer'))
        self.jobs.poll(); self.jobs.poll()
        self.assertEqual(len(self.launched), 1)
        with patch('ano.claude_code.alive', return_value=False): self.jobs.poll()
        self.assertIn('Claude could not finish', self.posted[0])
        self.assertEqual(len(self.launched), 2)                                    # the next one started
        self.assertIn('running: build the matchup engine', self.jobs.command('claude jobs'))

    def test_stop_kills_the_running_job(self):
        self.jobs.command('fix the soccer email'); self.jobs.poll()
        with patch('ano.claude_code.kill_tree') as kill:
            self.assertEqual(self.jobs.command('stop claude'), 'Stopped the Claude job.')
        kill.assert_called_once_with(4242)
        self.assertEqual(self.jobs.jobs()[0]['state'], 'stopped')

    def test_routines_hand_unmatched_work_to_claude_only_after_local_commands(self):
        from ano.routines import LocalRoutines
        routines = LocalRoutines(Mock(allowed=lambda: True), Mock())
        routines.claude_jobs = Mock(command=Mock(return_value='On it'))
        with patch.object(routines, 'plan', return_value=None), patch('ano.sports_email.email_request', return_value=None):
            self.assertIn('On it', routines.reply('fix the soccer email'))
        routines.claude_jobs.command.return_value = None
        with patch.object(routines, 'plan', return_value=None), patch('ano.sports_email.email_request', return_value=None):
            self.assertIsNone(routines.reply('tell me a joke'))


class PickRecordPhraseTests(TestCase):
    def test_asking_how_picks_did_reads_the_tracker_not_claude(self):
        from ano.routines import LocalRoutines
        routines = LocalRoutines(Mock(allowed=lambda: True), Mock())
        for text, days in (('how did my picks do', 1), ('how did my parlays do yesterday', 1), ('did my parlay hit', 1),
                           ('how did my picks do this week', 7), ('pick record this month', 30)):
            plan = routines.plan(text)
            self.assertEqual(plan.steps, [('scorpred_pick_record', {'days': days})], text)


class SelfUpdateTests(TestCase):
    def test_forex_approval_is_only_renewed_when_the_suite_passes(self):
        from ano.self_update import renew_forex
        with TemporaryDirectory() as tmp:
            self.assertIn('refused', renew_forex('new rule', state=tmp, passes=lambda: False))
            self.assertFalse((Path(tmp) / 'forex-native-execution.json').exists())
            self.assertIn('refused', renew_forex('  ', state=tmp, passes=lambda: True))
            self.assertTrue(renew_forex('new rule', state=tmp, passes=lambda: True).startswith('renewed'))
            data = json.loads((Path(tmp) / 'forex-native-execution.json').read_text())
            self.assertEqual(data['provider'], 'oanda_practice'); self.assertIn('new rule', data['renewal'])
