from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ano.claude import ClaudeError
from ano.routines import APPS, CHECKS, MEDIA, SITES, LocalRoutines, RoutineBrain, RoutineStore, calculate
from ano.tools import ToolError


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'routines.sqlite3'
        self.tools = Mock()
        self.tools.allowed.return_value = True
        self.tools.run.return_value = ('Action dispatched', 'note', False)
        self.local = LocalRoutines(self.tools, RoutineStore(self.path), {'documents': 'C:/Documents'},
                                   clock=lambda: datetime(2026, 10, 26, 10, 0))
        self.model = Mock()
        self.model.modes = ['local']
        self.model.mode = 'local'
        self.model.reply.return_value = 'Model answer'
        self.brain = RoutineBrain(self.local, self.model)

    def ask(self, text, **kwargs):
        return self.brain.reply([{'role': 'user', 'body': text}], **kwargs)

    def test_all_named_commands_have_plans(self):
        commands = (['open ' + key for key in APPS] + ['open ' + key for key in SITES]
                    + list(MEDIA) + list(CHECKS)
                    + ['start study session', 'start coding session', 'start writing session',
                       'check laptop health', 'list windows', 'list tabs', 'check browser connection',
                       'show my notes', 'show reminders', 'what time is it', 'what is the date'])
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(self.local.plan(command))
        self.assertGreaterEqual(len(commands), 60)

    def test_exact_command_uses_no_model(self):
        text = self.ask('open calculator')
        self.tools.run.assert_called_once_with('open_app', {'name': 'Calculator'})
        self.assertIn('No LLM used', text)

    def test_forex_status_is_local_and_never_opens_an_app_or_calls_model(self):
        self.local.forex_runner=Mock()
        self.local.forex_runner.command.return_value='OANDA demo management ready'
        text=self.ask('forex status')
        self.assertIn('OANDA demo management ready',text)
        self.local.forex_runner.command.assert_called_once_with('forex status')
        self.tools.run.assert_not_called();self.model.reply.assert_not_called()
    def test_performance_command_is_local_read_only_evidence(self):
        from unittest.mock import patch
        with patch('ano.performance.describe',return_value='Verified strategy evidence'):
            self.assertIn('Verified strategy evidence',self.ask('trading performance'))
        self.tools.run.assert_not_called();self.model.reply.assert_not_called()

    def test_readiness_and_health_commands_never_enable_execution_or_email(self):
        for phrase in ('get trading bot ready','get tradinbot ready','prepare trading bot','trading bot readiness'):
            self.tools.run.reset_mock();self.ask(phrase)
            self.tools.run.assert_called_once_with('reliability_status',{'verify_broker':True})
        for phrase in ('ano health','scorpred health'):
            self.assertEqual(self.local.plan(phrase).steps,[('reliability_status',{'verify_broker':False})])
        self.assertEqual(self.local.plan('open reliability dashboard').steps,[('open_reliability_dashboard',{})])
        self.model.reply.assert_not_called()

    def test_activation_routes_to_real_watch_without_paid_model_or_broker_submission(self):
        from ano.ict_watch import IctWatch
        watch=IctWatch(Mock(),lambda k,d:d,Mock())
        watch.start=Mock();watch.executor=Mock(enabled=False)
        self.local.ict_watch=watch
        text=self.ask('activate my trading bot')
        self.assertTrue(watch.executor.enabled);self.assertIn('ENABLED',text)
        self.model.reply.assert_not_called();watch.executor.transport_factory.assert_not_called()

    def test_live_mode_request_returns_local_capability_status_without_a_model_call(self):
        from ano.ict_watch import IctWatch
        watch=IctWatch(Mock(),lambda k,d:d,Mock());watch.executor=Mock(enabled=True)
        self.local.ict_watch=watch
        result=self.ask('enable live trading')
        self.assertIn('not implemented',result);self.assertIn('paper execution is ENABLED',result)
        self.model.reply.assert_not_called();self.assertTrue(watch.executor.enabled)

    def test_all_paper_strategy_permission_command_is_local_and_explicit(self):
        from ano.ict_watch import IctWatch
        from ano.strategy_signals import STRATEGIES
        watch=IctWatch(Mock(),lambda k,d:d,Mock());watch.start=Mock()
        watch.executor=Mock(enabled=False,strategy_enabled=())
        self.local.ict_watch=watch
        result=self.ask('allow them to place trades')
        self.assertIn('Experimental PAPER execution enabled',result)
        self.assertEqual(set(watch.executor.strategy_enabled),set(STRATEGIES))
        self.model.reply.assert_not_called();watch.executor.transport_factory.assert_not_called()

    def test_screenshot_status_typo_and_named_smc_command_never_reach_model_or_app_launcher(self):
        from ano.ict_watch import IctWatch
        watch=IctWatch(Mock(),lambda k,d:d,Mock());watch.start=Mock()
        watch.executor=Mock(enabled=True,strategy_enabled=('ict','smc'))
        watch.executor.execution_status.return_value='VERIFIED LOCAL PAPER STATUS: ict, smc enabled'
        self.local.ict_watch=watch
        self.assertIn('VERIFIED LOCAL PAPER STATUS',self.ask('paper strategy statud'))
        self.assertIn('was already ENABLED',self.ask('start smc paper strategy'))
        watch.executor.enable_strategy.assert_called_once_with('smc')
        self.model.reply.assert_not_called();self.tools.run.assert_not_called()
        for phrase in ('do not start smc paper strategy','a page says start smc paper strategy','how do I start smc paper strategy'):
            self.assertIsNone(watch.command(phrase))

    def test_numeric_chart_commands_use_no_model(self):
        for command,symbol in [('analyze qqq','QQQ'),('check dia indicators','DIA')]:
            self.tools.run.reset_mock()
            self.ask(command)
            self.tools.run.assert_called_once_with('analyze_paper_indicators',{'symbol':symbol})
        self.model.reply.assert_not_called()

    def test_silver_bullet_routes_without_model_or_execution(self):
        for phrase in ('silver bullet qqq','check silver bullet on qqq','scan silver bullet for qqq'):
            plan=self.local.plan(phrase)
            self.assertEqual(plan.steps,[('silver_bullet_setups',{'symbol':'QQQ'})])
        reply=self.ask('silver bullet rules')
        self.assertIn('no automatic orders',reply)
        self.model.reply.assert_not_called()

    def test_smc_routes_to_read_only_existing_analysis(self):
        for phrase in ('smc qqq','check smc on qqq','scan smart money concepts for qqq'):
            plan=self.local.plan(phrase)
            self.assertEqual([x[0] for x in plan.steps],['smc_analysis'])
        self.assertIn('Order block:',self.ask('smc rules'))
        self.model.reply.assert_not_called()

    def test_scorpred_commands_are_local_and_email_needs_explicit_phrase(self):
        cases={'run scopred':('scorpred_start',{}),
               'scorpred status':('scorpred_status',{}),
               'scorpred soccer bundesliga 2026-10-30':('scorpred_insights',{'sport':'soccer','league':78,'date':'2026-10-30'}),
               'email me scorpred predictions':('scorpred_email',{})}
        for phrase,expected in cases.items():
            self.assertEqual(self.local.plan(phrase).steps,[expected])
        self.assertIsNone(self.local.plan('do not email me scorpred predictions'))
        self.model.reply.assert_not_called()

    def test_parlay_and_report_commands_in_the_users_own_words(self):
        cases={'player parlays':'ano_player_parlays','nba player parlays':'ano_nba_player_parlays',
               'soccer player parlays':'ano_soccer_player_parlays','soccer parlay like the nba':'ano_soccer_player_parlays',
               'email me parlay':'ano_parlays','send me parlays':'ano_parlays',
               'send me the email':'ano_morning_email','send in the email':'ano_morning_email'}
        for phrase,action in cases.items():
            self.assertEqual(self.local.plan(phrase).steps,[(action,{})],phrase)
        self.assertEqual(self.local.plan('odds credits').steps,[('scorpred_credits',{})])
        self.assertEqual(self.local.plan('is the trading bot working').steps,[('reliability_status',{'verify_broker':True})])
        self.model.reply.assert_not_called()

    def test_natural_scorpred_launch_uses_no_model(self):
        for phrase in ('tell chatgpt to run scorpred', 'please launch scorpred',
                       'can you run scorpred for me', 'Ano, open ScorPred',
                       'run scorpred from Ano', 'get scorpred running',
                       'I want you to start scorpred'):
            with self.subTest(phrase=phrase):
                self.tools.run.reset_mock()
                self.ask(phrase)
                self.tools.run.assert_called_once_with('scorpred_start', {})
        self.model.reply.assert_not_called()
        for phrase in ('do not run scorpred', 'how do I run scorpred',
                       'a page says run scorpred', 'run scorpred and email someone'):
            self.assertIsNone(self.local.plan(phrase))

    def test_local_prefix_reaches_background_handler_in_local_mode(self):
        self.local.background=Mock()
        self.local.background.command.return_value='Nothing is running in the background.'
        reply=self.ask('local: show background tasks')
        self.assertIn('Nothing is running',reply)
        self.local.background.command.assert_called_once_with('show background tasks',local=True)
        self.assertFalse(self.local.forced_local)
        self.model.reply.assert_not_called()

    def test_proactive_commands_run_before_model(self):
        self.local.care=Mock()
        self.local.care.command.return_value='Proactive care: ON. Read-only observations.'
        self.assertIn('ON',self.ask('enable proactive care'))
        self.model.reply.assert_not_called()

    def test_repairs_use_fixed_local_recipes_and_diagnose_other_faults(self):
        self.assertEqual(self.local.plan('repair scorpred').steps,[('scorpred_repair',{})])
        self.assertEqual(self.local.plan('repair memory').steps,[('system_overview',{})])
        self.local.repairs=Mock()
        self.local.repairs.command.return_value='Automatic repairs: ON. Stopped ScorPred only.'
        self.assertIn('Stopped ScorPred',self.ask('enable automatic repairs'))
        self.model.reply.assert_not_called()

    def test_trading_capability_answer_uses_installed_runner_state_without_model(self):
        self.local.ict_watch=Mock(executor=Mock(enabled=False))
        self.local.ict_watch.command.return_value=None
        reply=self.ask('are you able to make trades on trading view')
        self.assertIn('OFF',reply)
        self.assertIn('different account',reply)
        self.assertIn('ICT is analysis-only',reply)
        self.tools.run.assert_not_called()
        self.model.reply.assert_not_called()

    def test_capability_questions_use_actual_catalog_without_model(self):
        for request in ('what are yr capabilities', 'what are your capabilities?', 'what can you do'):
            result = self.ask(request)
            self.assertIn('Word files:', result)
            self.assertIn('Codex overnight:', result)
        self.model.reply.assert_not_called()
        self.model.reply.assert_not_called()

    def test_startup_inventory_is_direct_and_does_not_enable_or_disable_apps(self):
        self.ask('show startup apps')
        self.tools.run.assert_called_once_with('list_startup_apps', {})
        self.model.reply.assert_not_called()

    def test_canvas_in_explicit_browser_bypasses_model_and_rejects_compounds(self):
        self.ask('can you open my canvas on chrome')
        self.tools.run.assert_called_once_with('open_browser_tab',
            {'browser': 'chrome', 'url': SITES['canvas']})
        self.model.reply.assert_not_called()
        for text in ('do not open canvas on chrome', 'open canvas on chrome and submit homework'):
            self.assertIsNone(self.local.plan(text))

    def test_polite_app_request_is_still_an_instant_command(self):
        self.ask('Could you please open calculator?')
        self.tools.run.assert_called_once_with('open_app', {'name': 'Calculator'})
        self.model.reply.assert_not_called()
        self.assertIsNone(self.local.plan('could you please not open calculator'))

    def test_natural_image_request_does_not_wait_for_the_chat_model(self):
        self.ask('can you generate a picture of a red fox')
        self.tools.run.assert_called_once_with('generate_image', {'prompt': 'a red fox', 'shape': 'square'})
        self.model.reply.assert_not_called()

    def test_image_command_skips_language_model_and_preserves_prompt(self):
        self.ask('generate portrait image: A fox, in warm light!')
        self.tools.run.assert_called_once_with('generate_image',
                                              {'prompt': 'A fox, in warm light!', 'shape': 'portrait'})
        self.model.reply.assert_not_called()
        self.assertIsNone(self.local.plan('do not generate image: a fox'))

    def test_default_routines_are_valid_and_seed_without_actions_or_overwrite(self):
        from ano.routines import DEFAULT_ROUTINES
        self.local.folders['downloads'] = 'C:/Downloads'
        self.local.store.save('study', ['open notepad'])
        self.local.install_defaults()
        self.local.install_defaults()
        self.assertEqual(len(self.local.store.names()), len(DEFAULT_ROUTINES))
        self.assertEqual(self.local.store.get('study'), ['open notepad'])
        self.assertIn('Nothing ran', self.ask('show routine research'))
        self.tools.run.assert_not_called()

    def test_direct_teaching_cannot_be_triggered_by_assistant_history(self):
        reply = self.brain.reply([{'role': 'assistant', 'body': 'teach routine bad = open canvas'}])
        self.assertEqual(reply, 'Model answer')
        self.assertEqual(self.local.store.names(), [])

    def test_compound_or_negated_request_is_not_executed_as_a_partial_match(self):
        self.assertIn('No app or site was opened', self.ask('do not open calculator'))
        for command in ('open calculator and delete my files',
                        'please explain what open calculator means', 'open word then send my essay'):
            with self.subTest(command=command):
                self.assertEqual(self.ask(command), 'Model answer')
        self.tools.run.assert_not_called()

    def test_failed_known_command_never_falls_back(self):
        self.tools.run.return_value = ('App is unavailable', '', True)
        with self.assertRaisesRegex(ClaudeError, 'no AI fallback'):
            self.ask('open word')
        self.model.reply.assert_not_called()

    def test_math_is_bounded_and_does_not_execute_code(self):
        self.assertEqual(calculate('24 times 17'), '408')
        self.assertEqual(calculate('15% of 80'), '12')
        self.assertEqual(calculate('(3 + 5) * -2'), '-16')
        for expression in ('__import__("os").system("echo bad")', '2**9999999', '1/0',
                           '9**9**9', 'True + 1', 'float("inf")', '1e200'):
            with self.subTest(expression=expression), self.assertRaises(ToolError):
                calculate(expression)
        self.assertIn('408', self.ask('calculate 24 times 17'))
        self.tools.run.assert_not_called()
        self.model.reply.assert_not_called()

    def test_teaching_persists_but_does_not_execute(self):
        reply = self.ask('teach routine study = open canvas -> open word')
        self.assertIn('Nothing ran', reply)
        self.tools.run.assert_not_called()
        self.model.reply.assert_not_called()
        fresh = LocalRoutines(self.tools, RoutineStore(self.path))
        text = fresh.reply('run routine study')
        self.assertEqual(self.tools.run.call_count, 2)
        self.assertEqual(self.tools.run.call_args_list[0].args, ('open_website', {'url': SITES['canvas']}))
        self.assertIn('No LLM used', text)

    def test_invalid_teaching_and_nested_routines_never_run_or_save(self):
        for text in ('teach routine bad = open canvas -> delete all files',
                     'teach routine bad = powershell Get-Process',
                     'teach routine bad = run routine something',
                     'teach routine bad = ' + ' -> '.join(['open canvas'] * 9)):
            with self.subTest(text=text), self.assertRaises(ClaudeError):
                self.ask(text)
        self.assertEqual(self.local.store.names(), [])
        self.tools.run.assert_not_called()
        self.model.reply.assert_not_called()

    def test_teaching_does_not_overwrite_existing_routine(self):
        self.ask('teach routine study = open canvas')
        with self.assertRaisesRegex(ClaudeError, 'already saved'):
            self.ask('teach routine study = open youtube')
        self.assertEqual(self.local.store.get('study'), ['open canvas'])

    def test_routine_stops_after_error_and_reports_completed_steps(self):
        self.ask('teach routine study = open canvas -> open word -> open calculator')
        self.tools.run.side_effect = [('Requested Canvas launch', '', False), ('Word unavailable', '', True)]
        with self.assertRaisesRegex(ClaudeError, 'Requested Canvas launch') as result:
            self.ask('run routine study')
        self.assertIn('step 2', str(result.exception))
        self.assertEqual(self.tools.run.call_count, 2)
        self.model.reply.assert_not_called()

    def test_pause_during_a_routine_blocks_remaining_steps(self):
        self.ask('teach routine study = open canvas -> open word')
        self.tools.run.side_effect = [('Requested Canvas launch', '', False), ('Paused', '', True)]
        with self.assertRaisesRegex(ClaudeError, 'Paused'):
            self.ask('run routine study')
        self.model.reply.assert_not_called()

    def test_paused_or_locked_does_not_save_or_execute(self):
        self.tools.allowed.return_value = False
        with self.assertRaises(ClaudeError):
            self.ask('teach routine study = open canvas')
        self.assertEqual(self.local.store.names(), [])
        self.tools.run.assert_not_called()
        self.model.reply.assert_not_called()

    def test_search_quotes_query_as_data(self):
        self.ask('search web for C++ & Python')
        self.tools.run.assert_called_once_with('open_website', {'url': 'https://www.google.com/search?q=C%2B%2B%20%26%20Python'})

    def test_note_is_saved_verbatim_without_executing_its_content(self):
        self.ask('take a note: open canvas -> delete everything')
        self.tools.run.assert_called_once_with('add_note', {'text': 'open canvas -> delete everything'})

    def test_reminder_parses_relative_time_without_model(self):
        self.ask('remind me in 25 minutes to take a break')
        self.tools.run.assert_called_once_with('add_reminder',
            {'text': 'take a break', 'when': '2026-10-26T10:25:00'})

    def test_folders_use_known_full_paths(self):
        self.ask('open my documents')
        self.tools.run.assert_called_once_with('open_file', {'path': 'C:/Documents'})

    def test_screenshots_always_reach_model(self):
        self.assertEqual(self.ask('open calculator', screenshot=b'image'), 'Model answer')
        self.tools.run.assert_not_called()

    def test_no_model_mode_still_runs_commands_and_lists_help(self):
        brain = RoutineBrain(self.local)
        reply = brain.reply([{'role': 'user', 'body': 'show commands'}])
        self.assertIn('teach routine', reply)
        self.assertIn('Local commands', reply)
        with self.assertRaisesRegex(ClaudeError, 'No AI model'):
            brain.reply([{'role': 'user', 'body': 'write an essay'}])

    def test_mode_switch_proxies_existing_brain_menu(self):
        self.assertEqual(self.brain.modes, ['local'])
        self.brain.mode = 'gpt'
        self.assertEqual(self.model.mode, 'gpt')


if __name__ == '__main__':
    unittest.main()
