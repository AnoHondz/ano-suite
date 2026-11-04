"""Separate native sports workspace; leaves the shared QML front end untouched."""
import json
from pathlib import Path
import sys

from PySide6.QtCore import QLockFile, QProcess, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel,
                              QMainWindow, QPushButton, QTextBrowser, QVBoxLayout, QWidget)
from .sports_reports import LOGO
from .sports_mail import SCOR_ROOT, STATE


class SportsDesk(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Ano · Sports desk');self.setWindowIcon(QIcon(str(LOGO)))
        self.resize(820,850);self.setMinimumSize(500,600)
        self.process=None
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
        header=QHBoxLayout();logo=QLabel();logo.setPixmap(QPixmap(str(LOGO)).scaled(64,64,Qt.KeepAspectRatio,Qt.SmoothTransformation));header.addWidget(logo)
        title=QLabel('ANO  /  SPORTS DESK\nHey boss, what are we checking today?');title.setObjectName('title');header.addWidget(title,1);layout.addLayout(header)
        options=QHBoxLayout();self.sport=QComboBox();self.sport.setAccessibleName('Report sport');self.sport.addItems(['All sports','NBA','Soccer','NFL']);options.addWidget(self.sport)
        self.props=QCheckBox('Include player props');self.props.setChecked(True);options.addWidget(self.props);options.addStretch();layout.addLayout(options)
        actions=QHBoxLayout();self.buttons=[]
        for text,fn in [('Refresh report',lambda:self.run('preview')),('Email me report',lambda:self.run('send')),('Phone status',lambda:self.run('status')),('Paper bot health',lambda:self.run('preview','trading status'))]:
            button=QPushButton(text);button.clicked.connect(fn);self.buttons.append(button);actions.addWidget(button)
        layout.addLayout(actions)
        self.status=QLabel('Local Scorpred analysis · No model credits · No bets placed');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.report=QTextBrowser();self.report.setOpenLinks(False);self.report.setOpenExternalLinks(False);layout.addWidget(self.report,1)
        phone=QHBoxLayout()
        for text,action in [('Enable phone email commands','enable'),('Disable phone commands','disable')]:
            button=QPushButton(text);button.clicked.connect(lambda checked=False,a=action:self.run(a));self.buttons.append(button);phone.addWidget(button)
        layout.addLayout(phone)
        note=QLabel('Phone: reply to an Ano email with picks, nba props, soccer picks, nfl picks, status or help.\nOnly your own Gmail account can command this desk. Keep your laptop on and online.');note.setWordWrap(True);layout.addWidget(note)
        self.setStyleSheet('QMainWindow,QWidget{background:#edf2f6;color:#192b3b;font:14px "Segoe UI";} QLabel#title{font-size:20px;font-weight:600;} QPushButton,QComboBox{background:white;border:1px solid #cad8e4;border-radius:8px;padding:10px;} QPushButton:hover{background:#dcecf8;} QPushButton:disabled{color:#8393a0;} QTextBrowser{background:white;border:1px solid #dbe4ed;border-radius:12px;padding:16px;}')
        latest=STATE/'latest-report.html'
        if latest.is_file():self.report.setHtml(latest.read_text(encoding='utf-8'))
        else:self.report.setHtml('<h2>Hey boss, welcome to your sports desk.</h2><p>Choose a sport, then refresh. You can review the results before emailing them.</p><p>SKIP stays SKIP. Player props distinguish historical targets from verified sportsbook lines.</p>')

    def run(self,action,override=None):
        if self.process is not None:return
        sport=('all','nba','soccer','nfl')[self.sport.currentIndex()]
        command=('props' if self.props.isChecked() else 'picks') if sport=='all' else sport+(' props' if self.props.isChecked() else ' picks')
        command=override or command
        # All-sports picks intentionally includes props in the phone grammar.
        args=['-m','ano.sports_mail',action]
        if action in ('preview','send'):args+=['--command',command]
        if action in ('preview','send') and not self.props.isChecked():args+=['--without-props']
        self.process=QProcess(self);self.process.setProgram(str(SCOR_ROOT/'.venv'/'Scripts'/'python.exe'));self.process.setArguments(args)
        self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        self.process.finished.connect(lambda code,status:self.finished(action,code))
        self.process.errorOccurred.connect(lambda error:self.failed())
        for b in self.buttons:b.setEnabled(False)
        self.status.setText('Working locally… analysis may take several minutes. This does not claim completion or email delivery.')
        self.process.start()

    def failed(self):
        if self.process:self.status.setText('The local worker could not start. No completion claimed.');self.cleanup()

    def cleanup(self):
        if self.process:self.process.deleteLater();self.process=None
        for b in self.buttons:b.setEnabled(True)

    def finished(self,action,code):
        if not self.process:return
        raw=bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        try:
            if code:raise ValueError()
            result=json.loads(raw)
            if result.get('state')=='preview':
                self.report.setHtml(Path(result['path']).read_text(encoding='utf-8'));self.status.setText('Report generated locally. No email sent. Check timestamps and line sources below.')
            elif result.get('state')=='sent':self.status.setText('Email accepted by SMTP for you@example.com. Inbox delivery is not guaranteed.')
            elif action=='status':self.status.setText('Phone commands '+('ON' if result.get('enabled') else 'OFF')+' · Worker: '+str(result.get('heartbeat',{}).get('state','not yet observed')))
            else:self.status.setText('Result: '+str(result.get('state','unknown'))+' · For uncertain email delivery, check the receipt before retrying.')
        except (ValueError,OSError):self.status.setText('Worker did not return a verified result. Check local sports-mail receipts; do not assume an email was sent.')
        self.cleanup()

    def closeEvent(self,event):
        if self.process is not None:
            self.status.setText('A report/email task is still running. Wait for its result before closing.');event.ignore()
        else:event.accept()


def main():
    app=QApplication(sys.argv);STATE.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(STATE/'desk.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(0):return
    window=SportsDesk();window.show();app.exec();lock.unlock()


if __name__=='__main__':main()
