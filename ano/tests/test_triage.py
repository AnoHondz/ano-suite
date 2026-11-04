from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ano.paid import PaidTasks, SpendLedger
from ano.routines import LocalRoutines, RoutineBrain, RoutineStore, APPS
from ano.triage import LocalTriage


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.toolbox = Mock()
        self.toolbox.allowed.return_value = True
        self.toolbox.run.return_value = ('Synthetic tool receipt', '', False)
        self.routines = LocalRoutines(self.toolbox, RoutineStore(self.root / 'routines.db'),
                                      clock=lambda: datetime(2026, 10, 27, 10))
        self.factory = Mock(side_effect=AssertionError('No paid client may be constructed'))
        self.fallback = Mock(side_effect=AssertionError('No local model may run'))
        self.local = RoutineBrain(self.routines, self.fallback)
        self.router = PaidTasks(self.local, self.toolbox, SpendLedger(self.root / 'spend.db'),
                                mode='openai', key_loader=lambda: 'never-used', client_factory=self.factory)

    def ask(self, text, chat=17):
        return self.router.reply([{'role': 'user', 'body': text, 'source_chat_id': chat}])

    def test_more_than_fifty_unique_phrasings_use_no_model(self):
        variants = [prefix + app + suffix for app in APPS
                    for prefix, suffix in [('open ', ''), ('can you please open ', ' for me'), ('please launch ', ' please')]]
        variants += ["what's my ram", 'is my battery ok', 'battery status', 'am i online',
                     'how much storage is free', 'what is the time', 'tell me the date',
                     'list my reminders', 'show notes', '2 + 3', 'opne calcualtor',
                     'open calc', 'please open ms word for me', 'read file example.txt',
                     'write document Fixture: Exact text.', 'take a note: Preserve MiXeD text.',
                     'remind me in 20 minutes to Check the fixture']
        self.assertGreaterEqual(len(set(variants)), 50)
        for phrase in variants:
            with self.subTest(phrase=phrase):
                self.assertIn('No LLM used', self.ask(phrase))
        self.factory.assert_not_called()
        self.fallback.reply.assert_not_called()

    def test_uncertain_match_needs_confirmation_in_same_chat(self):
        response = self.ask('open outlook w')
        self.assertIn('Did you mean', response)
        self.toolbox.run.assert_not_called()
        # An unrelated exact command invalidates the pending suggestion.
        self.ask('what time is it')
        self.assertFalse(self.routines.triage.decide('yes', 17).command)
        self.ask('open outlook w')
        self.assertFalse(self.routines.triage.decide('yes', 18).command)
        self.ask('yes')
        self.toolbox.run.assert_called_once_with('open_website', {'url': 'https://outlook.live.com/mail/'})
        self.factory.assert_not_called()

    def test_ambiguous_reminder_needs_meridiem_and_preserves_payload(self):
        self.assertIn('5 am or 5 pm', self.ask('remind me at 5 to Call MiRa'))
        self.toolbox.run.assert_not_called()
        self.ask('5pm')
        args = self.toolbox.run.call_args.args[1]
        self.assertEqual(args['text'], 'Call MiRa')
        self.assertEqual(args['when'], '2026-10-27T17:00:00')
        self.factory.assert_not_called()

    def test_negation_alternatives_and_expired_suggestions_do_not_act(self):
        for phrase in ("don't open calculator", 'open calculator or word', 'open zyxqv'):
            self.ask(phrase)
        self.toolbox.run.assert_not_called()
        clock = [1]
        triage = LocalTriage(clock=lambda: clock[0])
        triage.decide('open outlook w', 17)
        clock[0] = 302
        self.assertFalse(triage.decide('yes', 17).command)

    def test_fuzzy_action_confidence_and_payload_boundaries(self):
        triage = LocalTriage()
        decision = triage.decide('opne calcualtor', 17)
        self.assertEqual(decision.command, 'open calculator')
        self.assertGreaterEqual(decision.confidence, .88)
        for text in ('open outlook w', 'take a note: opne calcualtor',
                     'open calculator and then send an email', 'create document named open calculator'):
            self.assertFalse(triage.decide(text, 17).command)
        self.ask('take a note: opne calcualtor for me')
        self.toolbox.run.assert_called_once_with('add_note', {'text': 'opne calcualtor for me'})

    def test_real_words_near_launch_verbs_require_confirmation(self):
        for phrase in ('oven word', 'stark word', 'oven calcualtor'):
            with self.subTest(phrase=phrase):
                decision = self.routines.triage.decide(phrase, 17)
                self.assertFalse(decision.command)
                self.assertIn('Did you mean', decision.question)
                self.ask(phrase)
        self.toolbox.run.assert_not_called()
        self.factory.assert_not_called()

    def test_literal_domain_opens_free_without_rewriting_path_case(self):
        self.ask('open example.org/CaseSensitive?query=MiXeD')
        self.toolbox.run.assert_called_once_with('open_website', {'url':'https://example.org/CaseSensitive?query=MiXeD'})
        self.factory.assert_not_called()
        self.assertFalse(self.routines.triage.decide('open example dot org',17).command)

    def test_failed_local_command_is_not_recorded_as_local_model(self):
        from ano.claude import ClaudeError
        self.toolbox.run.return_value = ('Synthetic app failure','',True)
        with self.assertRaises(ClaudeError):
            self.ask('open calculator')
        with self.router.ledger.connect() as db:
            self.assertEqual(db.execute('select route,status,estimated_saved_usd from message_routes').fetchone(),
                             ('local_command','failed',0))
        self.factory.assert_not_called()

    def test_prefixed_confirmation_is_not_consumed_before_command_routing(self):
        self.ask('open outlook w')
        self.ask('local: yes')
        self.toolbox.run.assert_called_once_with('open_website', {'url':'https://outlook.live.com/mail/'})
        self.factory.assert_not_called()
        self.fallback.reply.assert_not_called()

    def test_paid_prefix_for_a_known_command_still_uses_free_code_first(self):
        self.router.mode = 'local'
        self.ask('paid: opne calcualtor')
        self.toolbox.run.assert_called_once_with('open_app', {'name':'Calculator'})
        self.factory.assert_not_called()
        self.assertFalse(self.router.last_paid)

    def test_routes_and_counterfactual_savings_are_recorded_without_message_text(self):
        self.ask('opne calcualtor')
        self.ask('what is the time')
        with self.router.ledger.connect() as db:
            rows = db.execute('SELECT route,status,message_chars,estimated_saved_usd,baseline_input,baseline_output FROM message_routes').fetchall()
            columns = [row[1] for row in db.execute('PRAGMA table_info(message_routes)')]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row[0:2] == ('local_command', 'completed') for row in rows))
        self.assertTrue(all(row[3:] == (.00015, 1000, 250) for row in rows))
        self.assertNotIn('body', columns)
        self.assertIn('2 handled free', self.router.ledger.savings_summary())
        self.factory.assert_not_called()

    def test_local_mode_does_not_claim_openai_savings(self):
        self.router.mode = 'local'
        self.ask('opne calcualtor')
        with self.router.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT route,estimated_saved_usd FROM message_routes').fetchone(), ('local_command', 0))

    def test_controller_exposes_savings_property_without_changing_qml(self):
        from ano.app import Controller
        from ano.memory import Memory
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([])
        memory = Memory(self.root / 'memory.db')
        self.addCleanup(memory.close)
        self.ask('what is the time')
        controller = Controller(memory, self.router)
        self.assertIn('1 handled free', controller.savingsSummary)

    def test_missing_simple_parameters_are_clarified_free(self):
        for phrase in ('remind me to Check the fixture', 'remind me tomorrow to Check the fixture',
                       'remind me Friday at 5 to Check the fixture', 'read file', 'open file', 'calculate'):
            self.assertIn('No LLM used', self.ask(phrase))
        self.toolbox.run.assert_not_called()
        self.factory.assert_not_called()
        self.ask('save a note that Keep MiXeD data')
        self.toolbox.run.assert_called_once_with('add_note', {'text': 'Keep MiXeD data'})

    def test_explicit_clock_and_duration_variants_are_free_and_preserve_text(self):
        cases = [
            ('at 5pm', '2026-10-27T17:00:00'), ('at 5 pm', '2026-10-27T17:00:00'),
            ('at 05:30 PM', '2026-10-27T17:30:00'), ('at 5:30 p.m.', '2026-10-27T17:30:00'),
            ('at 17:30', '2026-10-27T17:30:00'), ('at 11am', '2026-10-27T11:00:00'),
            ('in 5 mins', '2026-10-27T10:05:00'), ('in 5 min', '2026-10-27T10:05:00'),
            ('in 1 hr', '2026-10-27T11:00:00'), ('in 2 hrs', '2026-10-27T12:00:00'),
            ('in an hour', '2026-10-27T11:00:00'), ('in a minute', '2026-10-27T10:01:00'),
            ('in half an hour', '2026-10-27T10:30:00')]
        body = 'Call MiRa; preserve opne calcualtor and 5PM.'
        for phrase, expected in cases:
            with self.subTest(phrase=phrase):
                self.toolbox.run.reset_mock()
                self.ask('remind me ' + phrase + ' to ' + body)
                self.toolbox.run.assert_called_once_with('add_reminder', {'text':body,'when':expected})
        self.factory.assert_not_called()
        self.fallback.reply.assert_not_called()

    def test_passed_time_needs_tomorrow_confirmation_and_fixed_date(self):
        self.assertIn('tomorrow', self.ask('remind me at 5 am to Call MiRa'))
        self.toolbox.run.assert_not_called()
        # Crossing midnight after the question must not shift the suggested date.
        self.routines.clock = lambda: datetime(2026,10,28,0,1)
        self.ask('okay')
        self.toolbox.run.assert_called_once_with('add_reminder', {'text':'Call MiRa','when':'2026-10-28T05:00:00'})
        self.factory.assert_not_called()

    def test_ambiguous_meridiem_yes_does_not_guess_or_call_a_model(self):
        self.ask('remind me at 5 to Call MiRa')
        self.assertIn('choose am or pm', self.ask('yes'))
        self.toolbox.run.assert_not_called()
        self.ask('pm')
        self.toolbox.run.assert_called_once_with('add_reminder', {'text':'Call MiRa','when':'2026-10-27T17:00:00'})
        self.factory.assert_not_called()

    def test_past_selected_meridiem_asks_before_scheduling_tomorrow(self):
        self.ask('remind me at 5 to Call MiRa')
        self.assertIn('tomorrow', self.ask('am'))
        self.toolbox.run.assert_not_called()
        self.ask('yes')
        self.toolbox.run.assert_called_once_with('add_reminder', {'text':'Call MiRa','when':'2026-10-28T05:00:00'})

    def test_invalid_reminder_times_do_not_dispatch(self):
        for phrase in ('at 25:00','at 5:99','at 13pm','at 0am'):
            self.ask('remind me ' + phrase + ' to Call MiRa')
        self.toolbox.run.assert_not_called()
        self.factory.assert_not_called()

    def test_advanced_timing_is_deferred_without_examining_payload_as_an_instruction(self):
        for phrase in ('remind me tomorrow two hours before sunset to Check the fixture',
                       'remind me on the next business day to Check the fixture'):
            self.assertIsNone(self.routines.route_reply(phrase,17))
        self.assertIn('When should', self.ask('remind me tomorrow to Call before work'))
        self.toolbox.run.assert_not_called()
        self.factory.assert_not_called()

    def test_multiline_reminder_payload_is_not_normalized_or_executed(self):
        body = 'Keep MiXeD  spaces.\nopen calculator\nand\tstop monitoring'
        for prefix in ('remind me at 5pm to ', 'remind me in 5 mins to ', 'remind me tomorrow at 5 pm to '):
            self.toolbox.run.reset_mock()
            self.ask(prefix + body)
            self.toolbox.run.assert_called_once()
            self.assertEqual(self.toolbox.run.call_args.args[0], 'add_reminder')
            self.assertEqual(self.toolbox.run.call_args.args[1]['text'],body)
        self.factory.assert_not_called()
        self.fallback.reply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
