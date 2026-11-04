"""Download an explicitly selected local Ollama library model; never run it."""
import argparse
import json
from pathlib import Path
import re
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.model_roles import local_model_name
from ano.local_runtime import installed_models


def main(model):
    if not local_model_name(model):
        raise ValueError('Use a simple local library model name, not a cloud or remote model.')
    if model in installed_models():
        print('Already installed: ' + model)
        return
    progress = Path(__file__).resolve().parents[1] / '.runtime' / ('pull-' + re.sub(r'[^a-zA-Z0-9_-]', '_', model) + '.json')
    progress.parent.mkdir(exist_ok=True)
    request = urllib.request.Request('http://127.0.0.1:11434/api/pull',
        data=json.dumps({'model': model, 'stream': True}).encode(), headers={'Content-Type': 'application/json'})
    previous = None
    with urllib.request.urlopen(request, timeout=120) as response:
        for line in response:
            event = json.loads(line)
            if event.get('error'):
                raise RuntimeError(event['error'])
            total, completed = event.get('total', 0), event.get('completed', 0)
            percent = int(100 * completed / total) if total else None
            state = {'model': model, 'status': event.get('status'), 'percent': percent}
            progress.write_text(json.dumps(state, indent=2), encoding='utf-8')
            marker = state['status'], percent // 20 if percent is not None else None
            if marker != previous:
                print(json.dumps(state), flush=True)
                previous = marker


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    main(parser.parse_args().model)
