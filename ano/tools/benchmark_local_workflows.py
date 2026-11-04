"""Measure verified local file/note workflows in an isolated temporary workspace.

App opening is a dispatch stub, not a live desktop test. No LLM or API is called.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.plugins.apps import Apps
from ano.plugins.notes import Notebook
from ano.plugins.workshop import Workshop
from ano.plugins.word_documents import WordDocuments
from ano.routines import LocalRoutines, RoutineBrain, RoutineStore
from ano.tools import Toolbox


def benchmark(out):
    results = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        launches = []
        workshop = Workshop(root / 'workspace', None)
        word = WordDocuments(workshop, opener=launches.append)
        notebook = Notebook(root / 'notes.sqlite3')
        apps = Apps(listing=lambda: [{'Name': 'Word', 'AppID': 'word-fixture'}], launch=launches.append)
        tools = Toolbox(lambda: [root], plugins=[workshop, word, notebook, apps])
        fallback = Mock()
        fallback.reply.side_effect = AssertionError('Unexpected model fallback')
        brain = RoutineBrain(LocalRoutines(tools, RoutineStore(root / 'routines.sqlite3')), fallback)
        cases = [
            ('Open Microsoft Word', lambda: 'shell:AppsFolder\\word-fixture' in launches, 'Dispatch stub only; not live app opening'),
            ('opene microsoft word and make sure you make a document on everything on Ano',
             lambda: len(list(workshop.root.glob('Ano report*.docx'))) == 1, 'Real .docx creation and internal text read-back; open is a stub'),
            ('Save a note: Example note', lambda: 'Example note' in notebook.search_notes()[0], 'Real SQLite note read-back'),
            ('Write word document Probe: Exact body.', lambda: word.read_word_document('Probe')[0].endswith('Exact body.'), 'Real .docx exact text read-back'),
            ('List my documents', lambda: True, 'Read-only workspace listing; response checked'),
            ('Show my notes', lambda: True, 'Read-only notes query; response checked'),
        ]
        for request, verify, scope in cases:
            started = time.perf_counter()
            try:
                response = brain.reply([{'role': 'user', 'body': request}])
                passed = bool(verify()) and 'No LLM used' in response
                if request == 'List my documents':
                    passed = passed and 'Probe.docx' in response
                if request == 'Show my notes':
                    passed = passed and 'Example note' in response
                reason = '' if passed else 'Verification did not pass'
            except Exception as error:
                passed, reason = False, str(error)
            results.append({'request': request, 'passed': passed,
                            'seconds': round(time.perf_counter() - started, 4), 'scope': scope, 'reason': reason})
        fallback.reply.assert_not_called()
    report = {'scope': 'Isolated local integration fixtures, not general AI ability or live desktop completion.',
              'passed': sum(item['passed'] for item in results), 'total': len(results), 'results': results}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    benchmark(parser.parse_args().out)
