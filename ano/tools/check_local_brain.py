"""Synthetic local-only tool-use probe: python -m tools.check_local_brain.

No personal files, browser sessions, desktop actions, or paid providers are used.
"""
import argparse
import json
import secrets
import time

from ano.brain import AnoBrain
from ano.claude import ClaudeError
from ano.tools import Toolbox


class ReadinessProbe:
    def __init__(self, name='readiness_probe'):
        self.name = name
        self.receipt = secrets.token_hex(4)
        self.calls = 0

    def definitions(self):
        return [{'name': self.name,
                 'description': 'Read the synthetic diagnostic status and its receipt code. No side effects.',
                 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]

    def run(self, name, arguments):
        self.calls += 1
        return json.dumps({'status': 'ready', 'receipt': self.receipt}), ''


class ProbeTools(Toolbox):
    def _local_definitions(self):
        # This diagnostic exposes only its fixture, never real filesystem tools.
        return []

    def run(self, name, arguments):
        if name != self.plugins[0].name:
            return 'Only the synthetic diagnostic tool is available in this request.', '', True
        return super().run(name, arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='ano-local:latest')
    parser.add_argument('--profile', choices=('full', 'compact', 'fast'), default='fast')
    args = parser.parse_args()
    # Reuse an advertised compact-profile name, implemented only by our fixture.
    probe = ReadinessProbe('system_overview' if args.profile in ('compact', 'fast') else 'readiness_probe')
    tools = ProbeTools(lambda: [], plugins=[probe])
    brain = AnoBrain('local', tools, model=args.model,
                     base_url='http://127.0.0.1:11434/v1', output_tokens=256, profile=args.profile)
    started = time.monotonic()
    try:
        reply = brain.reply([{'role': 'user', 'body':
            f'Run {probe.name}, then report its status and exact receipt code in one sentence.'}])
        passed = probe.calls == 1 and probe.receipt in reply and 'ready' in reply.casefold()
        report = {'passed': passed, 'model': args.model, 'tool_calls': probe.calls, 'reply': reply}
    except ClaudeError as error:
        report = {'passed': False, 'model': args.model, 'tool_calls': probe.calls, 'error': str(error)}
    report['elapsed_seconds'] = round(time.monotonic() - started, 1)
    report['profile'] = args.profile
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
