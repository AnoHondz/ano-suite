"""One synthetic local recall probe; no user memory, app actions or paid API."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.facts import FactStore, FactMemoryBrain
from ano.gpt import GPTClient


def main(model, output):
    with TemporaryDirectory() as folder:
        path = Path(folder) / 'facts.db'
        store = FactStore(path)
        wrapper = FactMemoryBrain(store, None)
        saved = wrapper.reply([{'role': 'user', 'body': 'remember fact fixture code: BIRCH-71',
                                'source_chat_id': 101, 'source_message_id': 202}])
        reopened = FactStore(path)
        brain = GPTClient('local', model=model, base_url='http://127.0.0.1:11434/v1',
                          profile='chat', output_tokens=256, memory_context=reopened.context)
        started = time.monotonic()
        try:
            answer = brain.reply([{'role': 'user', 'body': 'What is the saved fixture code? Reply with only the code.'}])
            result = {'answer': answer, 'passed': answer.strip() == 'BIRCH-71'}
        except Exception as error:
            result = {'error': type(error).__name__, 'passed': False}
        report = {'model': model, 'scope': 'One synthetic saved fact, reopened database and a fresh local-model conversation. Not native app or general memory proof.',
                  'saved': saved, 'seconds': round(time.monotonic() - started, 3), **result}
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    main(args.model, args.out)
