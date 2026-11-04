"""Read back outputs created through the real Ano UI; never issues app commands."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.paths import data_directory


def main():
    from openpyxl import load_workbook
    from pypdf import PdfReader
    workspace = data_directory().parent
    with sqlite3.connect((data_directory() / 'memory.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        def turn(pattern):
            row = db.execute('SELECT u.id,u.chat_id,u.created_at,a.created_at,a.body FROM messages u '
                             'LEFT JOIN messages a ON a.in_reply_to=u.id AND a.role=\'assistant\' '
                             'WHERE u.role=\'user\' AND u.body LIKE ? ORDER BY u.id DESC,a.id DESC LIMIT 1', (pattern,)).fetchone()
            if not row:
                return {'state': 'not_found'}
            info = {'request_id': row[0], 'chat_id': row[1], 'state': 'answered' if row[3] else 'pending'}
            if row[3]:
                parse = lambda text: datetime.fromisoformat(text.replace('Z', '+00:00'))
                info['turn_seconds'] = round((parse(row[3]) - parse(row[2])).total_seconds(), 3)
                info['no_llm_receipt'] = 'No LLM used' in row[4]
                info['failure_reply'] = 'Task stopped without verified completion' in row[4] or 'Not completed:' in row[4]
                info['windows_adapter_returned'] = 'Intel(R) UHD Graphics 620' in row[4]
            return info
        gpu = turn('Which GPU do you have, and how much VRAM?')
        spreadsheet = turn('create spreadsheet Ano-017-Native-Formula-20261027:%')
        plain_pdf = turn('Make a PDF named "Ano-017-Native-Plain-English-20261027"%')
    path = workspace / 'Ano-017-Native-Formula-20261027.xlsx'
    if path.exists():
        formulas = load_workbook(path, data_only=False).active
        values = load_workbook(path, data_only=True).active
        spreadsheet.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_formula=formulas['B4'].value == '=SUM(B2:B3)', expected_value=values['B4'].value == 30,
            chart_count=len(formulas._charts))
        spreadsheet['outcome_verified'] = bool(spreadsheet['expected_formula'] and spreadsheet['expected_value']
            and spreadsheet['chart_count'] == 1 and spreadsheet.get('no_llm_receipt'))
    pdf_path = workspace / 'Ano-017-Native-Plain-English-20261027.pdf'
    if pdf_path.exists():
        reader = PdfReader(pdf_path)
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        plain_pdf.update(path=str(pdf_path), sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                         title_verified='Workflow check' in text, body_verified='This is exact supplied text.' in text)
        plain_pdf['file_outcome_verified'] = plain_pdf['title_verified'] and plain_pdf['body_verified']
    gpu['outcome_verified'] = bool(gpu.get('no_llm_receipt') and gpu.get('windows_adapter_returned'))
    report = {'version_seen_in_native_menu': '0.1.7',
              'scope': 'Live CUA-issued commands in native Ano, plus independent local file/database read-back. Only these cases are measured; no broad capability equivalence.',
              'gpu_question': gpu, 'explicit_spreadsheet_command': spreadsheet, 'plain_english_pdf': plain_pdf,
              'overall_goal_complete': False}
    out = Path(__file__).resolve().parents[1] / '.runtime/native-0.1.7-verification.json'
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
