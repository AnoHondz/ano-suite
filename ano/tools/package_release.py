"""Package the current bounded update; never include local user state or weights."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    runtime, releases = root / '.runtime', root / 'releases'
    test_log = runtime / f'capabilities-{version}-tests.log'
    ui_path = runtime / f'capabilities-{version}-ui.log'
    log = test_log.read_text(encoding='utf-8-sig')
    match = re.search(r'Ran (\d+) tests in ([\d.]+)s\s+OK', log)
    if not match:
        raise ValueError('Passing regression evidence is missing.')
    ui_log = ui_path.read_text(encoding='utf-8-sig')
    if 'QML chat, screen, activity' not in ui_log:
        raise ValueError('QML smoke verification is missing.')
    wheel = releases / f'ano_desktop-{version}-py3-none-any.whl'
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        required = ['ano/facts.py', 'ano/decks.py', 'ano/work_ledger.py', 'ano/voice_conversation.py',
                    'ano/assets/supplied-deck.pptx', 'ano/qml/Main.qml', 'ano/formulas.py', 'ano/local_limits.py', 'ano/word_revision.py',
                    'ano/triage.py', 'ano/streaming.py']
        if any(name not in names for name in required):
            raise ValueError('Required source or template missing from wheel.')
        if any('.runtime' in name or name.endswith(('.sqlite3', '.db', '.env')) for name in names):
            raise ValueError('Unexpected local-state file in package.')
        if f"APP_VERSION = '{version}'".encode() not in archive.read('ano/app.py'):
            raise ValueError('Running-version label does not match the release.')
        if b'def view_image_file(' not in archive.read('ano/tools.py'):
            raise ValueError('Image-view capability missing from wheel.')
        source_drift = [str(path.relative_to(root)).replace('\\', '/') for path in (root / 'ano').rglob('*')
                        if path.is_file() and path.suffix in ('.py', '.qml') and '__pycache__' not in path.parts
                        and (str(path.relative_to(root)).replace('\\', '/') not in names or
                             archive.read(str(path.relative_to(root)).replace('\\', '/')) != path.read_bytes())]
    fixture = runtime / 'PowerPoint-fixture-20261027.pptx'
    native_path = runtime / f'native-{version}-verification.json'
    native = json.loads(native_path.read_text(encoding='utf-8')) if native_path.exists() else {}
    native_cases = {key: {'passed': bool(value.get('outcome_verified', value.get('file_outcome_verified', False))),
                           'seconds': value.get('turn_seconds')}
                    for key, value in native.items() if isinstance(value, dict) and 'state' in value}
    recall_path = runtime / 'fact-recall-local-live.json'
    recall = json.loads(recall_path.read_text(encoding='utf-8')) if recall_path.exists() else {}
    audit = {'version': version, 'utc': datetime.now(timezone.utc).isoformat(), 'overall_goal_complete': False,
        'source_scope': {'distribution': 'The built wheel is the distributed application. Source snapshots preserve the current workspace separately.',
                         'current_source_differs_from_wheel': source_drift,
                         'newer_workspace_changes_included_in_wheel': not bool(source_drift)},
        'software_validation': {'regression_tests': int(match[1]), 'seconds': float(match[2]),
            'log': str(test_log), 'qml_smoke': 'Passed'},
        'verified_outcome_scope': {
            'no_model_first': '59 phrasing variants tested without a model. A real native typo request opened Calculator in 2.916 seconds with OpenAI mode still selected and no paid charge change. An uncertain native match asked locally and dispatched zero tools. Later audit adds known-verb limits, literal-domain routing and prefix-safe confirmations; broad language coverage remains unfinished.',
            'streaming_backend': 'Public synthetic live GPT-4.1 stream passed in 3.5 seconds, first text 3.063 seconds, one call, usage-based estimate $0.000326. Partial throttling passed. Prior rejected minimal request left a conservative $0.005042 reservation. Native streaming display and frontend integration remain unverified. Backend contract is included.',
            'image_understanding': 'One public red-circle/blue-square/green-triangle fixture passed through the actual image tool to local vision inference; all shapes correct in order, 177.906 seconds. Native version-specific command cases below are separate. General visual accuracy, image generation quality and interactive speed remain unfinished.',
            'word_format_routing': 'A previously failing native natural-language Word creation request succeeded with exact supplied text after excluding Markdown writers and guarding dispatch. 36.968 seconds, actual .docx opened in Word. See .runtime/word-format-native-verification.json; this is one case, not general reliability.',
            'word_revision': 'Exact inline body/table replacement preserves the original and saves a verified backup. Synthetic output inspected in native Word and passed Office XML validation; version-specific native dispatch evidence is listed separately below. General Word editing is unfinished.',
            'pptx': 'Three-slide synthetic fixture opened in native PowerPoint. Cover, list and card slides each inspected; editable elements and exact texts present. Direct generator fixture, not an Ano UI command.',
            'pptx_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
            'memory': ('One synthetic fact recalled correctly after database reopen in a fresh local-model conversation; '
                       f'model={recall.get("model")}, seconds={recall.get("seconds")}. Native UI recall and general memory remain unfinished.'
                       if recall.get('passed') else 'Software tests cover facts, revisions, provenance and forgetting; live recall pending.'),
            'voice': 'Simulated capture/playback tests verify utterance endpoint, mic lifecycle, playback gating, stale transcript rejection and Pause. Real microphone/speaker quality and echo cancellation unverified.',
            'xlsx': 'Synthetic formula workbook created through the actual command parser/tool path. Cached totals 60 and average 20 displayed in native Excel; worksheet recalculation agreed, and final chart labels/axes were visually inspected. General Excel compatibility and imported editing unfinished.',
            'local_waits': 'Adaptive bounded allowance tested with a simulated 125-second cold wait and existing cancellation tests. Actual CPU generation is not faster, and broad task completion remains unverified.',
            'jobs': 'Tool dispatch is checkpointed; returned receipts are not task verification. Generic safe resume remains unfinished.'},
        'deployment': ('Native version menu confirmed after a normal, idle Quit/relaunch; only the cases below have live outcome evidence.'
                       if native.get('version_seen_in_native_menu') == version else
                       'Source and Python wheel prepared; native version/command validation pending.'),
        'native_command_cases': native_cases,
        'distribution': 'Python wheel requiring dependencies and separately installed local models; not a standalone installer.',
        'not_completed': ['ChatGPT-level reasoning', 'General autonomous app tasks and playlist outcome',
            'General Office artifact editing and full Excel formula compatibility', 'Simultaneous conversational audio',
            'General resumable parallel projects', 'Semantic memory and verified integrations', 'Image quality equivalence'],
        'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}
    readme = f'''Ano {version} — local capability update

This Python package is not a standalone installer and is not ChatGPT parity.
Install in the existing Ano environment, then cleanly Quit/relaunch when idle.
Never force-stop active work to load this update. Voice is off until you start it.

python -m pip install ano_desktop-{version}-py3-none-any.whl
python -m ano

Includes editable supplied-content PPTX, explicit saved facts with revisions and
provenance, local hands-free voice turns, and persistent tool-dispatch receipts.
New: bounded cold-model waits with elapsed status, supplied numeric Excel formulas
with checked cached values, clearer chart labels, and direct GPU inspection.
Exact inline Word revisions save separate files and verified source backups.
General Word editing remains unfinished; see LOCAL_COMMANDS.md for scope.
No-model-first triage precedes the selected OpenAI brain; uncertain matches ask
locally. Message route counts and counterfactual dollar savings omit message text.
The existing OpenAI brain is preserved; no new paid dependency or fallback added.
Streaming, taskProgress and savingsSummary backend hooks are ready for frontend
wiring. Live hosted streaming passed; native streaming display is unverified.
Explicit Word creation excludes Markdown writers and guards wrong-format writes.
Local image files can provide bounded pixels to the configured vision model.
One synthetic three-shape inference succeeded in 178 seconds; broad visual accuracy
and interactive speed remain unfinished. Read the audit for native-command evidence.
The bundled PowerPoint template needs no Node runtime on the user's machine.
All AI remains local by default. No model defaults or weights were changed.
Voice is half duplex; actual microphone/speaker quality is still unverified.
Read audit.json for the precise evidence and remaining work.
'''
    target = releases / f'Ano-{version}.zip'
    with ZipFile(target, 'x', ZIP_DEFLATED) as archive:
        archive.write(wheel, wheel.name)
        archive.writestr('READ-ME.txt', readme)
        archive.writestr('audit.json', json.dumps(audit, indent=2))
        archive.write(root / 'LOCAL_COMMANDS.md', 'LOCAL_COMMANDS.md')
        contract = runtime / 'ui-backend-contract.json'
        if contract.exists():
            archive.write(contract, 'ui-backend-contract.json')
    (releases / f'Ano-{version}-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    progress_path = runtime / 'full-capability-implementation.json'
    progress = json.loads(progress_path.read_text(encoding='utf-8-sig'))
    progress['at_utc'] = audit['utc']
    progress['current_work'] = (f'{version}: ' + audit['deployment'] +
        f' Version-specific native cases recorded: {len(native_cases)}. All nine areas still have unfinished requirements.')
    progress['verification'].update({'full_tests': int(match[1]), 'qml_smoke': 'Passed',
        'runtime_deployment': audit['deployment'], 'native_cases': native_cases})
    progress['remaining_blockers'] = audit['not_completed'] + ['Wider natural-language task and cancellation/resume validation']
    progress['evidence'] += [str(test_log.relative_to(root)), str(ui_path.relative_to(root)),
                             '.runtime/PowerPoint-fixture-20261027.pptx', f'releases/Ano-{version}-audit.json']
    if native_path.exists():
        progress['evidence'].append(str(native_path.relative_to(root)))
    progress_path.write_text(json.dumps(progress, indent=2), encoding='utf-8')
    snapshot = runtime / (f'source-{version}-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip')
    with ZipFile(snapshot, 'x', ZIP_DEFLATED) as archive:
        for folder in ('ano', 'tests', 'tools'):
            for path in (root / folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    archive.write(path, str(path.relative_to(root)))
        for name in ('pyproject.toml', 'README.md', 'LOCAL_COMMANDS.md'):
            archive.write(root / name, name)
    print(json.dumps({'release': str(target), 'tests': int(match[1]), 'overall_goal_complete': False}, indent=2))


if __name__ == '__main__':
    main()
