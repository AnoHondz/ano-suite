import time
import unittest

from PySide6.QtCore import QCoreApplication, QObject, Signal

from ano import goals
from ano.goals import GoalRunner, read_status

app = QCoreApplication.instance() or QCoreApplication([])


class Settings:
    def __init__(self):
        self.values = {}

    def setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


class FakeController(QObject):
    replyFinished = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.sent, self.messages, self.paused = [], [], False

    def send(self, text):
        self.sent.append(text)

    def add_message(self, role, body):
        self.messages.append(body)


def settle(condition, seconds=3):
    end = time.time() + seconds
    while time.time() < end and not condition():
        app.processEvents()
        time.sleep(0.02)


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.memory = FakeController(), Settings()
        self.runner = GoalRunner(self.controller, self.memory)

    def start(self, goal='build the budget workbook'):
        self.assertIn('Goal set', self.runner.command(f'keep working on {goal}'))
        self.controller.replyFinished.emit('Goal set…', False)
        settle(lambda: self.controller.sent)

    def reply(self, text, failed=False):
        count = len(self.controller.sent)
        self.controller.replyFinished.emit(text, failed)
        settle(lambda: len(self.controller.sent) > count or self.runner.goal is None)

    def test_goal_prompts_offer_the_local_brain_no_unrelated_tools(self):
        # The local brain picks tools by keywords; a goal about a PDF must not be offered email or login tools.
        import re
        from ano.local_profile import TOOL_GROUPS
        text = goals.first_prompt('make a PDF checklist') + goals.next_prompt('make a PDF checklist', 2)
        offered = {name for pattern, names in TOOL_GROUPS if re.search(pattern, text, re.I) for name in names}
        self.assertFalse({'outlook_inbox', 'outlook_read_email', 'list_saved_logins', 'sign_in_saved_website'} & offered)

    def test_memory_is_only_touched_on_the_main_thread(self):
        import threading
        owner = threading.get_ident()
        touched = []
        self.memory.setting = lambda *a: touched.append(threading.get_ident()) or self.memory.values.get(a[0], a[1])
        self.memory.set_setting = lambda k, v: touched.append(threading.get_ident()) or self.memory.values.__setitem__(k, v)
        thread = threading.Thread(target=lambda: [self.runner.command(c) for c in
                                                  ('goal status', 'continue the goal', 'stop the goal')])
        thread.start()
        thread.join()
        settle(lambda: touched)
        self.assertTrue(all(ident == owner for ident in touched))

    def test_status_lines(self):
        self.assertEqual(read_status('did it\nSTATUS: DONE'), ('done', ''))
        self.assertEqual(read_status('STATUS: NEED YOU: sign in to Suno'), ('need', 'sign in to Suno'))
        self.assertEqual(read_status('no status line'), ('missing', ''))

    def test_two_replies_without_status_pause_the_goal(self):
        self.start()
        self.reply('did something')
        self.assertEqual(len(self.controller.sent), 2)  # one missing line is forgiven
        self.reply('did more')
        self.assertIn('did not say whether it is finished', self.controller.messages[-1])
        self.assertEqual(len(self.controller.sent), 2)

    def test_only_clear_goal_phrases_start_a_goal(self):
        self.assertIsNone(self.runner.command('work on my essay'))
        self.assertIn('Goal set', self.runner.command('work on my essay until it is done'))

    def test_it_keeps_going_until_done_and_saves_progress(self):
        self.runner.verifier = lambda goal, jobs: {'passed': True, 'references': ['synthetic independent check']}
        self.start()
        self.assertIn('Goal: build the budget workbook', self.controller.sent[0])
        self.assertEqual(self.memory.values['active_goal']['round'], 1)
        self.reply('Made the sheet.\nSTATUS: CONTINUE')
        self.assertIn('turn 2 of', self.controller.sent[1])
        self.reply('Checked totals.\nSTATUS: DONE')
        self.assertEqual(len(self.controller.sent), 2)
        self.assertIn('Goal output verified after 2 turn(s)', self.controller.messages[-1])
        self.assertIsNone(self.memory.values['active_goal'])

    def test_model_done_without_independent_evidence_keeps_goal_unfinished(self):
        self.start()
        self.reply('I saved it.\nSTATUS: DONE')
        self.assertIn('Completion unverified', self.controller.messages[-1])
        self.assertIsNotNone(self.memory.values['active_goal'])
        self.assertFalse(self.memory.values['last_goal_verification']['passed'])

    def test_status_inside_quoted_content_does_not_finish_a_goal(self):
        self.assertEqual(read_status('Example: STATUS: DONE\nStill working.'), ('missing', ''))
        self.assertEqual(read_status('STATUS: DONE\nActually more work remains.'), ('missing', ''))

    def test_repairable_output_mismatch_continues_with_check_feedback(self):
        checks=iter([{'passed':False,'repairable':True,'reason':'Missing verified requested format: .pdf'},
                     {'passed':True,'references':['actual output read-back']}])
        self.runner.verifier=lambda goal,jobs:next(checks)
        self.start('create a PDF')
        self.reply('Made a DOCX.\nSTATUS: DONE')
        self.assertIn('Missing verified requested format: .pdf',self.controller.sent[-1])
        self.assertIn('Do not duplicate completed work',self.controller.sent[-1])
        self.reply('Created and checked the PDF.\nSTATUS: DONE')
        self.assertIsNone(self.memory.values['active_goal'])

    def test_verification_repairs_are_bounded(self):
        self.runner.verifier=lambda goal,jobs:{'passed':False,'repairable':True,'reason':'Missing PDF'}
        self.start('create a PDF')
        for _ in range(3):self.reply('STATUS: DONE')
        self.assertEqual(len(self.controller.sent),3)
        self.assertIn('Completion unverified',self.controller.messages[-1])

    def test_it_stops_when_it_needs_the_user_and_can_be_continued(self):
        self.start()
        self.reply('STATUS: NEED YOU: sign in to Suno')
        self.assertIn('waiting for you: sign in to Suno', self.controller.messages[-1])
        self.assertIn('Picking up the goal', self.runner.command('continue the goal'))
        self.controller.replyFinished.emit('Picking up…', False)
        settle(lambda: len(self.controller.sent) == 2)
        self.assertIn('turn 2 of', self.controller.sent[-1])

    def test_two_failures_or_a_stop_end_it(self):
        self.start()
        self.reply('', failed=True)
        self.reply('', failed=True)
        self.assertIn('two turns in a row failed', self.controller.messages[-1])
        self.assertIn('Goal stopped', self.runner.command('stop the goal'))
        self.assertIsNone(self.memory.values['active_goal'])

    def test_a_local_goal_keeps_every_turn_on_the_free_brain(self):
        self.assertIn('free local AI', self.runner.command('keep working on tidy my notes', local=True))
        self.controller.replyFinished.emit('Goal set…', False)
        settle(lambda: self.controller.sent)
        self.reply('step one\nSTATUS: CONTINUE')
        self.assertTrue(all(prompt.startswith('local: ') for prompt in self.controller.sent))
        self.assertTrue(self.memory.values['active_goal']['local'])

    def test_a_stop_between_turns_sends_nothing_more(self):
        self.start()
        self.controller.replyFinished.emit('', True)  # the user stopped the running turn…
        self.runner.command('stop the goal')           # …and their "stop the goal" is handled before the next turn
        settle(lambda: self.runner.goal is None)
        self.assertEqual(len(self.controller.sent), 1)
        self.assertIsNone(self.memory.values['active_goal'])

    def test_the_turn_limit_is_respected(self):
        self.start()
        for _ in range(goals.MAX_ROUNDS):
            self.reply('working\nSTATUS: CONTINUE')
        self.assertEqual(len(self.controller.sent), goals.MAX_ROUNDS)
        self.assertIn('turn limit', self.controller.messages[-1])


if __name__ == '__main__':
    unittest.main()
