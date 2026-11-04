"""Tools a background Claude Code job uses to finish its work on Ano (see claude_code.HARD_RULES).

    python -m ano.self_update test                      full test suite (live RAM reading replaced by a calm one)
    python -m ano.self_update renew-forex --note TEXT   reruns the suite; renews the OANDA practice approval only if all pass
    python -m ano.self_update restart [--mail]          stops Ano so the supervisor relaunches it on the new code

The user chose on 2026-11-03 that background jobs may renew the forex practice approval on their own when every
test passes. Practice account only; risk limits live in the code and are covered by the suite.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_suite():
    import unittest
    from types import SimpleNamespace
    from unittest.mock import patch
    calm = SimpleNamespace(percent=50.0, available=8 * 1024 ** 3, total=16 * 1024 ** 3, used=8 * 1024 ** 3)
    with patch('psutil.virtual_memory', return_value=calm):
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT / 'tests'))
        result = unittest.TextTestRunner(verbosity=0).run(suite)
    for test, trace in result.failures + result.errors:
        print('FAILED:', test.id(), '-', trace.strip().splitlines()[-1])
    print(f'{result.testsRun} tests, {len(result.failures) + len(result.errors)} failing')
    return result.wasSuccessful()


def suite_passes():
    """A fresh interpreter, so the suite sees the code on disk and not modules this process already imported."""
    return subprocess.run([sys.executable, '-m', 'ano.self_update', 'test'], cwd=str(ROOT)).returncode == 0


def renew_forex(note, state=None, passes=suite_passes):
    from . import forex_v2
    from .forex_shadow import execution_settings, fingerprint
    from .paths import data_directory
    from .reliability import atomic_json, read_json
    if not note.strip():
        return 'refused: say what changed with --note'
    if not passes():
        return 'refused: the test suite is failing, so the forex practice approval was NOT renewed'
    state = Path(state or data_directory())
    path = state / 'forex-native-execution.json'
    old = read_json(path)
    atomic_json(path, {**old, 'version': forex_v2.VERSION, 'enabled': True, 'provider': 'oanda_practice',
                       'authorization': 'explicit_user_practice_auto', 'engine': fingerprint(),
                       'requested_at': datetime.now(timezone.utc).isoformat(),
                       'renewal': 'Background Claude job after a full passing test suite (user standing approval 2026-11-03): ' + note.strip()[:600]})
    return 'renewed: ' + json.dumps(execution_settings(state))


def restart_scorpred():
    """Stop local ScorPred and start it again with its own launcher (Start-ScorPred.ps1)."""
    import psutil
    from .claude_code import SCORPRED
    for process in psutil.process_iter(['cmdline']):
        try:
            if any('run_local.py' in part for part in process.info['cmdline'] or []) and any(str(SCORPRED).lower() in part.lower() for part in process.info['cmdline']):
                process.kill()
        except psutil.Error:
            pass
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCORPRED / 'Start-ScorPred.ps1')],
                            cwd=str(SCORPRED), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=240, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # no output pipes: the server it starts would inherit them and keep this waiting until the timeout
    return 'ScorPred restarted' if result.returncode == 0 else f'ScorPred launcher failed (exit {result.returncode})'


def restart(mail=False, scorpred=False):
    """Stop the Ano app (and optionally the email worker); the supervisor relaunches Ano within about a minute."""
    import psutil
    from .supervisor import processes
    notes = [restart_scorpred()] if scorpred else []
    stopped = []
    for module in ('ano',) + (('ano.sports_mail',) if mail else ()):
        for pid in processes(module):
            try:
                psutil.Process(pid).kill()
                stopped.append(pid)
            except psutil.Error:
                pass
    if mail:
        from .paths import data_directory
        from .repairs import restart_mail
        import time
        time.sleep(3)
        restart_mail(data_directory() / 'sports-mail')
    return '; '.join(notes + [f'stopped {len(stopped)} process(es); the supervisor restarts Ano within about a minute'])


def main():
    parser = argparse.ArgumentParser(prog='python -m ano.self_update')
    parser.add_argument('action', choices=('test', 'renew-forex', 'restart'))
    parser.add_argument('--note', default='')
    parser.add_argument('--mail', action='store_true')
    parser.add_argument('--scorpred', action='store_true')
    args = parser.parse_args()
    if args.action == 'test':
        sys.exit(0 if run_suite() else 1)
    if args.action == 'renew-forex':
        message = renew_forex(args.note)
        print(message)
        sys.exit(0 if message.startswith('renewed') else 1)
    print(restart(args.mail, args.scorpred))


if __name__ == '__main__':
    main()
