"""Create synthetic artifact fixtures and render the PDF for human visual review."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop


def main():
    root = Path(__file__).resolve().parents[1] / '.runtime' / ('artifact-fixtures-' + time.strftime('%Y%m%d-%H%M%S'))
    plugin = Artifacts(Workshop(root, None))
    results = []
    for method, arguments in [
        (plugin.create_spreadsheet, {'name': 'Supplied sales', 'headers': ['Month', 'Units'],
                                    'rows': [['January', 20], ['February', 35], ['March', 28]], 'chart_column': 2}),
        (plugin.create_pdf, {'name': 'Supplied summary', 'title': 'Supplied sales summary',
                            'text': 'Synthetic validation fixture — no private records.\n\nJanuary: 20 units\nFebruary: 35 units\nMarch: 28 units\n\nTotal supplied units: 83.\nThese are supplied fixture values, not external research.'}),
    ]:
        result, _ = method(**arguments)
        results.append(json.loads(result))
    from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    app = QGuiApplication.instance() or QGuiApplication([])
    pdf = QPdfDocument()
    pdf.load(results[1]['path'])
    preview = root / 'PDF-page-1.png'
    rendered = pdf.render(0, QSize(918, 1188))
    composite = QImage(rendered.size(), QImage.Format_RGB32)
    composite.fill(QColor('white'))
    painter = QPainter(composite)
    painter.drawImage(0, 0, rendered)
    painter.end()
    if not composite.save(str(preview)):
        raise RuntimeError('PDF render failed.')
    pdf.close()
    report = {'scope': 'Direct plugin fixtures; not a native Ano-command success claim.',
              'outputs': results, 'pdf_preview': str(preview), 'visual_review': 'Pending'}
    (root / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
