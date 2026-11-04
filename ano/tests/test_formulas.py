import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
from ano.formulas import calculate_formulas
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop
from ano.tools import ToolError


class FormulaTests(unittest.TestCase):
    def test_dependency_graph_range_aggregation_and_excel_rounding(self):
        headers = ['Item', 'Amount']
        rows = [['A', 12.5], ['B', 7.5], ['Total', None], ['Tax', None], ['Gross', None]]
        results = calculate_formulas(headers, rows, {'B4': '=SUM($B$2:B3)', 'B5': '=ROUND(B4*0.075,2)', 'B6': '=B4+B5'})
        self.assertEqual([results[(r, 2)]['value'] for r in (4, 5, 6)], [20, 1.5, 21.5])
        rounded = calculate_formulas(['Values'], [[None], [None]], {'A2': '=ROUND(1.005,2)', 'A3': '=ROUND(-1.25,1)'})
        self.assertEqual(rounded[(2, 1)]['value'], 1.01)
        self.assertEqual(rounded[(3, 1)]['value'], -1.3)

    def test_aggregate_functions_ignore_text_and_blank_range_entries(self):
        rows = [[2, None], ['text', None], [6, None], [None, None], [None, None]]
        expressions = {'B2': '=AVERAGE(A2:A5)', 'B3': '=COUNT(A2:A5)', 'B4': '=MIN(A2:A5)',
                       'B5': '=MAX(A2:A5)', 'B6': '=ABS(-9)+2^3'}
        result = calculate_formulas(['Values', 'Results'], rows, expressions)
        self.assertEqual([result[(r, 2)]['value'] for r in range(2, 7)], [4, 2, 2, 6, 17])

    def test_cycles_external_references_and_unsupported_functions_are_refused(self):
        for formulas in ({'B2': '=B3', 'B3': '=B2'}, {'B2': '=SUM(Sheet2!B2:B3)'},
                         {'B2': '=HYPERLINK("https://example.com","x")'}, {'B2': '=1/0'},
                         {'B2': '=__import__(1)'}, {'B2': '=2^999'}, {'B2': '=SUM(B1:B3)'},
                         {'B2': '=SUM()'}, {'B2': '=B999'}, {'B2': '=B2.__class__'}):
            with self.subTest(formulas=formulas), self.assertRaises(ToolError):
                calculate_formulas(['Item', 'Amount'], [['A', None], ['B', None]], formulas)

    def test_python_only_and_ambiguous_power_syntax_cannot_create_excel_errors(self):
        for expression in ('=0x10', '=1_000', '=SUM(RANGE(A2,A3))', '=2**3', '=-2^2', '=2^3^2'):
            with self.subTest(expression=expression), self.assertRaises(ToolError):
                calculate_formulas(['Input', 'Result'], [[1, None], [2, None]], {'B2': expression})
        result = calculate_formulas(['Result'], [[None], [None]], {'A2': '=(-2)^2', 'A3': '=(2^3)^2'})
        self.assertEqual([result[(r, 1)]['value'] for r in (2, 3)], [4, 64])

    def test_generated_workbook_has_formula_text_and_numeric_cached_results(self):
        with TemporaryDirectory() as folder:
            plugin = Artifacts(Workshop(Path(folder), None))
            response, _ = plugin.create_spreadsheet('Calculated', ['Item', 'Amount'],
                [['A', 12.5], ['B', 7.5], ['Total', None], ['Average', None]], chart_column=2,
                formulas={'B4': '=SUM(B2:B3)', 'B5': '=AVERAGE(B2:B3)'})
            receipt = json.loads(response)
            self.assertTrue(receipt['formula_cache_verified'])
            self.assertEqual(receipt['formula_count'], 2)
            formulas = load_workbook(receipt['path'], data_only=False).active
            values = load_workbook(receipt['path'], data_only=True).active
            self.assertEqual(formulas['B4'].value, '=SUM(B2:B3)')
            self.assertEqual((values['B4'].value, values['B5'].value), (20, 10))
            self.assertEqual(len(formulas._charts), 1)

    def test_invalid_formula_never_writes_output_and_input_rows_are_unchanged(self):
        with TemporaryDirectory() as folder:
            rows = [['A', 4], ['B', None]]
            plugin = Artifacts(Workshop(Path(folder), None))
            with self.assertRaises(ToolError):
                plugin.create_spreadsheet('Invalid', ['Item', 'Amount'], rows, formulas={'B3': '=B3+1'})
            self.assertFalse((Path(folder) / 'Invalid.xlsx').exists())
            self.assertEqual(rows, [['A', 4], ['B', None]])
