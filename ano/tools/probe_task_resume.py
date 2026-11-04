"""Exercise persisted recovery through Controller and real local file tools.

Isolated synthetic workspace; no native Ano state, model or UI interaction.
"""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from PySide6.QtCore import QCoreApplication
    from ano.app import Controller
    from ano.memory import Memory
    from ano.jobs import JobStore
    from ano.plugins.workshop import Workshop
    from ano.routines import RoutineStore, LocalRoutines, RoutineBrain
    from ano.tools import Toolbox
    runtime = Path(__file__).resolve().parents[1] / '.runtime'
    root = Path(tempfile.mkdtemp(prefix='resume-fixture-', dir=runtime))
    memory = Memory(root / 'memory.sqlite3')
    jobs = JobStore(root / 'jobs.sqlite3')
    chat = memory.latest_chat()
    command = 'write document Resume fixture: Exact synthetic resume content.'
    request = memory.add('user', command, chat)
    identifier = jobs.enqueue(jobs.project_for_chat(chat), command, chat, request)
    # Reopen durable state before any dispatch, as after an interrupted queued task.
    reopened = JobStore(root / 'jobs.sqlite3')
    assert reopened.recover_interrupted(include_queued=True) == 1
    assert reopened.claim_next() is None
    class NoModel:
        def reply(self, *args, **kwargs):
            raise AssertionError('This exact task must never call a model')
    toolbox = Toolbox(lambda: [root], plugins=[Workshop(root, None)])
    provider = RoutineBrain(LocalRoutines(toolbox, RoutineStore(root / 'routines.sqlite3')), NoModel())
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = Controller(memory, provider, reopened)
    controller.resumeTask(identifier)
    deadline = time.monotonic() + 10
    while controller.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not controller.busy, 'Controller did not complete'
    controller.pool.waitForDone()
    file = root / 'Resume fixture.md'
    assert file.read_text(encoding='utf-8') == 'Exact synthetic resume content.'
    assert reopened.get(identifier)['state'] == 'answered'
    reply = memory.recent(chat=chat)[-1]
    assert reply['role'] == 'assistant' and 'No LLM used' in reply['body']
    count = toolbox.executions
    controller.resumeTask(identifier)
    assert toolbox.executions == count == 1, 'A completed task was replayed'
    memory.close()
    report = {'scope': 'Real Controller resume after durable-store reopen and interrupted queue recovery; exact local file creation checked. No native UI, model, production state or general changed-destination resume proof.',
              'passed': True, 'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
              'tool_executions': count, 'original_request_id': request, 'original_chat_id': chat,
              'overall_goal_complete': False}
    (runtime / 'task-resume-file-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
