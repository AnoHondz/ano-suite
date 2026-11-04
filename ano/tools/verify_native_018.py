"""Independently inspect the Word revision issued through native Ano UI."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from docx import Document


def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root.parent / 'AnoWorkspace'
    source = workspace / 'Ano-018-Revision-Source-20261027.docx'
    output = workspace / 'Ano-018-Revision-Result-20261027.docx'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    backup = workspace / '.ano-word-backups' / (digest + '.docx')
    original = root / '.runtime/word-revision-20261027-01/Source.docx'
    with sqlite3.connect((workspace / '.ano-state/memory.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT u.id,u.chat_id,u.created_at,a.created_at,a.body FROM messages u "
                         "JOIN messages a ON a.in_reply_to=u.id AND a.role='assistant' "
                         "WHERE u.body LIKE 'revise word document Ano-018-Revision-Source-20261027:%' "
                         "ORDER BY u.id DESC,a.id DESC LIMIT 1").fetchone()
    assert row, 'No linked native command/reply found'
    parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    with ZipFile(source) as before, ZipFile(output) as after:
        parts_preserved = set(before.namelist()) == set(after.namelist()) and all(
            before.read(name) == after.read(name) for name in before.namelist() if name != 'word/document.xml')
    doc = Document(output)
    case = {'state': 'answered', 'request_id': row[0], 'chat_id': row[1],
            'turn_seconds': round((parse(row[3]) - parse(row[2])).total_seconds(), 3),
            'no_llm_receipt': 'No LLM used' in row[4],
            'source_unchanged': source.read_bytes() == original.read_bytes(),
            'backup_verified': backup.read_bytes() == source.read_bytes(),
            'unrelated_parts_preserved': parts_preserved,
            'body_correct': any(p.text == 'Hello Mira, exact body remains.' for p in doc.paragraphs),
            'table_unchanged': doc.tables[0].cell(1, 1).text == 'Pending',
            'path': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
    case['outcome_verified'] = all(case[key] for key in ('no_llm_receipt', 'source_unchanged',
        'backup_verified', 'unrelated_parts_preserved', 'body_correct', 'table_unchanged'))
    report = {'version_seen_in_native_menu': '0.1.8',
              'scope': 'One exact revision command issued through native Ano UI; independent file and linked-reply inspection. General editing and natural-language revision remain unfinished.',
              'word_revision': case, 'overall_goal_complete': False}
    (root / '.runtime/native-0.1.8-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    assert case['outcome_verified']


if __name__ == '__main__':
    main()
