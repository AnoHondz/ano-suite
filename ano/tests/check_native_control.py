"""Manual native integration check in our own throwaway window, not user apps."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from ano.desktop_native import WindowsDesktop


def wait_for(report, condition=lambda row: True):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            row = json.loads(report.read_text())
            if condition(row): return row
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise AssertionError('Synthetic control probe did not reach the expected state.')


with tempfile.TemporaryDirectory(prefix='ano-control-probe-') as directory:
    report = Path(directory) / 'report.json'
    command = report.with_suffix('.command')
    environment = dict(os.environ)
    environment.pop('QT_QPA_PLATFORM', None)
    fixture = Path(__file__).with_name('native_control_probe.py')
    process = subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), str(fixture), str(report)], env=environment)
    try:
        row = wait_for(report, lambda data: data['active'])
        backend = WindowsDesktop()
        print('Click the FIRST text box in the synthetic test window within eight seconds.', flush=True)
        row = wait_for(report, lambda data: backend.user.GetForegroundWindow() == data['hwnd'])
        target, size, image = backend.snapshot(row['hwnd'])
        assert image and size[0] > 0 and target['title'] == 'Ano synthetic control probe'
        backend.perform(target, size, 'type', {'text':'Hello Ano'}, lambda: True)
        wait_for(report, lambda data: data['text'] == 'Hello Ano')
        row = wait_for(report)
        left, top, right, bottom = target['rect']
        x = round((row['button'][0] - left) * size[0] / (right-left))
        y = round((row['button'][1] - top) * size[1] / (bottom-top))
        backend.perform(target, size, 'click', {'x':x, 'y':y}, lambda: True)
        wait_for(report, lambda data: data['clicks'] == 1)
        command.write_text('password')
        wait_for(report, lambda data: data['password_focus'])
        target, runtime_id = backend.password_target(row['hwnd'])
        backend.fill_password(target, runtime_id, 'synthetic-password', lambda: True)
        wait_for(report, lambda data: data['password_filled'])
        print('Native screenshot, Unicode typing, mouse click, password-field verification, and dummy password fill passed.')
    finally:
        command.write_text('quit')
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()  # Only the disposable helper process created above.
            process.wait(timeout=5)
