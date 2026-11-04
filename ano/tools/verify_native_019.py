"""Read-only evidence for the native 0.1.9 image-command trial."""
from pathlib import Path
from datetime import datetime
import json
import sqlite3


def main():
    root = Path(__file__).resolve().parents[1]
    state = root.parent / 'AnoWorkspace/.ano-state'
    with sqlite3.connect((state / 'memory.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT u.id,u.chat_id,u.created_at,a.chat_id,a.created_at,a.body FROM messages u "
                         "LEFT JOIN messages a ON a.in_reply_to=u.id AND a.role='assistant' "
                         "WHERE u.body LIKE 'Read the local image file C:/Users/User/Documents/AnoWorkspace/Ano-public-shapes-20261027.png%' "
                         "ORDER BY u.id DESC,a.id DESC LIMIT 1").fetchone()
    assert row, 'Native request not found'
    with sqlite3.connect((state / 'jobs.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        job = db.execute('SELECT id,state FROM work_jobs WHERE request_message_id=?', (row[0],)).fetchone()
        steps = db.execute('SELECT tool,state FROM work_steps WHERE job_id=? ORDER BY created_at', (job[0],)).fetchall()
    text = row[5] or ''
    positions = [text.lower().find(term) for term in ('red', 'circle', 'blue', 'square', 'green', 'triangle')]
    parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    case = {'state': job[1], 'request_id': row[0], 'chat_id': row[1],
            'reply_linked_to_original_chat': row[3] == row[1],
            'actual_image_tool_returned': ('view_image_file', 'returned') in steps,
            'expected_terms_in_order': all(p >= 0 for p in positions) and positions == sorted(positions),
            'turn_seconds': round((parse(row[4]) - parse(row[2])).total_seconds(), 3) if row[4] else None,
            'response': text, 'steps': steps}
    case['outcome_verified'] = bool(case['actual_image_tool_returned'] and case['expected_terms_in_order']
                                    and case['reply_linked_to_original_chat'])
    report = {'version_seen_in_native_menu': '0.1.9',
              'scope': 'One native UI request with an explicit image-tool hint and valid public fixture path. Not general image-command reliability or visual accuracy.',
              'image_command': case, 'overall_goal_complete': False}
    if job[1] in ('running', 'queued'):
        print(json.dumps(report, indent=2))
        raise SystemExit('Task still active; no final verification file written.')
    out = root / '.runtime/native-0.1.9-verification.json'
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
