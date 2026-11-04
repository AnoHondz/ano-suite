"""Run with the project virtualenv: python tests/smoke_ui.py."""
import os
import tempfile
import time
from threading import Thread
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import timedelta

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'

from PySide6.QtCore import QObject, QMetaObject, QPoint, QPointF, Qt, QTimer, QUrl, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtGui import QFontDatabase, QImage, QColor, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ano.app import Controller, load_fonts
from ano.memory import Memory
from ano.screen import ScreenCapture, PreviewProvider
from ano.activity import ActivityTracker, utcnow, stamp
from ano.voice import VoiceService
from ano.speech import SpeechService
from ano.tools import FileAccess
from ano.plugins.mail import Outbox
from ano.plugins.system import Fixes
from ano.review import Reviews
from ano.desktop import DesktopControl
from ano.plugins.browser import BrowserHealth
from ano.login_vault import LoginStore, LoginVault


app = QApplication([])
# The offscreen platform does not discover Windows fonts automatically.
font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
for filename in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'SegoeIcons.ttf', 'segmdl2.ttf'):
    if (font_dir / filename).exists():
        QFontDatabase.addApplicationFont(str(font_dir / filename))
load_fonts()
diagnostics = []
failures = []
qInstallMessageHandler(lambda kind, context, message: diagnostics.append(message))
with tempfile.TemporaryDirectory() as directory:
    memory = Memory(Path(directory) / 'test.sqlite3')
    # Persisted floating mode must not show a second native window during engine.load.
    memory.set_setting('floating', True)
    controller = Controller(memory)
    activity = ActivityTracker(memory, lambda: None, lambda: True)
    controller.activity = activity
    activity.tick()
    engine = QQmlApplicationEngine()
    preview = PreviewProvider()
    capture = ScreenCapture(lambda: not controller.paused and not controller.busy, preview)
    controller.screen_capture = capture
    def recognize(stop, cancel, listening):
        listening()
        stop.wait(2)
        return 'example voice draft'
    voice = VoiceService(SimpleNamespace(available=lambda: True, recognize=recognize),
                         lambda: not controller.paused and not activity.locked)
    speaker_backend = Mock()
    speaker_backend.available.return_value = True
    speaker_backend.speaking.return_value = False
    speaker_backend.voices = ['coral', 'nova']
    speaker_backend.voice = 'coral'
    speaker_backend.error = ''
    speech = SpeechService(memory, speaker_backend, lambda: not controller.paused and not activity.locked)
    controller.voice = voice
    controller.speech = speech
    voice.starting.connect(speech.stop)
    engine.addImageProvider('capture', preview)
    engine.rootContext().setContextProperty('ano', controller)
    engine.rootContext().setContextProperty('screenCapture', capture)
    engine.rootContext().setContextProperty('activity', activity)
    engine.rootContext().setContextProperty('voice', voice)
    engine.rootContext().setContextProperty('speech', speech)
    memory.set_setting('file_folders', [directory])
    files = FileAccess(memory)
    engine.rootContext().setContextProperty('files', files)
    outbox = Outbox(lambda: not controller.paused)
    sent = []
    outbox.deliver = sent.append
    engine.rootContext().setContextProperty('outbox', outbox)
    fix_runs = []
    fixes = Fixes(lambda: not controller.paused, runner=lambda fix: fix_runs.append(fix) or 'Example output')
    engine.rootContext().setContextProperty('fixes', fixes)
    reviews = Reviews(lambda: not controller.paused, timeout=2)
    desktop_preview = PreviewProvider()
    desktop_backend = Mock()
    desktop_control = DesktopControl(desktop_backend, reviews, lambda: not controller.paused, desktop_preview)
    class FakeVault:
        def __init__(self): self.data = {}
        def get_password(self, service, account): return self.data.get((service, account))
        def set_password(self, service, account, value): self.data[(service, account)] = value
        def delete_password(self, service, account): self.data.pop((service, account), None)
    saved_logins = LoginVault(LoginStore(FakeVault()), reviews, desktop_backend, lambda: not controller.paused, desktop_control.stop)
    engine.addImageProvider('control', desktop_preview)
    engine.rootContext().setContextProperty('reviews', reviews)
    engine.rootContext().setContextProperty('desktopControl', desktop_control)
    engine.rootContext().setContextProperty('savedLogins', saved_logins)
    browser_health = BrowserHealth(False)  # not configured in the smoke test
    engine.rootContext().setContextProperty('browserHealth', browser_health)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'ano/qml/Main.qml')))
    assert engine.rootObjects(), diagnostics
    window = engine.rootObjects()[0]
    # The offscreen platform has no Windows colour scheme, so dark mode is forced for that preview.
    if os.environ.get('ANO_PREVIEW_DARK'):
        window.setProperty('dark', True)
    capture.window = window

    def verify():
        try:
            assert window.property('uiReady'), 'UI readiness must be deferred until construction finishes'
            # A growing command menu must scroll inside the compact panel.
            main_menu = window.findChild(QObject, 'mainMenu')
            assert QMetaObject.invokeMethod(main_menu, 'open')
            QTest.qWait(150)
            assert main_menu.property('height') <= window.height() - 12
            menu_content = main_menu.property('contentItem')
            assert QMetaObject.invokeMethod(menu_content, 'positionViewAtEnd')
            QTest.qWait(150)
            quit_item = window.findChild(QObject, 'quitAno')
            quit_y = quit_item.mapToScene(QPointF(0, 0)).y()
            assert 0 <= quit_y and quit_y + quit_item.height() <= window.height(), (quit_y, quit_item.height(), window.height())
            assert QMetaObject.invokeMethod(main_menu, 'close')
            controller.setFloating(False)
            if os.environ.get('ANO_PREVIEW_PATH'):
                assert window.grabWindow().save(os.environ['ANO_PREVIEW_PATH'])
            # Dragging the header moves the whole panel and saves its position.
            drag_handle = window.findChild(QObject, 'panelDragHandle')
            drag_start = drag_handle.mapToScene(QPointF(drag_handle.width()/2, drag_handle.height()/2)).toPoint()
            old_x, old_y = window.x(), window.y()
            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, drag_start)
            QTest.mouseMove(window, drag_start + QPoint(24, 18), 20)
            # Release at the same screen position after the window itself has moved.
            release_position = drag_start + QPoint(24 - (window.x()-old_x), 18 - (window.y()-old_y))
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, release_position)
            QTest.qWait(400)
            assert (window.x(), window.y()) == (old_x + 24, old_y + 18), (old_x, old_y, window.x(), window.y(), drag_handle.property('nativeMove'))
            assert memory.setting('panel_position', {}) == {'x': window.x(), 'y': window.y()}
            # Collapsing leaves only the icon, positioned on screen; tapping restores chat.
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'collapseToIcon'), 'clicked')
            QTest.qWait(100)
            cloud_window = window.findChild(QObject, 'floatingIcon')
            assert not window.isVisible() and cloud_window.property('visible'), (window.isVisible(), window.property('visibility'), controller.floating, cloud_window.property('visible'))
            assert cloud_window.width() == 60 and cloud_window.height() == 60
            assert any(s.availableGeometry().contains(cloud_window.geometry()) for s in app.screens())
            if os.environ.get('ANO_CLOUD_PREVIEW_PATH'):
                assert cloud_window.grabWindow().save(os.environ['ANO_CLOUD_PREVIEW_PATH'])
            QTest.mouseClick(cloud_window, Qt.LeftButton, Qt.NoModifier, QPoint(cloud_window.width()//2, cloud_window.height()//2))
            app.processEvents()
            assert window.isVisible() and not cloud_window.property('visible')
            controller.send('Test a saved conversation')
            assert len(controller.messages) == 1
            # Exercise the real QML preview and image provider with synthetic pixels.
            image = QImage(1000, 700, QImage.Format_RGB32)
            image.fill(QColor('#eef3ef'))
            painter = QPainter(image)
            painter.setPen(QColor('#183c35'))
            font = painter.font()
            font.setPixelSize(40)
            painter.setFont(font)
            painter.drawText(70, 120, 'Example screen for Ano')
            font.setPixelSize(23)
            painter.setFont(font)
            painter.drawText(70, 190, 'A synthetic preview. No desktop pixels were captured.')
            painter.end()
            screen = SimpleNamespace(grabWindow=lambda _: SimpleNamespace(toImage=lambda: image))
            button = window.findChild(QObject, 'explainScreen')
            assert button
            assert QMetaObject.invokeMethod(button, 'triggered')
            question = window.findChild(QObject, 'captureQuestion')
            assert question.property('text') == 'Ano, what am I looking at?'
            capture.timer.stop()
            assert not window.isVisible()
            with patch('ano.screen.active_screen', return_value=screen):
                capture._take()
            app.processEvents()
            assert capture.ready
            assert window.isVisible()
            popup = window.findChild(QObject, 'capturePreview')
            assert popup and popup.property('visible')
            if os.environ.get('ANO_SCREEN_PREVIEW_PATH'):
                assert window.grabWindow().save(os.environ['ANO_SCREEN_PREVIEW_PATH'])
            capture.cancel()
            app.processEvents()
            assert not popup.property('visible')
            controller.togglePause()
            capture.begin()
            assert not capture.capturing
            controller.togglePause()
            controller.togglePause()
            controller.send('Must not be saved')
            assert len(controller.messages) == 1
            controller.togglePause()
            controller.search('conversation')
            assert len(controller.messages) == 1
            controller.search('unmatched')
            assert not controller.messages
            controller.search('')
            assert len(controller.messages) == 1
            # Timeline rendering and delete confirmation use synthetic app titles.
            now = utcnow()
            identifier = memory.start_activity('editor.exe', 'Example project', stamp(now - timedelta(minutes=5)))
            memory.touch_activity(identifier, stamp(now - timedelta(minutes=2)))
            activity.changed.emit()
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'openActivity'), 'triggered')
            app.processEvents()
            timeline = window.findChild(QObject, 'activityTimeline')
            assert timeline.property('visible')
            activity_list = window.findChild(QObject, 'activityList')
            assert activity_list.property('count') == 1
            if os.environ.get('ANO_ACTIVITY_PREVIEW_PATH'):
                assert window.grabWindow().save(os.environ['ANO_ACTIVITY_PREVIEW_PATH'])
            tracking_switch = window.findChild(QObject, 'activitySwitch')
            click_position = tracking_switch.mapToScene(QPointF(tracking_switch.width() / 2, tracking_switch.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, click_position)
            assert not activity.enabled
            assert not memory.setting('activity_enabled', True)
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, click_position)
            assert activity.enabled
            activity.search('missing')
            app.processEvents()
            assert activity_list.property('count') == 0
            activity.search('')
            app.processEvents()
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'deleteAllActivity'), 'clicked')
            app.processEvents()
            confirm = window.findChild(QObject, 'deleteActivityConfirm')
            assert confirm.property('visible')
            assert QMetaObject.invokeMethod(confirm, 'accept')
            app.processEvents()
            assert not memory.activities()
            assert activity_list.property('count') == 0
            QMetaObject.invokeMethod(timeline, 'close')
            QTest.qWait(250)
            # The folder list shows the allowed folders and removal persists.
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'openFiles'), 'triggered')
            app.processEvents()
            files_popup = window.findChild(QObject, 'filesPopup')
            assert files_popup.property('visible')
            assert window.findChild(QObject, 'folderList').property('count') == 1
            files.removeFolder(directory)
            app.processEvents()
            assert window.findChild(QObject, 'folderList').property('count') == 0
            assert memory.setting('file_folders', None) == []
            QMetaObject.invokeMethod(files_popup, 'close')
            QTest.qWait(250)
            # A prepared email waits in the review window; only the Send click delivers the edited text.
            outbox.propose({'to': 'person@example.com', 'cc': '', 'subject': 'Example', 'body': 'Draft text',
                            'inReplyTo': '', 'references': ''})
            app.processEvents()
            review = window.findChild(QObject, 'emailReview')
            assert review.property('visible') and not sent
            window.findChild(QObject, 'emailBody').setProperty('text', 'Edited text')
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'sendEmail'), 'clicked')
            for _ in range(100):
                app.processEvents()
                if not outbox.ready:
                    break
                time.sleep(.01)
            assert [item['body'] for item in sent] == ['Edited text'] and not outbox.ready
            QTest.qWait(250)
            assert not review.property('visible')
            # A proposed fix shows its command and only runs on Run fix.
            fixes.propose({'title': 'Restart the Example service', 'admin': True,
                           'explanation': 'The Example service stopped responding. Restarting it is safe; nothing is deleted.',
                           'command': 'Restart-Service -Name Example'})
            app.processEvents()
            fix_review = window.findChild(QObject, 'fixReview')
            assert fix_review.property('visible') and not fix_runs
            assert window.findChild(QObject, 'fixCommand').property('text') == 'Restart-Service -Name Example'
            if os.environ.get('ANO_FIX_PREVIEW_PATH'):
                QTest.qWait(250)
                assert window.grabWindow().save(os.environ['ANO_FIX_PREVIEW_PATH'])
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'runFix'), 'clicked')
            for _ in range(100):
                app.processEvents()
                if not fixes.ready:
                    break
                time.sleep(.01)
            assert [fix['title'] for fix in fix_runs] == ['Restart the Example service'] and not fixes.ready
            QTest.qWait(250)
            assert not fix_review.property('visible')
            # Push-to-talk sends the transcript when the button is released.
            talk = window.findChild(QObject, 'pushToTalk')
            position = talk.mapToScene(QPointF(talk.width() / 2, talk.height() / 2)).toPoint()
            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, position)
            for _ in range(300):
                app.processEvents()
                time.sleep(.01)
                if voice.recording:
                    break
            assert voice.recording, (voice.busy, voice.status, talk.property('enabled'), talk.property('pressed'))
            assert len(memory.recent()) == 1
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, position)
            for _ in range(100):
                if not voice.busy:
                    break
                app.processEvents()
                time.sleep(.01)
            assert not voice.busy
            app.processEvents()
            assert window.findChild(QObject, 'composer').property('text') == ''
            assert [m['body'] for m in memory.recent()][-1] == 'example voice draft'
            # A session stop transcript must not be forwarded again by QML
            # after the session's handler has already deactivated the session.
            previous_messages = list(memory.recent())
            voice._turn = True
            voice.transcript.emit('stop voice conversation')
            app.processEvents()
            assert memory.recent() == previous_messages
            assert window.findChild(QObject, 'composer').property('text') == ''
            voice._turn = False
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'spokenReplies'), 'clicked')  # Settings switch row
            assert speech.enabled and memory.setting('spoken_replies', False)
            window.setWidth(360)
            window.setHeight(480)
            app.processEvents()
            composer = window.findChild(QObject, 'composer')
            assert composer.mapToScene(QPointF(0, composer.height())).y() <= window.height()
            # The menu opens; colour choices live in Settings and are remembered.
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'openMenu'), 'clicked')
            app.processEvents()
            menu = window.findChild(QObject, 'mainMenu')
            assert menu.property('visible')
            if os.environ.get('ANO_MENU_PREVIEW_PATH'):
                QTest.qWait(250)
                assert window.grabWindow().save(os.environ['ANO_MENU_PREVIEW_PATH'])
            QMetaObject.invokeMethod(menu, 'close')
            nebula = window.findChild(QObject, 'theme_nebula')
            assert nebula and QMetaObject.invokeMethod(nebula, 'clicked')
            assert controller.theme == 'nebula'
            assert memory.setting('ui_theme', '') == 'nebula'
            # Permission modes are selected by real QML Settings choices; each choice starts a fresh session.
            desktop_control._available = True
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'fullControlMode'), 'clicked')
            assert desktop_control.enabled and desktop_control.permissionMode == 'full'
            assert 'Full permission' in desktop_control.status
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'freeRoamControlMode'), 'clicked')
            assert desktop_control.freeRoam and desktop_control.backend.allow_terminals
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'reviewedControlMode'), 'clicked')
            assert desktop_control.enabled and desktop_control.permissionMode == 'reviewed'
            desktop_control.stop()
            assert not desktop_control.freeRoam and not desktop_control.backend.allow_terminals
            # Vault entry uses only the in-memory fake backend, never Windows credentials.
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'openPasswords'), 'triggered')
            app.processEvents()
            vault_popup = window.findChild(QObject, 'vaultPopup')
            assert vault_popup.property('visible') and saved_logins.private
            for name, value in [('loginLabel', 'Synthetic login'), ('loginTarget', 'https://example.invalid'),
                                ('loginUser', 'dummy-user'), ('loginPassword', 'dummy-test-secret')]:
                window.findChild(QObject, name).setProperty('text', value)
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'saveLogin'), 'clicked')
            assert len(saved_logins.entries) == 1
            assert window.findChild(QObject, 'loginPassword').property('text') == ''
            if os.environ.get('ANO_VAULT_PREVIEW_PATH'):
                assert window.grabWindow().save(os.environ['ANO_VAULT_PREVIEW_PATH'])
            QMetaObject.invokeMethod(vault_popup, 'close')
            app.processEvents()
            assert not saved_logins.private
            reviewed = []
            def request_review():
                reviews.request(kind='desktop_input', title='Synthetic action review', target='Example app',
                                detail='Type the approved example text.', preview='', button='Approve once')
                reviewed.append(True)
            worker = Thread(target=request_review)
            worker.start()
            for _ in range(100):
                app.processEvents()
                time.sleep(.01)
                if reviews.ready: break
            app.processEvents()
            assert reviews.ready
            assert window.findChild(QObject, 'actionReview').property('visible')
            assert QMetaObject.invokeMethod(window.findChild(QObject, 'approveComputerAction'), 'clicked')
            worker.join(1)
            app.processEvents()
            assert reviewed == [True] and not reviews.ready
            # Real rendered responses support both one-click copying and selected plain text.
            sample_reply = 'Copy **this response** with a link: [example](https://example.test)'
            memory.add('assistant', sample_reply)
            controller.search('')
            QTest.qWait(200)
            def visual_items(item):
                yield item
                for child in item.childItems():
                    yield from visual_items(child)
            copy_buttons = [item for item in visual_items(window.contentItem()) if item.objectName() == 'copyResponse']
            copy_button = next(button for button in copy_buttons if button.property('responseText') == sample_reply)
            assert QMetaObject.invokeMethod(copy_button, 'clicked')
            assert app.clipboard().text() == sample_reply
            assert copy_button.property('copied')
            response = next(item for item in visual_items(window.contentItem()) if item.objectName() == 'responseText' and 'Copy' in item.property('text'))
            assert response.property('readOnly') and response.property('selectByMouse')
            assert QMetaObject.invokeMethod(response, 'selectAll')
            assert QMetaObject.invokeMethod(response, 'copy')
            assert 'Copy this response' in app.clipboard().text()
            assert '**' not in app.clipboard().text()
            app.clipboard().setText('Before Ctrl+C')
            response.forceActiveFocus()
            QTest.keyClick(window, Qt.Key_C, Qt.ControlModifier)
            assert 'Copy this response' in app.clipboard().text()
            if os.environ.get('ANO_CHAT_PREVIEW_PATH'):
                ano_theme = os.environ.get('ANO_CHAT_THEME', 'aurora')
                controller.setTheme(ano_theme)
                window.setWidth(380)
                window.setHeight(560)
                memory.add('user', 'What is on my calendar tomorrow?')
                memory.add('assistant', 'You have two things tomorrow:\n• 10:00–10:30 Example standup\n• 14:30 Dentist\n\n· Checked your calendar for Mon 26 Oct')
                memory.add('user', 'Remind me at 2 to leave')
                memory.add('assistant', 'Done. I will remind you at 14:00 tomorrow.\n\n· Reminder set for Mon 26 Oct 14:00')
                controller.search('')
                QTest.qWait(300)
                assert window.grabWindow().save(os.environ['ANO_CHAT_PREVIEW_PATH'])
        except Exception as error:
            import traceback
            traceback.print_exc()
            failures.append(error)
        finally:
            app.quit()

    # Let QML bindings and the software renderer run before and after mutations.
    QTimer.singleShot(200, verify)
    app.exec()
    app.processEvents()
    voice.close()
    speech.close()
    outbox.close()
    fixes.close()
    window.hide()
    engine.deleteLater()
    app.processEvents()
    memory.close()
errors = [message for message in diagnostics if any(
    text in message for text in ('Error', 'failed', 'Unable', 'ReferenceError', 'TypeError'))]
assert not errors, errors
assert not failures, failures
print('QML chat, screen, activity, files, voice, compact layout, vault save/clear, and action approval passed.')
for message in diagnostics:
    print(message)
