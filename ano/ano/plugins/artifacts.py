"""Bounded local artifact creation from supplied data, without model inference.

Files are created exclusively. Supplied spreadsheet strings remain literal and
cannot turn into formulas or links. Existing files are never overwritten.
"""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import math
import re
from xml.sax.saxutils import escape

from ..tools import ToolError


def _number(value):
    if isinstance(value, str) and re.fullmatch(r'\s*-?\d{1,15}(?:\.\d{1,10})?\s*', value):
        return float(value) if '.' in value else int(value)
    return value


def _shorten(text, limit):
    text = ' '.join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(' ', 1)[0]
    return (cut or text[:limit - 1]) + '…'


def tidy_slides(slides):
    """Fit near-miss slides to the template's limits instead of failing: (slides, notes on what changed).

    Points given as one block of text are split into lines, empty points dropped, more than four kept to the first
    four, and over-long titles and points shortened at a word. A slide with no points at all is still refused.
    """
    if not isinstance(slides, list):
        return slides, []
    tidy, notes = [], []
    for number, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            return slides, []
        bullets = slide.get('bullets')
        if isinstance(bullets, str):
            bullets = [line.strip(' -•*\t') for line in bullets.splitlines()]
        bullets = [_shorten(point, 120) for point in (bullets or []) if str(point).strip()]
        if len(bullets) > 4:
            notes.append(f'slide {number}: kept the first 4 of {len(bullets)} points')
            bullets = bullets[:4]
        if not bullets:
            raise ToolError(f'Slide {number} (“{slide.get("title", "")}”) has no points. Give each slide 1–4 short points. '
                            'Nothing was created.')
        title = _shorten(slide.get('title', ''), 65)
        if title != ' '.join(str(slide.get('title', '')).split()):
            notes.append(f'slide {number}: shortened the title')
        tidy.append({'title': title, 'bullets': bullets})
    return tidy, notes


class Artifacts:
    def __init__(self, workshop):
        self.workshop = workshop

    def definitions(self):
        return [
            {'name': 'create_presentation', 'description': 'Create an editable PPTX from supplied slide titles and 1–4 short points per slide (each under 120 characters), using the bundled template. A title slide is always added first, so N content slides make N+1 slides in total; when the user asks for an exact total, supply one fewer content slide and set total_slides to the total they asked for (it is checked). Text read-back; never overwrite.',
             'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'title': {'type': 'string'},
                'subtitle': {'type': 'string'},
                'total_slides': {'type': 'integer', 'description': 'The total the user asked for, title slide included. Set it whenever the user names a number of slides.'},
                'slides': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'title': {'type': 'string'}, 'bullets': {'type': 'array', 'items': {'type': 'string'}}},
                    'required': ['title', 'bullets'], 'additionalProperties': False}}},
                'required': ['name', 'title', 'slides'], 'additionalProperties': False}},
            {'name': 'create_spreadsheet', 'description': 'Create a styled XLSX table, optional column chart and explicit numeric formulas. Supports arithmetic, SUM/AVERAGE/MIN/MAX/COUNT/ABS/ROUND with cached results. Ordinary strings stay literal; never overwrite.',
             'input_schema': {'type': 'object', 'properties': {
                 'name': {'type': 'string'}, 'headers': {'type': 'array', 'items': {'type': 'string'}},
                 'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': ['string', 'number', 'boolean', 'null']}}},
                 'formulas': {'type': 'object', 'additionalProperties': {'type': 'string'},
                              'description': 'Explicit formula cells, e.g. {"B5":"=SUM(B2:B4)"}. References must stay in supplied data rows.'},
                 'chart_column': {'type': 'integer', 'description': 'One-based numeric data column; first column holds categories.'}},
                 'required': ['name', 'headers', 'rows'], 'additionalProperties': False}},
            {'name': 'create_pdf', 'description': 'Create a paginated PDF from exact supplied title and body text; embedded local font and text read-back. Never overwrite.',
             'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'title': {'type': 'string'},
                'text': {'type': 'string'}}, 'required': ['name', 'title', 'text'], 'additionalProperties': False}},
        ]

    def run(self, name, arguments):
        self.workshop._check()
        if name not in ('create_spreadsheet', 'create_pdf', 'create_presentation'):
            raise ToolError('Unknown artifact tool.')
        return getattr(self, name)(**arguments)

    def create_presentation(self, name, title, slides, subtitle='Supplied content', total_slides=None):
        from ..decks import build_deck
        slides, adjusted = tidy_slides(slides)
        dropped = []
        if total_slides is not None and len(slides) + 1 != total_slides:
            if total_slides < 2:
                raise ToolError('A deck here is a title slide plus at least one content slide, so total_slides must be '
                                'at least 2. Nothing was created.')
            if len(slides) + 1 < total_slides:
                raise ToolError(f'{len(slides)} content slides plus the title slide make {len(slides) + 1}, not the '
                                f'{total_slides} asked for. Supply exactly {total_slides - 1} content slide(s). Nothing was created.')
            # Too many: keep the first ones so the total is exactly what the user asked for, and say what went.
            slides, dropped = slides[:total_slides - 1], slides[total_slides - 1:]
        path = self.workshop._path(name, ('.pptx',), '.pptx')
        if path.exists():
            raise ToolError('That presentation already exists. Nothing was overwritten.')
        payload = build_deck(title, slides, subtitle)
        details = {'slides': len(slides) + 1, 'text_readback_verified': True,
                   'editable': True, 'visual_layout': 'Not independently inspected', 'content': 'Supplied text; not AI research.'}
        if adjusted:
            details['adjusted'] = adjusted
        if dropped:
            details['trimmed_to_requested_total'] =('The title slide counts toward the total, so these supplied slides were '
                                                     'left out: ' + '; '.join(str(s.get('title', '')) for s in dropped))
        return self._save(path, payload, details)

    def _save(self, path, payload, details):
        self.workshop._check()
        try:
            with path.open('xb') as stream:
                stream.write(payload)
        except FileExistsError:
            raise ToolError('That artifact already exists. Nothing was overwritten.') from None
        if path.read_bytes() != payload:
            raise ToolError(f'File saved but byte verification failed: {path}. Inspect before retrying.')
        return json.dumps({'path': str(path), 'bytes_verified': True,
            'sha256': hashlib.sha256(payload).hexdigest(), **details}, ensure_ascii=False), 'Created local artifact'

    def create_spreadsheet(self, name, headers, rows, chart_column=None, formulas=None):
        from openpyxl import Workbook, load_workbook
        from openpyxl.chart import BarChart, Reference
        from openpyxl.chart.label import DataLabelList
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.table import Table, TableStyleInfo
        path = self.workshop._path(name, ('.xlsx',), '.xlsx')
        if path.exists():
            raise ToolError('That spreadsheet already exists. Nothing was overwritten.')
        if not isinstance(headers, list) or not 1 <= len(headers) <= 30 or any(
                not isinstance(x, str) or not x.strip() or len(x) > 100 for x in headers):
            raise ToolError('Supply 1–30 non-empty headers, each under 100 characters.')
        if len(set(x.casefold() for x in headers)) != len(headers):
            raise ToolError('Spreadsheet headers must be unique.')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
            raise ToolError('Supply 1–2,000 data rows.')
        if type(chart_column) is int and 2 <= chart_column <= len(headers):
            # Small models often send chart numbers as text ("88"); plain decimal numerals are unambiguous.
            rows = [[_number(value) if index == chart_column - 1 else value for index, value in enumerate(row)]
                    if isinstance(row, list) else row for row in rows]
        for row in rows:
            if not isinstance(row, list) or len(row) != len(headers):
                raise ToolError('Each data row must have exactly one value per header.')
            for value in row:
                if type(value) not in (str, int, float, bool, type(None)):
                    raise ToolError('Cells must be text, finite numbers, booleans, or null.')
                if isinstance(value, str) and (len(value) > 2000 or any(ord(c) < 32 and c not in '\n\t\r' for c in value)):
                    raise ToolError('Cell text is too long or contains invalid control characters.')
                if isinstance(value, (float, int)) and not math.isfinite(value):
                    raise ToolError('Numbers must be finite.')
        from ..formulas import calculate_formulas, cache_values
        calculated = calculate_formulas(headers, rows, formulas if formulas is not None else {}, check=self.workshop._check)
        def effective_value(row, column):
            return calculated.get((row, column), {}).get('value', rows[row - 2][column - 1])
        if chart_column is not None and (type(chart_column) is not int or not 2 <= chart_column <= len(headers)
                or any(type(effective_value(row, chart_column)) not in (int, float) for row in range(2, len(rows) + 2))):
            raise ToolError('Chart column must be a numeric column after the category column.')
        book = Workbook()
        sheet = book.active
        sheet.title = 'Data'
        for values in [headers] + rows:
            sheet.append(values)
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Never interpret supplied =, +, @ text.
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                cell.font = Font(name='Calibri', size=11)
        for position, calculation in calculated.items():
            sheet.cell(*position, value=calculation['formula'])
        for cell in sheet[1]:
            cell.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='244B47')
        for column in range(1, len(headers) + 1):
            width = max(len(str(values[column - 1] or '')) for values in [headers] + rows)
            sheet.column_dimensions[get_column_letter(column)].width = min(42, max(14, width + 3))
        sheet.freeze_panes = 'A2'
        table = Table(displayName='SuppliedData', ref=f'A1:{get_column_letter(len(headers))}{len(rows) + 1}')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium4', showRowStripes=True)
        sheet.add_table(table)
        if chart_column:
            chart = BarChart()
            chart.style = 10
            chart.title = headers[chart_column - 1]
            chart.legend = None
            chart.x_axis.title = headers[0]
            chart.y_axis.title = headers[chart_column - 1]
            chart.title.overlay = chart.x_axis.title.overlay = chart.y_axis.title.overlay = False
            chart.x_axis.tickLblPos = chart.y_axis.tickLblPos = 'nextTo'
            chart.x_axis.delete = chart.y_axis.delete = False
            chart.dLbls = DataLabelList()
            chart.dLbls.showVal = True
            chart.dLbls.showCatName = chart.dLbls.showSerName = chart.dLbls.showLegendKey = False
            chart.dLbls.showPercent = False
            chart.dLbls.dLblPos = 'outEnd'
            chart.add_data(Reference(sheet, min_col=chart_column, min_row=1, max_row=len(rows) + 1), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(rows) + 1))
            sheet.add_chart(chart, f'{get_column_letter(len(headers) + 2)}2')
        stream = BytesIO()
        book.save(stream)
        payload = stream.getvalue()
        if calculated:
            payload = cache_values(payload, calculated)
        reopened = load_workbook(BytesIO(payload), data_only=False)
        actual = [list(row) for row in reopened.active.values]
        expected = [[None if x == '' else x for x in row] for row in [headers] + rows]
        for (row, col), calculation in calculated.items():
            expected[row - 1][col - 1] = calculation['formula']
        actual_formulas = {(cell.row, cell.column) for row in reopened.active for cell in row if cell.data_type == 'f'}
        cached = load_workbook(BytesIO(payload), data_only=True).active
        if (actual != expected or actual_formulas != set(calculated) or any(
                cached.cell(*position).value != result['value'] for position, result in calculated.items())):
            raise ToolError('Spreadsheet read-back failed. Nothing was saved.')
        return self._save(path, payload, {'rows': len(rows), 'columns': len(headers), 'chart_count': int(bool(chart_column)),
            'cell_readback_verified': True, 'formula_count': len(calculated), 'formula_cache_verified': True,
            'formulas': 'Only explicit supported formulas are calculated. Other strings remain literal. General Excel functions and imported workbook recalculation are unfinished.',
            'visual_layout': 'Not independently inspected'})

    def create_pdf(self, name, title, text):
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.enums import TA_LEFT
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from pypdf import PdfReader
        path = self.workshop._path(name, ('.pdf',), '.pdf')
        if path.exists():
            raise ToolError('That PDF already exists. Nothing was overwritten.')
        self.workshop._body(text)
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ToolError('Supply a title under 200 characters.')
        font = Path('C:/Windows/Fonts/arial.ttf')
        if not font.exists():
            font = Path(__file__).resolve().parents[1] / 'assets' / 'fonts' / 'MPLUSRounded1c-Regular.ttf'
        if 'AnoPDF' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AnoPDF', str(font)))
        styles = getSampleStyleSheet()
        for style in (styles['Title'], styles['BodyText']):
            style.fontName = 'AnoPDF'
            style.alignment = TA_LEFT
        styles['Title'].fontSize = 24
        styles['Title'].leading = 30
        styles['Title'].textColor = colors.HexColor('#244B47')
        styles['BodyText'].fontSize = 10
        styles['BodyText'].leading = 15
        stream = BytesIO()
        document = SimpleDocTemplate(stream, pagesize=(612, 792), rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54,
                                     title=title, author='Ano')
        story = [Paragraph(escape(title), styles['Title']), Spacer(1, 16)]
        story += [Paragraph(escape(line) or '&#160;', styles['BodyText']) for line in text.splitlines()]
        def footer(canvas, doc):
            canvas.setFont('AnoPDF', 9)
            canvas.setFillColor(colors.HexColor('#666666'))
            canvas.drawRightString(558, 30, str(doc.page))
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        payload = stream.getvalue()
        reader = PdfReader(BytesIO(payload))
        extracted = '\n'.join(page.extract_text() or '' for page in reader.pages)
        normalized = lambda s: ''.join(s.split())
        if normalized(title) not in normalized(extracted) or any(normalized(line) not in normalized(extracted)
                for line in text.splitlines() if line.strip()):
            raise ToolError('PDF text read-back failed. Nothing was saved.')
        return self._save(path, payload, {'pages': len(reader.pages), 'text_readback_verified': True,
                                         'visual_layout': 'Not independently inspected'})
