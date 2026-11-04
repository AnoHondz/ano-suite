from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from unittest.mock import Mock

from ano.plugins.word_documents import WordDocuments
from ano.plugins.workshop import Workshop
from ano.routines import LocalRoutines, RoutineBrain, RoutineStore
from ano.tools import Toolbox, ToolError
from ano.local_profile import task_tools


class WordWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workshop = Workshop(self.root / 'workspace', Mock())
        self.opener = Mock()
        self.word = WordDocuments(self.workshop, opener=self.opener)
        self.toolbox = Toolbox(lambda: [self.root], plugins=[self.workshop, self.word])
        self.routines = LocalRoutines(self.toolbox, RoutineStore(self.root / 'routines.db'))
        self.fallback = Mock()
        self.brain = RoutineBrain(self.routines, self.fallback)

    def test_exact_failed_request_now_finishes_without_ai(self):
        request = 'opene microsoft word and make sure you make a document on everything on Ano'
        response = self.brain.reply([{'role': 'user', 'body': request}])
        self.assertIn('Saved and verified Word document', response)
        self.assertIn('No LLM used', response)
        self.fallback.reply.assert_not_called()
        self.opener.assert_called_once()
        path = Path(self.opener.call_args.args[0])
        content, _ = self.word.read_word_document(path.name)
        self.assertIn('PROJECT AND CAPABILITY REPORT', content)
        self.assertIn('not proof', content)

    def test_literal_body_preserved_and_roundtrip_verified(self):
        text = 'First  line — Unicode.\n\nDo not alter opene inside this body.'
        self.word.write_word_document('Example', text)
        actual, _ = self.word.read_word_document('Example.docx')
        self.assertEqual(actual, '[Untrusted document text]\n' + text)
        self.assertIn('Example.docx', self.workshop.list_documents()[0])
        self.opener.assert_not_called()

    def test_no_overwrite(self):
        self.word.write_word_document('Example', 'Original')
        with self.assertRaises(ToolError):
            self.word.write_word_document('Example', 'Replacement')
        self.assertIn('Original', self.word.read_word_document('Example')[0])

    def test_reads_paragraphs_and_table_cells_in_document_order(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph('Before')
        table = doc.add_table(rows=2, cols=2)
        for row, values in zip(table.rows, [('Item', 'Count'), ('Apples', '4')]):
            for cell, value in zip(row.cells, values):
                cell.text = value
        doc.add_paragraph('After')
        data = BytesIO()
        doc.save(data)
        self.assertEqual(self.word._read(data.getvalue()), 'Before\nItem\tCount\nApples\t4\nAfter')
        path = self.workshop.root / 'Table fixture.docx'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.getvalue())
        text, _ = self.word.read_word_document(path.name)
        self.assertEqual(text, '[Untrusted document text]\nBefore\nItem\tCount\nApples\t4\nAfter')

    def test_reads_nested_tables_and_merged_cells_once(self):
        from docx import Document
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).merge(table.cell(0, 1)).text = 'Merged heading'
        table.cell(1, 0).text = 'Outer'
        nested = table.cell(1, 1).add_table(rows=1, cols=1)
        nested.cell(0, 0).text = 'Nested fact'
        data = BytesIO()
        doc.save(data)
        text = self.word._read(data.getvalue())
        self.assertEqual(text.count('Merged heading'), 1)
        self.assertIn('Outer', text)
        self.assertIn('Nested fact', text)

    def test_invalid_names_and_xml_do_not_save(self):
        for name in ('../outside', 'C:\\outside', 'CON', 'file.docm', '.hidden'):
            with self.assertRaises(ToolError):
                self.word.write_word_document(name, 'text')
        with self.assertRaises(ToolError):
            self.word.write_word_document('Invalid', 'bad\x00text')
        self.assertFalse((self.workshop.root / 'Invalid.docx').exists())

    def test_open_failure_keeps_verified_file_and_reports_it(self):
        self.opener.side_effect = OSError('No association')
        result, _ = self.word.write_word_document('Example', 'Saved once', True)
        self.assertIn('saved file is intact', result)
        self.assertIn('Saved once', self.word.read_word_document('Example')[0])

    def test_pause_and_folder_restrictions(self):
        self.workshop.allowed = lambda: False
        with self.assertRaises(ToolError):
            self.word.create_ano_report()
        self.workshop.allowed = lambda: True
        self.workshop.roots = lambda: [self.root / 'different']
        with self.assertRaises(ToolError):
            self.word.create_ano_report()

    def test_routing_advertises_needed_tools(self):
        definitions = self.word.definitions() + [
            {'name': 'open_app', 'description': 'Open app', 'input_schema': {'type': 'object'}},
            {'name': 'draft_document', 'description': 'Draft', 'input_schema': {'type': 'object'}}]
        tools = {item['function']['name'] for item in task_tools(definitions,
            'opene microsoft word and make a document about Ano')}
        self.assertTrue({'open_app', 'create_ano_report', 'write_word_document', 'draft_document'} <= tools)

    def test_simple_typo_only_executes_known_whole_request(self):
        for request in ('opene microsoft word', 'openn word', 'please oppen word', 'launch word'):
            self.assertEqual(self.routines.plan(request).steps, [('open_app', {'name': 'Word'})])
        for request in ('do not opene word', 'opene word and delete my files',
                        'make a report about Ano and send it to everyone'):
            self.assertIsNone(self.routines.plan(request))

    def test_word_command_preserves_exact_text(self):
        plan = self.routines.plan('write word document Test: Text\n\nMore opene.')
        self.assertEqual(plan.steps, [('write_word_document', {'name': 'Test', 'text': 'Text\n\nMore opene.'})])

    def test_local_word_draft_is_labeled_and_saved_without_overwrite(self):
        self.workshop.writer = Mock(return_value='A short draft based on supplied facts.')
        plan = self.routines.plan('draft word document Study about a study checklist')
        self.assertTrue(plan.uses_model)
        response = self.brain.reply([{'role': 'user', 'body': 'draft word document Study about a study checklist'}])
        self.assertIn('Draft used the local LLM', response)
        text, _ = self.word.read_word_document('Study')
        self.assertIn('AI-generated draft', text)
        self.assertIn('A short draft based on supplied facts.', text)
        self.workshop.writer.assert_called_once()
        with self.assertRaises(ToolError):
            self.word.draft_word_document('Study', 'another topic')
        self.workshop.writer.assert_called_once()

    def test_missing_writer_or_pause_does_not_save_a_draft(self):
        with self.assertRaises(ToolError):
            self.word.draft_word_document('Missing', 'a topic')
        def paused_writer(prompt):
            self.workshop.allowed = lambda: False
            return 'Draft text'
        self.workshop.writer = paused_writer
        with self.assertRaises(ToolError):
            self.word.draft_word_document('Paused', 'a topic')
        self.assertFalse((self.workshop.root / 'Paused.docx').exists())
