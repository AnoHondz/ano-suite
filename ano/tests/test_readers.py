from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage

from ano import autostart
from ano.tools import Toolbox

RELS = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'
REL = '<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{}" Target="{}"/>'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
P = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
     'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def write_zip(path, parts):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, text in parts.items():
            archive.writestr(name, text)


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.toolbox = Toolbox(lambda: [self.folder])

    def tearDown(self):
        self.directory.cleanup()

    def read(self, name):
        return self.toolbox.run('read_file', {'path': name})

    def test_excel_sheets_rows_shared_strings_and_dates(self):
        write_zip(self.folder / 'Budget.xlsx', {
            'xl/workbook.xml': f'<workbook {S}><sheets><sheet name="March" sheetId="1" r:id="rId1"/>'
                               '<sheet name="Notes" sheetId="2" r:id="rId2"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': RELS.format(REL.format('rId1', 'worksheet', 'worksheets/sheet1.xml') +
                                                      REL.format('rId2', 'worksheet', '/xl/worksheets/sheet2.xml')),
            'xl/sharedStrings.xml': f'<sst {S}><si><t>Rent</t></si><si><r><t>Due </t></r><r><t>date</t></r></si></sst>',
            'xl/styles.xml': f'<styleSheet {S}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
            'xl/worksheets/sheet1.xml': f'<worksheet {S}><sheetData>'
                                        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
                                        '<row r="2"><c r="A2"><v>850.5</v></c><c r="B2" t="b"><v>1</v></c>'
                                        '<c r="C2" s="1"><v>46113</v></c></row></sheetData></worksheet>',
            'xl/worksheets/sheet2.xml': f'<worksheet {S}><sheetData><row r="4"><c r="B4" t="inlineStr"><is><t>Pay early</t></is></c>'
                                        '</row></sheetData></worksheet>'})
        text, _, failed = self.read('Budget.xlsx')
        self.assertFalse(failed, text)
        self.assertEqual(text, '## Sheet: March\nRow 1: Rent |  | Due date\nRow 2: 850.5 | TRUE | 2026-04-01\n'
                               '## Sheet: Notes\nRow 4:  | Pay early')

    def test_powerpoint_slides_in_order_with_speaker_notes(self):
        slide = '<p:sld {}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{}</a:t></a:r></a:p>' \
                '<a:p><a:r><a:t>{}</a:t></a:r><a:r><a:t>!</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
        write_zip(self.folder / 'Pitch.pptx', {
            'ppt/presentation.xml': f'<p:presentation {P}><p:sldIdLst><p:sldId id="256" r:id="rId9"/>'
                                    '<p:sldId id="257" r:id="rId3"/></p:sldIdLst></p:presentation>',
            'ppt/_rels/presentation.xml.rels': RELS.format(REL.format('rId9', 'slide', 'slides/slide2.xml') +
                                                           REL.format('rId3', 'slide', 'slides/slide1.xml')),
            'ppt/slides/slide2.xml': slide.format(P, 'Welcome', 'Hello'),
            'ppt/slides/slide1.xml': slide.format(P, 'Plan', 'Ship it'),
            'ppt/slides/_rels/slide2.xml.rels': RELS.format(REL.format('rId1', 'notesSlide', '../notesSlides/notesSlide1.xml')),
            'ppt/notesSlides/notesSlide1.xml': f'<p:notes {P}><a:p><a:r><a:t>Smile first</a:t></a:r></a:p>'
                                               '<a:p><a:r><a:t>1</a:t></a:r></a:p></p:notes>'})
        text, _, failed = self.read('Pitch.pptx')
        self.assertFalse(failed, text)
        self.assertEqual(text, '## Slide 1\nWelcome\nHello!\nSpeaker notes: Smile first\n## Slide 2\nPlan\nShip it!')

    def test_old_and_damaged_office_files_fail_clearly(self):
        (self.folder / 'old.xlsx').write_bytes(b'\xd0\xcf\x11\xe0 not a zip')
        text, _, failed = self.read('old.xlsx')
        self.assertTrue(failed)
        self.assertIn('.xlsx', text)
        self.assertTrue(self.read_missing_type())

    def read_missing_type(self):
        (self.folder / 'deck.ppt').write_bytes(b'x')
        return self.read('deck.ppt')[2]

    def test_pictures_and_scanned_pdfs_use_ocr_and_are_labelled(self):
        image = QImage(5000, 100, QImage.Format.Format_RGB32)
        image.fill(QColor('white'))
        image.save(str(self.folder / 'receipt.jpg'), 'JPG')
        seen = []

        def ocr(paths):
            seen.extend(QImage(str(path)).width() for path in paths)
            return ['Total 12.50']
        with patch('ano.readers.run_ocr', side_effect=ocr):
            text, note, failed = self.read('receipt.jpg')
        self.assertFalse(failed, text)
        self.assertEqual(text, '[Text recognised by Windows OCR; it may contain mistakes.]\nTotal 12.50')
        self.assertEqual(seen, [4000])  # scaled down to what the OCR engine accepts
        with patch('ano.tools.read_pdf', return_value='  '), \
                patch('ano.readers.read_scanned_pdf', return_value='## Page 1 (recognised text)\nSigned') as scanned:
            (self.folder / 'scan.pdf').write_bytes(b'%PDF-1.4')
            text, _, failed = self.read('scan.pdf')
        scanned.assert_called_once()
        self.assertIn('Signed', text)

    def test_missing_ocr_language_is_explained(self):
        from ano.readers import run_ocr
        from ano.tools import ToolError
        with patch('ano.plugins.system.powershell', return_value=(0, 'ANO-NO-OCR')):
            with self.assertRaisesRegex(ToolError, 'not installed for your language'):
                run_ocr(['x.png'])


class AutostartTests(unittest.TestCase):
    def test_command_uses_windowless_python_and_tray_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            python = Path(folder) / 'python.exe'
            python.write_bytes(b'')
            self.assertEqual(autostart.command(python), f'"{python}" -m ano.supervisor --sign-in')
            (Path(folder) / 'pythonw.exe').write_bytes(b'')
            self.assertEqual(autostart.command(python), f'"{Path(folder) / "pythonw.exe"}" -m ano.supervisor --sign-in')

    def test_enable_disable_touch_only_the_current_users_run_value(self):
        values = {}
        fake = MagicMock()
        fake.HKEY_CURRENT_USER = 'HKCU'
        fake.OpenKey.return_value.__enter__.return_value = 'key'
        fake.SetValueEx.side_effect = lambda key, name, _, kind, value: values.__setitem__(name, value)
        fake.QueryValueEx.side_effect = lambda key, name: (values[name], 1) if name in values else (_ for _ in ()).throw(OSError())
        fake.DeleteValue.side_effect = lambda key, name: values.pop(name)
        with patch.dict(sys.modules, {'winreg': fake}):
            self.assertFalse(autostart.enabled())
            autostart.enable()
            self.assertEqual(values, {'Ano': autostart.command()})
            self.assertTrue(autostart.enabled())
            autostart.disable()
            self.assertEqual(values, {})
        self.assertEqual(fake.OpenKey.call_args.args[:2], ('HKCU', autostart.RUN_KEY))


if __name__ == '__main__':
    unittest.main()
