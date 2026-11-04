"""Bounded sequential public-fixture probes. Never executes model tool calls."""
import argparse
import json
from pathlib import Path
import re
import time
import urllib.request


def request(path, payload):
    req = urllib.request.Request('http://127.0.0.1:11434' + path,
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=120) as response:
        return json.load(response)


def run(model, output):
    cases = [
        ('same_day', [('user', 'Ravi arrived Monday at 8:30 PM.'),
                      ('assistant', 'Ravi arrived Monday at 8:30 PM.'),
                      ('user', 'What day and time is 45 minutes earlier?')],
         lambda text: bool(re.search(r'\bmonday\b', text, re.I) and re.search(r'7:45\s*p\.?m', text, re.I)
                           and not re.search(r'\b(sunday|tuesday)\b', text, re.I))),
        ('rollover', [('user', 'It is Tuesday at 12:15 AM. What day and time was it 30 minutes earlier?')],
         lambda text: bool(re.search(r'\bmonday\b', text, re.I) and re.search(r'11:45\s*p\.?m', text, re.I)
                           and not re.search(r'\btuesday\b', text, re.I))),
        ('follow_constraints', [('user', 'Return exactly these three words in reverse order, with spaces and no punctuation: oak fern moss')],
         lambda text: text.strip() == 'moss fern oak'),
    ]
    report = {'model': model, 'scope': 'Synthetic local reasoning only; no app actions or parity claim.',
              'default_changed': False, 'results': []}
    for name, messages, predicate in cases:
        started = time.monotonic()
        try:
            response = request('/api/chat', {'model': model, 'stream': False, 'think': False,
                'keep_alive': '30s', 'messages': [{'role': 'system', 'content': 'Answer briefly and accurately. /no_think'}] +
                    [{'role': role, 'content': text} for role, text in messages],
                'options': {'num_ctx': 4096, 'num_predict': 128, 'num_thread': 4, 'temperature': 0}})
            text = response.get('message', {}).get('content', '')
            result = {'case': name, 'answer': text, 'passed': predicate(text), 'done_reason': response.get('done_reason')}
        except Exception as error:
            result = {'case': name, 'passed': False, 'error': type(error).__name__}
        result['seconds'] = round(time.monotonic() - started, 3)
        report['results'].append(result)
        report['gate_passed'] = len(report['results']) == len(cases) and all(r['passed'] for r in report['results'])
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        if result.get('error'):
            break  # A timed-out server must reach terminal state before another probe.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.model, args.out)
