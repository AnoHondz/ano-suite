"""Text from Excel and PowerPoint files, and from pictures or scanned PDFs through Windows' built-in OCR.

Office files are read straight from their XML, so nothing extra needs installing. OCR uses the
Windows.Media.Ocr engine through Windows PowerShell; it runs on this PC and costs nothing.
"""
from datetime import datetime, timedelta
import os
from pathlib import Path
import posixpath
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile

from .tools import ToolError

MAX_PART_BYTES = 50 * 1024 * 1024  # per XML part, so a small zip cannot expand into gigabytes
MAX_ROWS = 5000
MAX_OCR_PAGES = 10
MAX_OCR_SIDE = 4000
IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tif', '.tiff', '.webp'}

SHEET = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
DRAWING = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
SLIDES = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
REL_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
PACKAGE_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'


def _xml(archive, name):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise ToolError('That file is too large inside to read safely.')
    return ElementTree.fromstring(archive.read(info))


def _relationships(archive, part):
    """Relationship id -> (type, package path) for one part, e.g. xl/workbook.xml."""
    folder, name = posixpath.split(part)
    root = _xml(archive, posixpath.join(folder, '_rels', name + '.rels'))
    links = {}
    for rel in (root.iter(PACKAGE_REL) if root is not None else ()):
        target = rel.get('Target', '')
        path = target[1:] if target.startswith('/') else posixpath.normpath(posixpath.join(folder, target))
        links[rel.get('Id')] = (rel.get('Type', ''), path)
    return links


def _open(file, kind):
    try:
        return zipfile.ZipFile(file)
    except (zipfile.BadZipFile, OSError):
        raise ToolError(f'That {kind} file could not be read. Older .xls/.ppt files must be saved as .xlsx/.pptx.') from None


# ---------------------------------------------------------------- Excel

BUILTIN_DATES = {14, 15, 16, 17, 22, 45, 46, 47}


def _column(reference):
    letters = re.match(r'[A-Z]+', reference or '')
    number = 0
    for letter in (letters[0] if letters else 'A'):
        number = number * 26 + ord(letter) - 64
    return number - 1


def _date_styles(archive):
    """Indexes of cell styles that display numbers as dates or times."""
    root = _xml(archive, 'xl/styles.xml')
    if root is None:
        return set()
    custom = set()
    for fmt in root.iter(SHEET + 'numFmt'):
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', '', fmt.get('formatCode', '')).lower()
        if re.search(r'[dy]|h+:m|m+:s', code):
            custom.add(int(fmt.get('numFmtId', -1)))
    xfs = root.find(SHEET + 'cellXfs')
    return {index for index, xf in enumerate(xfs if xfs is not None else ())
            if int(xf.get('numFmtId', 0)) in BUILTIN_DATES | custom}


def _excel_date(value):
    moment = datetime(1899, 12, 30) + timedelta(days=float(value))
    return f'{moment:%Y-%m-%d}' if moment.time() == datetime.min.time() else f'{moment:%Y-%m-%d %H:%M}'


def read_xlsx(file):
    with _open(file, 'Excel') as archive:
        try:
            workbook = _xml(archive, 'xl/workbook.xml')
            if workbook is None:
                raise ToolError('That Excel file has no workbook inside.')
            links = _relationships(archive, 'xl/workbook.xml')
            shared_root = _xml(archive, 'xl/sharedStrings.xml')
            shared = [''.join(t.text or '' for t in item.iter(SHEET + 't'))
                      for item in (shared_root.iter(SHEET + 'si') if shared_root is not None else ())]
            dates = _date_styles(archive)
            out = []
            for sheet in workbook.iter(SHEET + 'sheet'):
                _, path = links.get(sheet.get(REL_ID), ('', ''))
                root = _xml(archive, path)
                if root is None:
                    continue
                out.append(f'## Sheet: {sheet.get("name", "?")}')
                for count, row in enumerate(root.iter(SHEET + 'row')):
                    if count >= MAX_ROWS:
                        out.append(f'[Only the first {MAX_ROWS:,} rows are shown.]')
                        break
                    cells = {}
                    for cell in row.iter(SHEET + 'c'):
                        kind, value = cell.get('t'), cell.findtext(SHEET + 'v')
                        if kind == 'inlineStr':
                            value = ''.join(t.text or '' for t in cell.iter(SHEET + 't'))
                        elif kind == 's' and value is not None:
                            value = shared[int(value)] if int(value) < len(shared) else ''
                        elif kind == 'b':
                            value = 'TRUE' if value == '1' else 'FALSE'
                        elif kind in (None, 'n') and value is not None and int(cell.get('s', 0)) in dates:
                            try:
                                value = _excel_date(value)
                            except (ValueError, OverflowError):
                                pass
                        if value not in (None, ''):
                            cells[_column(cell.get('r'))] = ' '.join(str(value).split())
                    if cells:
                        values = [cells.get(i, '') for i in range(max(cells) + 1)]
                        out.append(f'Row {row.get("r", count + 1)}: ' + ' | '.join(values))
            return '\n'.join(out)
        except (ElementTree.ParseError, ValueError, KeyError, IndexError):
            raise ToolError('That Excel file could not be read.') from None


# ---------------------------------------------------------------- PowerPoint

def _paragraphs(root):
    return [text for text in (''.join(t.text or '' for t in p.iter(DRAWING + 't')).strip()
                              for p in root.iter(DRAWING + 'p')) if text]


def read_pptx(file):
    with _open(file, 'PowerPoint') as archive:
        try:
            presentation = _xml(archive, 'ppt/presentation.xml')
            if presentation is None:
                raise ToolError('That PowerPoint file has no presentation inside.')
            links = _relationships(archive, 'ppt/presentation.xml')
            out = []
            for number, slide in enumerate(presentation.iter(SLIDES + 'sldId'), 1):
                _, path = links.get(slide.get(REL_ID), ('', ''))
                root = _xml(archive, path)
                if root is None:
                    continue
                out.append(f'## Slide {number}')
                out.extend(_paragraphs(root))
                for kind, target in _relationships(archive, path).values():
                    if kind.endswith('/notesSlide'):
                        notes = _xml(archive, target)
                        # Skip the slide-number placeholder that notes pages repeat.
                        lines = [line for line in (_paragraphs(notes) if notes is not None else []) if line != str(number)]
                        if lines:
                            out.append('Speaker notes: ' + ' '.join(lines))
            return '\n'.join(out)
        except (ElementTree.ParseError, ValueError, KeyError):
            raise ToolError('That PowerPoint file could not be read.') from None


# ---------------------------------------------------------------- OCR (pictures and scanned PDFs)

OCR_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # module-loading progress would otherwise leak CLIXML into the text
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } |
    Select-Object -First 1
function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    $task.Result
}
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { 'ANO-NO-OCR'; exit }
foreach ($path in ($env:ANO_OCR -split '\|')) {
    'ANO-PAGE'
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        foreach ($line in $result.Lines) { $line.Text }
    } finally { $stream.Dispose() }
}
'''


def run_ocr(paths):
    """Recognised text for each PNG, using Windows OCR in the user's language."""
    from .plugins.system import powershell
    try:
        code, output = powershell(OCR_SCRIPT, 60 + 20 * len(paths), {'ANO_OCR': '|'.join(str(p) for p in paths)})
    except subprocess.TimeoutExpired:
        raise ToolError('Text recognition took too long. Try a smaller picture or fewer pages.') from None
    if 'ANO-NO-OCR' in output:
        raise ToolError('Windows text recognition is not installed for your language (Settings > Time & language > Language).')
    if code != 0 or 'ANO-PAGE' not in output:
        raise ToolError('Windows could not recognise text in that file.')
    return [page.strip() for page in output.split('ANO-PAGE')[1:]]


def _save_png(image, folder, name):
    from PySide6.QtCore import Qt
    if image.isNull():
        raise ToolError('That picture could not be opened.')
    if max(image.width(), image.height()) > MAX_OCR_SIDE:
        image = image.scaled(MAX_OCR_SIDE, MAX_OCR_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    path = Path(folder) / name
    if not image.save(str(path), 'PNG'):
        raise ToolError('That picture could not be prepared for text recognition.')
    return path


def read_image(file, ocr=None):
    from PySide6.QtGui import QImage
    with tempfile.TemporaryDirectory() as folder:
        # Re-encoding as PNG handles every format Qt reads and caps the size the OCR engine accepts.
        [text] = (ocr or run_ocr)([_save_png(QImage(str(file)), folder, 'page.png')])
    return text


def read_scanned_pdf(file, ocr=None, pages=MAX_OCR_PAGES):
    ocr = ocr or run_ocr
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    document = QPdfDocument()
    if document.load(str(file)) != QPdfDocument.Error.None_:
        raise ToolError('That PDF could not be opened. It may be password protected.')
    try:
        total = document.pageCount()
        count = min(total, pages)
        with tempfile.TemporaryDirectory() as folder:
            images = []
            for page in range(count):
                size = document.pagePointSize(page)  # points; render at about 200 dpi
                scale = min(200 / 72, MAX_OCR_SIDE / max(1.0, size.width(), size.height()))
                image = document.render(page, QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
                images.append(_save_png(image, folder, f'page-{page + 1}.png'))
            texts = ocr(images)
    finally:
        document.close()
    out = [f'## Page {number} (recognised text)\n{text}' for number, text in enumerate(texts, 1) if text]
    if total > count:
        out.append(f'[Text recognition covered the first {count} of {total} pages.]')
    return '\n\n'.join(out)
