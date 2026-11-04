"""A disposable GUI fixture. Uses synthetic text only; never an actual login."""
import json
from pathlib import Path
import sys

from PySide6.QtCore import QTimer, Qt, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel

app = QApplication([])
report = Path(sys.argv[1])
command = report.with_suffix('.command')
window = QWidget()
window.setWindowTitle('Ano synthetic control probe')
window.setWindowFlag(Qt.WindowStaysOnTopHint)
layout = QVBoxLayout(window)
layout.addWidget(QLabel('Ano test window — synthetic data only'))
text = QLineEdit()
text.setAccessibleName('Synthetic text field')
layout.addWidget(text)
password = QLineEdit()
password.setEchoMode(QLineEdit.Password)
password.setAccessibleName('Synthetic password field')
layout.addWidget(password)
click = QPushButton('Test click')
layout.addWidget(click)
clicks = [0]
click.clicked.connect(lambda: clicks.__setitem__(0, clicks[0] + 1))
window.setGeometry(40, 520, 400, 200)
window.show()
window.raise_()
window.activateWindow()
text.setFocus()

def update():
    if command.exists():
        action = command.read_text()
        command.unlink()
        if action == 'password': password.setFocus()
        if action == 'quit': app.quit()
    center = click.mapToGlobal(QPoint(click.width() // 2, click.height() // 2))
    payload = dict(hwnd=int(window.winId()), text=text.text(), clicks=clicks[0],
                   active=window.isActiveWindow(),
                   password_filled=password.text() == 'synthetic-password',
                   password_focus=password.hasFocus(), button=[center.x(), center.y()])
    temporary = report.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload))
    temporary.replace(report)

timer = QTimer()
timer.setInterval(80)
timer.timeout.connect(update)
timer.start()
QTimer.singleShot(30000, app.quit)
app.exec()
