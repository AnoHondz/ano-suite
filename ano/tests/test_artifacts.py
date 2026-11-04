from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
from pypdf import PdfReader
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop
from ano.tools import ToolError
from ano.routines import LocalRoutines, RoutineBrain, RoutineStore
from ano.tools import Toolbox


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workshop = Workshop(self.root / 'outputs', None)
        self.plugin = Artifacts(self.workshop)

    def test_spreadsheet_preserves_values_and_chart_and_blocks_overwrite(self):
        result, _ = self.plugin.create_spreadsheet('Example', ['Month', 'Units'], [['Jan', 20], ['Feb', 35]], 2)
        report = json.loads(result)
        book = load_workbook(report['path'])
        self.assertEqual(list(book.active.values), [('Month', 'Units'), ('Jan', 20), ('Feb', 35)])
        self.assertEqual(len(book.active._charts), 1)
        original = Path(report['path']).read_bytes()
        with self.assertRaises(ToolError):
            self.plugin.create_spreadsheet('Example', ['Other'], [[5]])
        self.assertEqual(Path(report['path']).read_bytes(), original)

    def test_formula_like_data_is_literal_not_executed(self):
        result, _ = self.plugin.create_spreadsheet('Literal', ['Input'], [['=HYPERLINK("https://example.com","x")'], ['+123'], ['@SUM(A1)']])
        sheet = load_workbook(json.loads(result)['path']).active
        self.assertTrue(all(cell.data_type == 's' for row in sheet for cell in row))
        self.assertEqual(sheet['A2'].value, '=HYPERLINK("https://example.com","x")')

    def test_invalid_data_and_revocation_write_nothing(self):
        for rows in ([[float('nan')]], [['x', 2]], [[{'nested': 1}]]):
            with self.assertRaises(ToolError):
                self.plugin.create_spreadsheet('Invalid', ['Column'], rows)
        with self.assertRaises(ToolError):
            self.plugin.create_spreadsheet('BadChart', ['Name', 'Count'], [['A', 'text']], 2)
        self.workshop.allowed = lambda: False
        with self.assertRaises(ToolError):
            self.plugin.create_pdf('Paused', 'Title', 'Body')
        self.assertEqual(list(self.workshop.root.glob('*')), [])

    def test_pdf_pagination_readback_and_literal_markup(self):
        text = 'Literal <script> text & facts.\n' + '\n'.join('Line %s: exact supplied content.' % i for i in range(120))
        result, _ = self.plugin.create_pdf('Report', 'Example report', text)
        report = json.loads(result)
        reader = PdfReader(report['path'])
        self.assertGreater(len(reader.pages), 1)
        extracted = '\n'.join(p.extract_text() for p in reader.pages)
        self.assertIn('Literal <script> text & facts.', extracted)
        self.assertIn('Line 119:', extracted)
        with self.assertRaises(ToolError):
            self.plugin.create_pdf('Report', 'Different', 'Do not replace')

    def test_direct_commands_create_actual_files_without_model(self):
        class NoModel:
            def reply(self, *args, **kwargs):
                raise AssertionError('This exact command must not require a model.')
        toolbox = Toolbox(lambda: [self.root], plugins=[self.plugin])
        brain = RoutineBrain(LocalRoutines(toolbox, RoutineStore(self.root / 'routines.db')), NoModel())
        for command in ['create pdf Fixture: Exact supplied body.',
                        'create spreadsheet Fixture: {"headers":["Item","Count"],"rows":[["A",3]],"chart_column":2}',
                        'create presentation Fixture: {"title":"My deck","slides":[{"title":"A slide","bullets":["An exact fact"]}]}']:
            answer = brain.reply([{'role': 'user', 'body': command}])
            self.assertIn('No LLM used', answer)
        self.assertTrue((self.workshop.root / 'Fixture.pdf').exists())
        self.assertTrue((self.workshop.root / 'Fixture.xlsx').exists())
        self.assertTrue((self.workshop.root / 'Fixture.pptx').exists())
