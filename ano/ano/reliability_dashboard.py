"""Native diagnostic view. No trading or email controls."""
import sys
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QTimer, QLockFile
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QTextBrowser
from .paths import data_directory
from .reliability import Reliability,render


def load_fonts(app):
    for path in (Path(__file__).parent/'assets/fonts').glob('*.ttf'):
        QFontDatabase.addApplicationFont(str(path))
    if 'M PLUS Rounded 1c' in QFontDatabase.families():app.setFont(QFont('M PLUS Rounded 1c',10))


class Signals(QObject):
    done=Signal(object)


class ReadHealth(QRunnable):
    def __init__(self,service,verify):
        super().__init__();self.service,self.verify=service,verify;self.signals=Signals()
    def run(self):
        try:self.signals.done.emit(self.service.snapshot(verify_broker=self.verify))
        except Exception:self.signals.done.emit(None)


class Dashboard(QMainWindow):
    def __init__(self,service):
        super().__init__();self.service=service;self.job=None
        self.setWindowTitle('Ano · Reliability & paper readiness');self.resize(1000,780)
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root)
        controls=QHBoxLayout();self.refresh=QPushButton('Refresh health');self.verify=QPushButton('Verify paper readiness')
        self.refresh.clicked.connect(lambda:self.read(False));self.verify.clicked.connect(lambda:self.read(True))
        controls.addWidget(self.refresh);controls.addWidget(self.verify);controls.addStretch();layout.addLayout(controls)
        self.status=QLabel('Read-only health checks. No orders or emails.');layout.addWidget(self.status)
        self.view=QTextBrowser();self.view.setOpenLinks(False);layout.addWidget(self.view)
        self.timer=QTimer(self);self.timer.timeout.connect(lambda:self.read(False));self.timer.start(60000)
        self.read(False)

    def read(self,verify):
        if self.job:return
        self.status.setText('Reading actual health evidence…')
        self.refresh.setEnabled(False);self.verify.setEnabled(False)
        self.job=ReadHealth(self.service,verify);self.job.signals.done.connect(self.finished)
        QThreadPool.globalInstance().start(self.job)

    def finished(self,report):
        self.job=None;self.refresh.setEnabled(True);self.verify.setEnabled(True)
        if report:
            self.view.setHtml(render(report));self.status.setText('Observation time: '+report['at']+' · Read-only health check; background permissions shown below.')
        else:self.status.setText('Health could not be verified. No readiness claimed.')

    def closeEvent(self,event):
        if self.job:event.ignore();self.status.setText('A read-only check is finishing. Wait for its result before closing.')
        else:event.accept()


def main():
    app=QApplication(sys.argv);load_fonts(app);state=data_directory();state.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(state/'reliability-dashboard.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(0):return
    window=Dashboard(Reliability(state));window.show();app.exec();lock.unlock()


if __name__=='__main__':main()
