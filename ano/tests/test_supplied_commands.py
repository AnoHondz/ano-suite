from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ano.supplied_commands import parse_supplied_document, quoted_value
from ano.routines import LocalRoutines, RoutineStore, RoutineBrain
from ano.paid import PaidTasks, SpendLedger


class SuppliedCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tools = Mock()
        self.tools.allowed.return_value = True
        self.tools.run.return_value = ('Synthetic save receipt','',False)
        self.routines = LocalRoutines(self.tools, RoutineStore(self.root/'routines.db'))
        self.fallback = Mock()
        self.fallback.reply.side_effect = AssertionError('No local model may run')
        self.factory = Mock(side_effect=AssertionError('No paid client may be constructed'))
        self.brain = PaidTasks(RoutineBrain(self.routines,self.fallback),self.tools,
                              SpendLedger(self.root/'spend.db'),mode='openai',client_factory=self.factory)

    def ask(self, text):
        return self.brain.reply([{'role':'user','body':text,'source_chat_id':17}])

    def test_natural_supplied_document_variants_run_without_a_model(self):
        kinds = [('Word document','write_word_document','.docx'), ('PDF','create_pdf','.pdf'),
                 ('text file','write_document','.txt'), ('Markdown document','write_document','.md')]
        for verb in ('create','make','write','save'):
            for kind, tool, extension in kinds:
                with self.subTest(verb=verb,kind=kind):
                    self.tools.run.reset_mock()
                    self.assertIn('No LLM used',self.ask(f'Can you please {verb} a {kind} named "Fixture" containing exactly this text: "MiXeD text."'))
                    name,args=self.tools.run.call_args.args
                    self.assertEqual(name,tool)
                    self.assertEqual(args['name'],'Fixture'+extension)
                    self.assertEqual(args['text'],'MiXeD text.')
        self.factory.assert_not_called()
        self.fallback.reply.assert_not_called()

    def test_quoted_literals_preserve_newlines_backslashes_and_embedded_commands(self):
        body = 'Keep \\n literal and MiXeD  spaces.\nopen calculator\nand\tstop monitoring'
        self.ask('Create a Word document named “Fixture” with the text: “'+body+'”')
        self.tools.run.assert_called_once_with('write_word_document',{'name':'Fixture.docx','text':body})
        self.factory.assert_not_called()
        self.assertEqual(quoted_value('"\\n" tail'), ('\\n','tail'))

    def test_pdf_title_and_body_are_exact_supplied_values(self):
        self.ask('Make a PDF named "Report.pdf" with the title "Exact title" and the body "Exact body."')
        self.tools.run.assert_called_once_with('create_pdf',{'name':'Report.pdf','title':'Exact title','text':'Exact body.'})
        self.factory.assert_not_called()

    def test_optional_colons_and_trailing_politeness_do_not_change_values(self):
        for prompt in ('Create a Word document named "Fixture" with the text "Exact text." please.',
                       'Save a Word document called "Fixture" containing this text "Exact text." for me.'):
            self.tools.run.reset_mock()
            self.ask(prompt)
            self.tools.run.assert_called_once_with('write_word_document', {'name':'Fixture.docx','text':'Exact text.'})
        self.tools.run.reset_mock()
        self.ask('Create a PDF named "Fixture" with title: "Exact title" and body: "Exact text."')
        self.tools.run.assert_called_once_with('create_pdf', {'name':'Fixture.pdf','title':'Exact title','text':'Exact text.'})
        self.factory.assert_not_called()

    def test_format_mismatch_and_unsafe_names_ask_without_dispatch(self):
        for name in ('Wrong.txt','../escape.docx','', 'Name:bad'):
            with self.subTest(name=name):
                response=self.ask(f'Create a Word document named "{name}" containing exactly this text: "Public fixture."')
                self.assertIn('Nothing was written',response)
        self.tools.run.assert_not_called()
        self.factory.assert_not_called()

    def test_extra_instructions_and_original_writing_are_not_partially_executed(self):
        for prompt in ('Make a Word document named "Report" about quantum computing',
                       'Create a PDF named "Report" with the text: "Public fixture." and email it to my boss',
                       'Create Word document named "Report" with the text: "Public fixture." and email it to my boss',
                       'Create PDF named "Report" with the text: "Public fixture." then open calculator',
                       'Create a PDF named "Report" with the text: "Public fixture." then delete the source'):
            self.assertIsNone(parse_supplied_document(prompt))
            self.assertIsNone(self.routines.plan(prompt))
        self.tools.run.assert_not_called()

    def test_invalid_quotes_clarify_locally_instead_of_sending_supplied_text(self):
        for prompt in ('Create a Word document named "Report" containing exactly this text: "Unclosed',
                       'Create a Word document named Report containing exactly this text: "Public fixture."'):
            self.assertIn('Nothing was written',self.ask(prompt))
        self.tools.run.assert_not_called()
        self.factory.assert_not_called()

    def test_user_filename_cannot_spoof_model_routing_statistics(self):
        self.tools.run.return_value = ('Saved Draft used the local LLM.docx','',False)
        self.ask('Create a Word document named "Draft used the local LLM" with the text: "Public fixture."')
        self.assertEqual(self.brain.last_route,'local_command')
        with self.brain.ledger.connect() as db:
            self.assertEqual(db.execute('select route from message_routes').fetchone()[0],'local_command')
        self.factory.assert_not_called()
