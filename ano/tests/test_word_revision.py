from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from docx import Document
from docx.oxml import OxmlElement
from ano.plugins.word_documents import WordDocuments
from ano.plugins.workshop import Workshop
from ano.routines import RoutineStore, LocalRoutines, RoutineBrain
from ano.tools import ToolError, Toolbox


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workshop = Workshop(self.root, None)
        self.word = WordDocuments(self.workshop)

    def fixture(self, name='Source.docx'):
        doc = Document()
        doc.sections[0].header.paragraphs[0].text = 'Synthetic header'
        doc.sections[0].footer.paragraphs[0].text = 'Synthetic footer'
        doc.add_heading('Validation', level=1)
        paragraph = doc.add_paragraph()
        paragraph.add_run('Hello ')
        paragraph.add_run('Ra').bold = True
        paragraph.add_run('vi').italic = True
        paragraph.add_run(', exact body remains.')
        doc.add_table(rows=1, cols=2).rows[0].cells[0].text = 'Pending'
        stream = BytesIO()
        doc.save(stream)
        with ZipFile(BytesIO(stream.getvalue())) as source:
            parts = {info.filename: source.read(info.filename) for info in source.infolist()}
        parts['customXml/ano-fixture.xml'] = b'<fixture>Preserve this unknown part byte-for-byte.</fixture>'
        result = BytesIO()
        with ZipFile(result, 'w', ZIP_DEFLATED) as output:
            for path, payload in parts.items():
                output.writestr(path, payload)
        path = self.root / name
        path.write_bytes(result.getvalue())
        return path

    def test_fragmented_runs_replace_exactly_and_other_parts_remain_identical(self):
        source = self.fixture()
        before = source.read_bytes()
        result, _ = self.word.revise_word_document('Source', 'Ravi', 'Mira', 'Revised')
        revised = self.root / 'Revised.docx'
        self.assertIn('Saved and verified', result)
        self.assertEqual(source.read_bytes(), before)
        self.assertIn('Hello Mira, exact body remains.', self.word._read(revised.read_bytes()))
        readback = Document(revised)
        self.assertTrue(readback.paragraphs[1].runs[1].bold)
        with ZipFile(source) as original, ZipFile(revised) as output:
            self.assertEqual(set(original.namelist()), set(output.namelist()))
            for name in original.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(original.read(name), output.read(name), name)
        backups = list((self.root / '.ano-word-backups').glob('*.docx'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)

    def test_table_cell_edit_and_explicit_count_guard(self):
        source = self.fixture()
        self.word.revise_word_document('Source', 'Pending', 'Approved', 'Table revision')
        self.assertEqual(Document(self.root / 'Table revision.docx').tables[0].cell(0, 0).text, 'Approved')
        with self.assertRaisesRegex(ToolError, 'Expected 2 exact matches but found 1'):
            self.word.revise_word_document('Source', 'Pending', 'Approved', 'Wrong count', 2)
        self.assertFalse((self.root / 'Wrong count.docx').exists())

    def test_existing_output_and_missing_match_preserve_files(self):
        source = self.fixture()
        before = source.read_bytes()
        existing = self.root / 'Existing.docx'
        existing.write_bytes(b'Preserved existing output')
        with self.assertRaises(ToolError):
            self.word.revise_word_document('Source', 'Ravi', 'Mira', 'Existing')
        with self.assertRaises(ToolError):
            self.word.revise_word_document('Source', 'Not in the file', 'Mira', 'Missing')
        self.assertEqual(existing.read_bytes(), b'Preserved existing output')
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root / '.ano-word-backups').exists())

    def test_tracked_changes_and_fields_refuse_edit_before_writing(self):
        for kind in ('ins', 'fldChar'):
            source = self.fixture()
            doc = Document(source)
            element = OxmlElement('w:' + kind)
            doc.paragraphs[1]._p.append(element)
            doc.save(source)
            before = source.read_bytes()
            with self.assertRaises(ToolError):
                self.word.revise_word_document('Source', 'Ravi', 'Mira', 'Unsupported')
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((self.root / 'Unsupported.docx').exists())

    def test_source_changed_during_preflight_is_not_overwritten(self):
        source = self.fixture()
        from ano.word_revision import replace_inline
        def changed_after_transform(*args, **kwargs):
            result = replace_inline(*args, **kwargs)
            source.write_bytes(b'A concurrent replacement from another writer')
            return result
        with patch('ano.word_revision.replace_inline', side_effect=changed_after_transform):
            with self.assertRaisesRegex(ToolError, 'original changed'):
                self.word.revise_word_document('Source', 'Ravi', 'Mira', 'Concurrent')
        self.assertEqual(source.read_bytes(), b'A concurrent replacement from another writer')
        self.assertFalse((self.root / 'Concurrent.docx').exists())

    def test_direct_revision_command_uses_no_model(self):
        self.fixture()
        class NoModel:
            def reply(self, *args, **kwargs):
                raise AssertionError('Exact local revision must not call a model.')
        toolbox = Toolbox(lambda: [self.root], plugins=[self.word])
        brain = RoutineBrain(LocalRoutines(toolbox, RoutineStore(self.root / 'routines.db')), NoModel())
        response = brain.reply([{'role': 'user', 'body': 'revise word document Source: {"old":"Ravi","new":"Mira","output_name":"Revised"}'}])
        self.assertIn('No LLM used', response)
        self.assertTrue((self.root / 'Revised.docx').exists())
