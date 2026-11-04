"""Read-only local model tool-selection probe; never executes a returned tool.

This measures selection on synthetic requests, not successful application actions.
Use --model NAME --out PATH. All traffic is hard-coded to local Ollama.
"""
import argparse
import json
from pathlib import Path
import tempfile
import time
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ano.local_profile import task_tools, fast_system
from ano.plugins.apps import Apps
from ano.plugins.notes import Notebook
from ano.plugins.workshop import Workshop
from ano.plugins.word_documents import WordDocuments
from finetune.evaluate import check_call


CASES = [
    ('Open Microsoft Word', 'open_app', {'name': 'Word'}),
    ('opene microsoft word and make sure you make a document on everything on Ano',
     'create_ano_report', {'open_document': True}),
    ('Save a note: Example note', 'add_note', {'text': 'Example note'}),
    ('Write word document Probe: Exact body.', 'write_word_document',
     {'name': 'Probe', 'text': 'Exact body.'}),
    ('List my documents', 'list_documents', {}),
    ('Show my notes', 'search_notes', {'query': ''}),
]


def probe(model, out, limit=6):
    results = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workshop = Workshop(root / 'documents', None)
        definitions = sum((plugin.definitions() for plugin in
                           (Apps(), Notebook(root / 'notes.db'), workshop,
                            WordDocuments(workshop))), [])
        for prompt, tool, arguments in CASES[:limit]:
            request = {'model': model, 'messages': [
                {'role': 'system', 'content': fast_system()},
                {'role': 'user', 'content': prompt}],
                'tools': task_tools(definitions, prompt), 'stream': False,
                'think': False, 'keep_alive': '30s', 'options': {'temperature': 0, 'num_predict': 128,
                                           'num_ctx': 4096}}
            started = time.monotonic()
            try:
                req = urllib.request.Request('http://127.0.0.1:11434/api/chat',
                    data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=120) as response:
                    body = json.load(response)
                message = body.get('message', {})
                passed, reason = check_call({'name': tool, 'arguments': arguments}, message)
                result = {'request': prompt, 'passed': passed, 'reason': reason,
                          'seconds': round(time.monotonic() - started, 3),
                          'response': message, 'prompt_tokens': body.get('prompt_eval_count'),
                          'output_tokens': body.get('eval_count')}
            except Exception as error:
                result = {'request': prompt, 'passed': False, 'reason': type(error).__name__,
                          'seconds': round(time.monotonic() - started, 3)}
            results.append(result)
            report = {'model': model, 'scope': 'Synthetic selection only; no tool executed.',
                      'passed': sum(r['passed'] for r in results), 'total': len(results),
                      'results': results}
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({k: v for k, v in result.items() if k != 'response'}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--limit', type=int, default=6)
    args = parser.parse_args()
    probe(args.model, args.out, args.limit)
