"""Package this bounded update without private runtime records or model weights."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    root = Path(__file__).resolve().parents[1]
    release = root / 'releases'
    wheel = release / 'ano_desktop-0.1.4-py3-none-any.whl'
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        if any('/.runtime/' in n or n.endswith(('.sqlite3', '.db', '.env')) for n in names):
            raise ValueError('Unexpected private/runtime file in wheel.')
        required = ['ano/jobs.py', 'ano/model_roles.py', 'ano/plugins/artifacts.py', 'ano/qml/Main.qml']
        if any(n not in names for n in required):
            raise ValueError('A required source file is missing from the wheel.')
    audit = {'version': '0.1.4', 'built_utc': datetime.now(timezone.utc).isoformat(),
        'overall_goal_complete': False,
        'tests': {'passed': 428, 'log': '.runtime/capability-foundation-tests.log',
                  'scope': 'Software regression tests, not desktop task or model quality equivalence.'},
        'qml_smoke': 'Passed before installation; actual running process still needs clean restart and live validation.',
        'artifacts': {'pdf': 'Synthetic supplied-data output: bytes/text verified and corrected page visually reviewed.',
                      'xlsx': 'Synthetic supplied-data table/chart: values/package checked; native Excel visual check pending.',
                      'pptx': 'Unfinished', 'formulas': 'Unfinished; input strings remain literal.'},
        'models': {'default_changed': False, 'training_performed': False,
                   'reasoning_gate': 'Qwen3.5 4B, Qwen3 4B default and Qwen3 4B Instruct did not pass the complete gate.'},
        'distribution': 'Python wheel; requires Python and dependencies. Not a standalone Windows installer.',
        'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}
    readme = '''Ano 0.1.4 capability foundation update

This is a Python package, not a standalone Windows installer or a claim of ChatGPT parity.
In the existing Ano workspace, the update has been installed into .venv.
Close Ano using Menu > Quit only after active tasks finish, then use Launch Ano.cmd.
Do not force-stop an active task just to load an update.

For a separate Python 3.11+ environment:
  python -m pip install ano_desktop-0.1.4-py3-none-any.whl
  python -m ano
Dependencies require a package download. Local model/voice/image files are separate.
No API key is needed for exact local commands. No model defaults were changed.

Includes persistent command queue, stop/cancel controls, original-chat replies,
persisted task errors, explicit local model roles, and supplied-data XLSX/PDF output.
Existing documents are not overwritten. Interrupted work pauses and does not replay.
Tests do not prove real app completion. Reasoning and the other capability gaps
remain unfinished; see audit.json for the measured scope.
'''
    target = release / 'Ano-0.1.4.zip'
    if target.exists():
        raise FileExistsError('Release already exists; inspect it before repackaging.')
    with ZipFile(target, 'x', ZIP_DEFLATED) as archive:
        archive.write(wheel, wheel.name)
        archive.writestr('READ-ME.txt', readme)
        archive.writestr('audit.json', json.dumps(audit, indent=2))
        archive.writestr('release.json', json.dumps({'version': '0.1.4', 'standalone': False}, indent=2))
    (release / 'Ano-0.1.4-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    progress = root / '.runtime/full-capability-implementation.json'
    report = json.loads(progress.read_text(encoding='utf-8-sig'))
    report['at_utc'] = audit['built_utc']
    report['current_work'] = '0.1.4 installed into the existing environment. Queue/UI integration and XLSX/PDF tools pass regression tests. Running old app has not been force-restarted; native update validation is pending. All nine areas remain unfinished.'
    report['verification'].update({'full_tests': 428, 'task_queue_tests': 4, 'artifact_tests': 5,
        'runtime_deployment': 'Editable package installed; old running process preserved pending clean restart.'})
    report['remaining_blockers'] = ['Reasoning gate failed; no candidate promoted',
        'Live queued-command/cancellation verification pending', 'Real playlist completion unverified',
        'XLSX visual/revision/formula workflows and PPTX creation unfinished',
        'Live conversational voice, safe resumable tool checkpoints, durable memory retrieval and image quality unfinished']
    report['evidence'] = ['.runtime/capability-foundation-tests.log', '.runtime/qwen3-4b-instruct-reasoning-gate.json',
        '.runtime/artifact-fixtures-20261027-082709/verification.json']
    progress.write_text(json.dumps(report, indent=2), encoding='utf-8')
    fixture = root / '.runtime/artifact-fixtures-20261027-082709/verification.json'
    verification = json.loads(fixture.read_text())
    verification['visual_review'] = 'PDF page 1 inspected: readable title/body/footer, margins intact. XLSX native display pending.'
    fixture.write_text(json.dumps(verification, indent=2), encoding='utf-8')
    snapshot = root / '.runtime' / ('source-capability-foundation-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip')
    with ZipFile(snapshot, 'x', ZIP_DEFLATED) as archive:
        for path in (root / 'ano').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.write(path, str(path.relative_to(root)))
        for name in ('pyproject.toml', 'README.md', 'LOCAL_COMMANDS.md'):
            archive.write(root / name, name)
    print(json.dumps({'release': str(target), 'source_snapshot': str(snapshot), 'overall_goal_complete': False}, indent=2))


if __name__ == '__main__':
    main()
