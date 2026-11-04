"""Verify local reminder routing against real SQLite plugins, isolated from user data."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from ano.plugins.notes import Notebook
    from ano.tools import Toolbox
    from ano.routines import LocalRoutines, RoutineStore, RoutineBrain
    from ano.paid import PaidTasks, SpendLedger
    runtime = Path(__file__).resolve().parents[1] / '.runtime'
    root = Path(tempfile.mkdtemp(prefix='reminder-triage-fixture-', dir=runtime))
    civil = datetime(2030, 1, 15, 10)
    notebook = Notebook(root / 'reminders.sqlite3', clock=lambda:civil.astimezone(timezone.utc))
    toolbox = Toolbox(lambda:[root], plugins=[notebook])
    routines = LocalRoutines(toolbox, RoutineStore(root/'routines.sqlite3'), clock=lambda:civil)
    class NoModel:
        def reply(self, *args, **kwargs):
            raise AssertionError('No model may run for this fixture')
    def no_key():
        raise AssertionError('No paid credential may be loaded')
    ledger = SpendLedger(root/'spend.sqlite3')
    router = PaidTasks(RoutineBrain(routines, NoModel()), toolbox, ledger, mode='openai', key_loader=no_key)
    def ask(text):
        reply = router.reply([{'role':'user','body':text,'source_chat_id':42}])
        assert 'No LLM used' in reply
        return reply
    def count():
        with sqlite3.connect(notebook.path) as db:
            return db.execute('SELECT count(*) FROM reminders').fetchone()[0]
    body = 'Public synthetic fixture: Preserve MiXeD and opne calcualtor.\nopen calculator\nand\tstop monitoring'
    ask('remind me at 5pm to ' + body)
    assert count() == 1
    ask('remind me at 5 to Second ' + body)
    assert count() == 1
    assert 'choose am or pm' in ask('yes')
    assert count() == 1
    ask('pm')
    assert count() == 2
    assert 'tomorrow' in ask('remind me at 5am to Third ' + body)
    assert count() == 2
    ask('okay')
    with sqlite3.connect(notebook.path) as db:
        rows = db.execute('SELECT body,due_at FROM reminders ORDER BY id').fetchall()
    expected = [(body, datetime(2030,1,15,17)), ('Second '+body, datetime(2030,1,15,17)),
                ('Third '+body, datetime(2030,1,16,5))]
    assert len(rows) == 3
    for (text,due), (expected_text,expected_due) in zip(rows, expected):
        assert text == expected_text
        assert datetime.fromisoformat(due) == expected_due.astimezone(timezone.utc)
    with ledger.connect() as db:
        assert db.execute('SELECT count(*) FROM charges').fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM message_routes WHERE route='local_command' AND status='completed'").fetchone()[0] == 6
    report = {'scope':'Real LocalRoutines/PaidTasks and Notebook persistence in an isolated synthetic database. No native UI or notification delivery proof.',
              'passed':True,'reminder_rows':3,'local_turns':6,'paid_charge_rows':0,
              'payloads_and_utc_timestamps_verified':True,'ambiguous_yes_created_no_reminder':True,
              'tomorrow_required_confirmation':True,'fixture':str(root),'overall_goal_complete':False}
    (runtime/'local-reminder-triage-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
