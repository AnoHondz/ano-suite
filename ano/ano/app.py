import os
import re
import ctypes
from datetime import datetime, timedelta, timezone
import json
import sys
import sqlite3
from pathlib import Path
import threading
import webbrowser

from PySide6.QtCore import QObject, Property, Signal, Slot, QStandardPaths, QUrl, QRunnable, QThreadPool, QTimer, QPoint, QProcess
from PySide6.QtGui import QAction, QFontDatabase, QIcon, QWindow, QCursor
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QMessageBox

from .memory import Memory
from .paths import data_directory
from .shortcut import GlobalShortcut
from .claude import ClaudeError, load_rules
from .credentials import load_gmail, load_calendar, load_browser_token
from .screen import ScreenCapture, PreviewProvider
from .activity import ActivityTracker, is_activity_question
from .windows_activity import WindowsActivity, SessionEvents
from .voice import OfflineRecognizer, VoiceService
from .voice_setup import MODEL_NAME
from .speech import SapiSpeaker, SpeechService
from .tools import FileAccess, OutboundReview, Toolbox, local_command
from . import autostart
from .plugins.apps import Apps
from .plugins.mail import Gmail, Outbox
from .plugins.media import Media
from .plugins.notes import Notebook
from .plugins.schedule import GoogleCalendar
from .plugins.system import Fixes, System
from .plugins.browser import Browser, BrowserBridge, BrowserHealth
from .browser_tasks import BrowserTasks
from .browser_forms import BrowserForms
from .single_instance import SingleInstance
from .plugins.windows import Windows
from .plugins.local_images import LocalImages
from .plugins.startup import Startup
from .plugins.scorpred import ScorPred
from .plugins.video import Video
from .plugins.websearch import WebSearch
from .plugins.web import Web
from .plugins.workshop import Workshop
from .plugins.word_documents import WordDocuments
from .plugins.artifacts import Artifacts
from .plugins.paper_trading import PaperTrading
from .action_log import ActionLog
from .plugins.local_status import LocalStatus
from .brain import AnoBrain, LocalConversationRouter
from .routines import LocalRoutines, RoutineBrain, RoutineStore, SITES
from .scheduled import ScheduledActions, TARGETS as SCHEDULE_TARGETS
from .codex_watch import CodexWatch
from .monitor import Monitors, MonitorWorker, safe_to_open
from .handle import HandleRequests
from .gpt import Brains, GPTClient, LOCAL_URL
from .model_policy import ModelPolicy, is_loopback_url
from .local_runtime import local_writer_config, installed_models
from .model_roles import select_model_roles
from .jobs import JobStore
from .work_ledger import WorkLedger
from .facts import FactStore, FactMemoryBrain, FactSearch
from .voice_conversation import VoiceConversation
from .review import Reviews
from .desktop import DesktopControl
from .desktop_native import WindowsDesktop
from .login_vault import LoginStore, LoginVault

APP_VERSION = '0.1.10'

class ReplySignals(QObject):
    completed = Signal(str, str)
    progress = Signal(str)
    partial = Signal(str)


class ReplyJob(QRunnable):
    def __init__(self, provider, history, screenshot=None, activity_context=None):
        super().__init__()
        self.provider = provider
        self.history = history
        self.screenshot = screenshot
        self.activity_context = activity_context
        self.signals = ReplySignals()

    def run(self):
        try:
            kwargs = {}
            if getattr(self.provider, 'supports_progress', False) is True:
                kwargs['progress'] = self.signals.progress.emit
            if getattr(self.provider, 'supports_partial', False) is True:
                from .streaming import PartialText
                kwargs['partial'] = PartialText(self.signals.partial.emit)
            if self.screenshot is not None:
                kwargs['screenshot'] = self.screenshot
            if self.activity_context is not None:
                kwargs['activity_context'] = self.activity_context
            reply = self.provider.reply(self.history, **kwargs)
            self.signals.completed.emit(reply, '')
        except ClaudeError as error:
            self.signals.completed.emit('', str(error))
        except Exception:
            # Do not expose SDK exceptions, headers, or credentials in UI/logs.
            self.signals.completed.emit('', 'The AI request failed. Please try again.')
        finally:
            self.screenshot = None


class Controller(QObject):
    quitRequested = Signal()
    messagesChanged = Signal()
    pausedChanged = Signal()
    statusChanged = Signal()
    busyChanged = Signal()
    appearanceChanged = Signal()
    chatsChanged = Signal()
    expressionChanged = Signal()
    jobsChanged = Signal()
    partialReply = Signal(int, str)
    taskProgressChanged = Signal()

    @Property('QVariantMap', notify=taskProgressChanged)
    def taskProgress(self):
        return self.browser_tasks.progress_snapshot() if self.browser_tasks else {}

    def _receive_partial(self, text, chat, request):
        if (self._busy and not self._discard and not self._paused and not self._active_cancelled
                and chat == self._reply_chat and request == self._reply_request
                and not (self.login_vault and self.login_vault.private)):
            self.partialReply.emit(chat, text)
    savingsChanged = Signal()

    @Property(str, notify=savingsChanged)
    def savingsSummary(self):
        ledger = getattr(self.provider, 'ledger', None)
        if ledger is None or not callable(getattr(ledger, 'savings_summary', None)):
            return 'No route statistics recorded yet'
        try:
            return ledger.savings_summary()
        except Exception:
            return 'Route statistics unavailable'
    voiceSessionChanged = Signal()
    replyFinished = Signal(str, bool)
    alive = Signal(str, str)  # (kind, message) from the monitor; the QML makes Ano fly with the message
    watchStatusChanged = Signal()

    def __init__(self, memory, provider=None, jobs=None):
        super().__init__()
        self.memory = memory
        self.jobs = jobs
        self._active_job = None
        self.last_completed_job = None
        self.last_enqueued_job = None
        self._reply_request = None
        self._active_cancelled = False
        self._queued_payloads = {}
        self._theme = memory.setting('ui_theme', 'aurora')
        if self._theme not in ('aurora', 'nebula', 'solar'):
            self._theme = 'aurora'
        self._chat = memory.latest_chat()
        self._reply_chat = None
        self._messages = memory.recent(chat=self._chat)
        self._paused = False
        self.provider = provider
        self._busy = False
        self._needs_clarification = False
        self._discard = False
        self._query = ''
        self._job = None
        self.screen_capture = None
        self.activity = None
        self.voice = None
        self.voice_conversation = None
        self.speech = None
        self.desktop_control = None
        self.browser_tasks = None
        self.login_vault = None
        self.pool = QThreadPool(self)
        self._status = 'Ano ready · Can research online, read your chosen folders, and open websites' if provider else 'Local preview · AI is not connected'
        self._monitor_status = ''
        self._codex_watch = ''
        self._watch_items = []
        self._monitoring_paused = False
        self._spending = ''
        self.handles = HandleRequests(lambda: memory.setting('handle_requests', None),
                                      lambda state: memory.set_setting('handle_requests', state))
        self._inventory, self._inventory_loading = {}, False
        self.challenge_summary = lambda: []
        self._inventoryRead.connect(self._set_inventory)

    challengesChanged = Signal()

    @Property('QVariantList', notify=challengesChanged)
    def challengeSummary(self):
        """Newest challenge result per area, for Settings → Challenge results ([] until the runner is wired)."""
        try:
            return self.challenge_summary()
        except Exception:
            return []

    @Slot()
    def refreshChallenges(self):
        self.challengesChanged.emit()

    inventoryChanged = Signal()
    _inventoryRead = Signal(object)  # from the reading thread; Qt delivers it on the main thread

    @Property('QVariantMap', notify=inventoryChanged)
    def inventory(self):
        """{apps, running, extensions: [{browser, name}], plugins, loading} for Settings → Apps & plugins."""
        return dict(self._inventory, loading=self._inventory_loading)

    @Slot()
    def refreshInventory(self):
        if self._inventory_loading:
            return
        self._inventory_loading = True
        self.inventoryChanged.emit()
        from .inventory import inventory
        threading.Thread(target=lambda: self._inventoryRead.emit(inventory()), name='ano-inventory', daemon=True).start()

    def _set_inventory(self, found):
        self._inventory, self._inventory_loading = found, False
        self.inventoryChanged.emit()

    handlesChanged = Signal()

    @Property('QVariantMap', notify=handlesChanged)
    def handleStates(self):
        """'Handle it' requests by number: 'pending', 'approved' or 'dismissed' (missing = expired)."""
        return {str(number): state for number, state in self.handles.states.items()}

    def request_handle(self, note, rule_id):
        number = self.handles.add(note, rule_id)
        self.handlesChanged.emit()
        return number

    @Slot(int)
    def approveHandle(self, number):
        """The user's tap: only now does the notification go to Ano, fenced off as untrusted data."""
        prompt = self.handles.approve(number)
        self.handlesChanged.emit()
        if prompt:
            self.send(prompt)

    @Slot(int)
    def dismissHandle(self, number):
        self.handles.dismiss(number)
        self.handlesChanged.emit()

    @Property('QVariantList', notify=watchStatusChanged)
    def watchItems(self):
        """Everything Ano is watching or waiting to do, for the Watching panel."""
        return self._watch_items

    @Property(bool, notify=watchStatusChanged)
    def monitoringPaused(self):
        return self._monitoring_paused

    @Property(str, notify=watchStatusChanged)
    def spending(self):
        """e.g. 'Today $0.04 · total $0.83 of the $20.00 cap' ('' before any paid use)."""
        return self._spending

    def set_watch_details(self, items, paused, spending):
        if (items, paused, spending) != (self._watch_items, self._monitoring_paused, self._spending):
            self._watch_items, self._monitoring_paused, self._spending = items, paused, spending
            self.watchStatusChanged.emit()

    @Property(str, notify=watchStatusChanged)
    def monitorStatus(self):
        """'' when no monitor is active, e.g. '2 monitors · Claude: working'."""
        return self._monitor_status

    @Property(str, notify=watchStatusChanged)
    def codexWatch(self):
        """'' when the Codex watch is off, e.g. 'limited · resumes 3:21 PM'."""
        return self._codex_watch

    def set_watch_status(self, monitor=None, codex=None):
        changed = False
        if monitor is not None and monitor != self._monitor_status:
            self._monitor_status, changed = monitor, True
        if codex is not None and codex != self._codex_watch:
            self._codex_watch, changed = codex, True
        if changed:
            self.watchStatusChanged.emit()

    @Property(bool, constant=True)
    def connected(self):
        return self.provider is not None

    @Property(str, constant=True)
    def appVersion(self):
        return APP_VERSION

    @property
    def work_allowed(self):
        return not self._paused and not self._active_cancelled

    @Property(bool, notify=voiceSessionChanged)
    def voiceConversationAvailable(self):
        return bool(self.voice_conversation and self.voice_conversation.available)

    @Property(bool, notify=voiceSessionChanged)
    def voiceConversationActive(self):
        return bool(self.voice_conversation and self.voice_conversation.active)

    @Property(str, notify=voiceSessionChanged)
    def voiceConversationStatus(self):
        return self.voice_conversation.status if self.voice_conversation else ''

    @Slot()
    def toggleVoiceConversation(self):
        if self.voice_conversation:
            if self.voice_conversation.active:
                self.voice_conversation.stop()
            else:
                self.voice_conversation.start()

    @Slot()
    def stopVoiceConversation(self):
        if self.voice_conversation:
            self.voice_conversation.stop()

    @Slot()
    def interruptVoiceConversation(self):
        if self.voice_conversation:
            self.voice_conversation.interrupt()

    @Property(bool, constant=True)
    def queueEnabled(self):
        return self.jobs is not None

    @Property(int, notify=jobsChanged)
    def queuedCount(self):
        return sum(j['state'] == 'queued' for j in self.jobs.list_jobs()) if self.jobs else 0

    @Property('QVariantList', notify=jobsChanged)
    def taskItems(self):
        items = list(reversed(self.jobs.list_jobs()))[:100] if self.jobs else []
        for item in items:
            if item['state'] == 'paused':
                item.update(self.jobs.recovery_options(item['id']))
        return items

    @Slot(str)
    def resumeTask(self, identifier):
        if not self.jobs or self._paused or not self.provider:
            return
        try:
            task = self.jobs.get(identifier)
            history = self.memory.history_for_request(task['request_message_id'], task['chat_id'])
            if not history or history[-1]['body'].startswith('[Approved screen snapshot]'):
                self.setStatus('Capture a fresh screen and issue a new command for this task')
                return
            self.jobs.resume_without_changes(identifier)
        except (ValueError, KeyError) as error:
            self.setStatus(str(error))
            return
        self.jobsChanged.emit()
        self._dispatch_next()

    @Slot()
    def stopTask(self):
        if not self._busy:
            return
        if self._reply_chat is not None:
            self.partialReply.emit(self._reply_chat, '')
        self._active_cancelled = True
        self._discard = True
        if self.jobs and self._active_job:
            self.jobs.request_cancel(self._active_job)
            self.jobsChanged.emit()
        if self.desktop_control:
            self.desktop_control.stop()
        if self.speech:
            self.speech.stop()
        self.setStatus('Stopping task · An already dispatched action cannot be recalled')

    @Slot(str)
    def cancelTask(self, identifier):
        if not self.jobs:
            return
        if identifier == self._active_job:
            self.stopTask()
        else:
            self.jobs.request_cancel(identifier)
            self._queued_payloads.pop(identifier, None)
            self.jobsChanged.emit()

    def _dispatch_next(self):
        if not self.jobs or self._busy or self._paused or not self.provider:
            return
        if self.login_vault and self.login_vault.private:
            self.setStatus('Queued work waits for private login to finish')
            return
        task = self.jobs.claim_next()
        if not task:
            return
        if task['checkpoint'].get('requires_reinspection'):
            self.jobs.pause(task['id'])
            self.jobsChanged.emit()
            self.setStatus('Saved task needs destination inspection before resuming')
            if self.queuedCount:
                QTimer.singleShot(0, self._dispatch_next)
            return
        self._active_job = task['id']
        self._reply_chat = task['chat_id']
        self._reply_request = task['request_message_id']
        self._busy = True
        self._discard = self._active_cancelled = False
        screenshot, activity = self._queued_payloads.pop(task['id'], (None, None))
        history = self.memory.history_for_request(self._reply_request, self._reply_chat)
        history[-1].update(source_chat_id=self._reply_chat, source_message_id=self._reply_request)
        self._job = ReplyJob(self.provider, history, screenshot, activity)
        self._job.signals.completed.connect(self._receive)
        self._job.signals.progress.connect(self._reply_progress)
        self._job.signals.partial.connect(lambda text, chat=self._reply_chat, request=self._reply_request:
                                         self._receive_partial(text, chat, request))
        self.busyChanged.emit()
        self.jobsChanged.emit()
        self.setStatus('Ano is working…')
        self.pool.start(self._job)

    @Property(str, notify=appearanceChanged)
    def theme(self):
        return self._theme

    @Property('QVariantList', constant=True)
    def brains(self):
        return list(getattr(self.provider, 'modes', []))

    @Property(str, notify=appearanceChanged)
    def brain(self):
        return getattr(self.provider, 'mode', '')

    @Slot(str)
    def setBrain(self, mode):
        if mode in self.brains:
            self.provider.mode = mode
            self.memory.set_setting('brain', mode)
            self.appearanceChanged.emit()

    @Slot(str)
    def setTheme(self, theme):
        if theme in ('aurora', 'nebula', 'solar'):
            self._theme = theme
            self.memory.set_setting('ui_theme', theme)
            self.appearanceChanged.emit()

    @Property(bool, notify=appearanceChanged)
    def floating(self):
        return bool(self.memory.setting('floating', False))

    @Slot(bool)
    def setFloating(self, value):
        self.memory.set_setting('floating', bool(value))
        self.appearanceChanged.emit()

    @Property(int, constant=True)
    def bubbleX(self):
        return int(self.memory.setting('bubble_x', -1))

    @Property(int, constant=True)
    def bubbleY(self):
        return int(self.memory.setting('bubble_y', -1))

    @Slot(int, int)
    def saveBubble(self, x, y):
        self.memory.set_setting('bubble_x', int(x))
        self.memory.set_setting('bubble_y', int(y))

    @Slot(int, int)
    def savePanelPosition(self, x, y):
        self.memory.set_setting('panel_position', {'x': int(x), 'y': int(y)})

    @Slot(int, int, result='QVariantMap')
    def floatingPosition(self, width, height):
        """Place only after showing; hidden QML windows can have no screen geometry yet."""
        saved_x = self.memory.setting('bubble_x', None)
        saved_y = self.memory.setting('bubble_y', None)
        has_saved = type(saved_x) is int and type(saved_y) is int
        point = QPoint(saved_x, saved_y) if has_saved else QPoint()
        saved_screen = next((s for s in QApplication.screens() if has_saved and s.availableGeometry().contains(point)), None)
        screen = saved_screen or QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return {'x': 24, 'y': 120}
        area = screen.availableGeometry()
        x = point.x() if saved_screen else area.right() - width - 24
        y = point.y() if saved_screen else area.center().y() - height // 2
        return {'x': max(area.left(), min(x, area.right() - width + 1)),
                'y': max(area.top(), min(y, area.bottom() - height + 1))}

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(bool, notify=expressionChanged)
    def needsClarification(self):
        return self._needs_clarification

    def _set_clarification(self, value):
        if self._needs_clarification != value:
            self._needs_clarification = value
            self.expressionChanged.emit()

    @Property('QVariantList', notify=chatsChanged)
    def chats(self):
        return self.memory.chats()

    @Property(int, notify=chatsChanged)
    def currentChat(self):
        return self._chat

    @Property(str, notify=chatsChanged)
    def chatTitle(self):
        return next((chat['title'] for chat in self.memory.chats() if chat['id'] == self._chat), 'New chat')

    @Slot()
    def newChat(self):
        self._set_clarification(False)
        # An empty open chat is reused rather than piling up blank ones.
        if self.memory.recent(1, chat=self._chat):
            self._chat = self.memory.new_chat()
        self.chatsChanged.emit()
        self.search('')

    @Slot(int)
    def openChat(self, chat):
        self._set_clarification(False)
        if any(item['id'] == chat for item in self.memory.chats()):
            self._chat = chat
            self.chatsChanged.emit()
            self.search('')

    @Slot(int)
    def deleteChat(self, chat):
        if self._busy and chat == self._reply_chat:
            return
        if self.jobs:
            for task in self.jobs.list_jobs():
                if task['chat_id'] == chat and task['state'] in ('queued', 'paused'):
                    self.cancelTask(task['id'])
        self.memory.delete_chat(chat)
        if chat == self._chat:
            self._chat = self.memory.latest_chat()
        self.chatsChanged.emit()
        self.search('')

    def add_message(self, role, body):
        """Add a message from outside a reply (reminders, fix results) to the open chat."""
        self.memory.add(role, body, self._chat)
        self.chatsChanged.emit()
        self.search(self._query)

    @Property('QVariantList', notify=messagesChanged)
    def messages(self):
        return self._messages

    @Property(bool, notify=pausedChanged)
    def paused(self):
        return self._paused

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Slot()
    def togglePause(self):
        self._paused = not self._paused
        if self._paused and self._reply_chat is not None:
            self.partialReply.emit(self._reply_chat, '')
        if self.activity:
            self.activity.set_paused(self._paused or bool(self.login_vault and self.login_vault.private))
        if self._paused:
            self.stopVoiceConversation()
            if self.desktop_control:
                self.desktop_control.stop()
            if self.voice:
                self.voice.cancel()
            if self.speech:
                self.speech.stop()
        if self._paused and self.screen_capture:
            self.screen_capture.cancel()
        if self._paused and self._busy:
            self._active_cancelled = True
            self._discard = True
            self._status = 'Paused · The request already sent cannot be recalled; its reply will be discarded'
            self.statusChanged.emit()
        self.pausedChanged.emit()
        if not self._paused and self.jobs and self.queuedCount:
            QTimer.singleShot(0, self._dispatch_next)

    @Slot()
    def requestQuit(self):
        if not self._paused:
            self.togglePause()
        self.quitRequested.emit()

    @Slot(str)
    def send(self, body):
        if self.screen_capture and (self.screen_capture.capturing or self.screen_capture.ready):
            return
        self._send(body)

    @Slot(str)
    def approveScreen(self, question):
        if self._paused or self._busy or not self.provider or not self.screen_capture:
            return
        screenshot = self.screen_capture.consume()
        if screenshot is None:
            return
        self._send(question.strip() or 'Ano, what am I looking at?', screenshot)

    @Slot(str)
    def screenError(self, error):
        self.setStatus(error)

    @Slot(str)
    def setStatus(self, status):
        self._status = status
        self.statusChanged.emit()

    @Slot()
    def recallActivity(self):
        self.send('What was I working on earlier?')

    @Property(bool, notify=busyChanged)
    def websiteTaskPending(self):
        task = getattr(self.browser_tasks, 'task', None)
        return bool(task and task['status'] in ('blocked', 'partly_completed')
                    and task.get('resumable') is not False and not task.get('pending_dispatch'))

    @Slot()
    def continueWebsiteTask(self):
        """Only the local UI calls this: the button explicitly grants full control."""
        if self._busy or self._paused or not self.websiteTaskPending or not self.login_vault or not self.login_vault.hold:
            return
        self.login_vault.done()
        if self.login_vault.private or not self.desktop_control:
            return
        self.desktop_control.startSession('full')
        if not self.desktop_control.permitted():
            self.setStatus('Control could not start. Unlock and resume Ano, then try again.')
            return
        task_id = self.browser_tasks.task['id']
        self.send(f'Continue my saved browser task {task_id}. I finished the private login and chose full control. '
                  'Resume the saved progress, inspect the current page, and verify authentication before continuing. '
                  'Do not replay a previously dispatched submission.')

    def _send(self, body, screenshot=None):
        body = body.strip()
        if body.casefold() in ('stop current task', 'cancel current task'):
            self.stopTask()
            return
        if self._paused or (self._busy and (not self.jobs or screenshot)) or (self.voice and self.voice.busy) or not body:
            return
        if self.jobs and any(j['goal'] == body and j['chat_id'] == self._chat and
                             j['state'] in ('queued', 'running', 'cancelling') for j in self.jobs.list_jobs()):
            self.setStatus('That command is already running or queued')
            return
        self._set_clarification(False)
        activity_context = local_recall = None
        if self.activity and is_activity_question(body):
            activity_context, local_recall = self.activity.recall(body)
        request = self.memory.add('user', ('[Approved screen snapshot] ' if screenshot else '') + body, self._chat)
        self.chatsChanged.emit()
        self.search(self._query)
        if self.provider and self.jobs:
            identifier = self.jobs.enqueue(self.jobs.project_for_chat(self._chat), body, self._chat, request,
                                           requires_context=screenshot is not None or activity_context is not None)
            self.last_enqueued_job = identifier
            self._queued_payloads[identifier] = (screenshot, activity_context)
            self.jobsChanged.emit()
            if self._busy:
                self.setStatus(f'Command queued · {self.queuedCount} waiting')
            else:
                self._dispatch_next()
            return
        if self.provider:
            self._reply_request = request
            self._active_cancelled = False
            self._busy = True
            self._discard = False
            self._reply_chat = self._chat
            self.busyChanged.emit()
            self._status = 'Ano is thinking…'
            self._job = ReplyJob(self.provider, self.memory.recent(20, chat=self._chat), screenshot, activity_context)
            self._job.signals.completed.connect(self._receive)
            self._job.signals.progress.connect(self._reply_progress)
            self._job.signals.partial.connect(lambda text, chat=self._reply_chat, request=self._reply_request:
                                             self._receive_partial(text, chat, request))
            self.pool.start(self._job)
        else:
            if local_recall:
                self.memory.add('assistant', local_recall, self._chat)
                self.search(self._query)
                if self.speech:
                    self.speech.speak(local_recall)
            self._status = 'Saved on this device · Run python -m ano.configure local to connect your local brain'
        self.statusChanged.emit()

    @Slot(str)
    def _reply_progress(self, text):
        if self._busy and not self._discard:
            self.setStatus(text)

    @Slot(str, str)
    def _receive(self, reply, error):
        if self._reply_chat is not None:
            self.partialReply.emit(self._reply_chat, '')
        self.savingsChanged.emit()
        self._busy = False
        self._job = None
        self.busyChanged.emit()
        if self._discard:
            self._status = 'Reply discarded · Inspect any already dispatched actions before retrying'
            if self.jobs and self._active_job:
                self.memory.add('assistant', self._status, self._reply_chat, reply_to=self._reply_request)
                self.chatsChanged.emit()
                self.search(self._query)
        elif error:
            self._status = error
            # Keep a failed command visibly attached to its original chat. A
            # transient status bar alone left the last user command unanswered
            # and made stale earlier replies look like its result.
            self.memory.add('assistant', 'Task stopped without verified completion.\n\n' + error,
                            self._reply_chat, reply_to=self._reply_request)
            self.chatsChanged.emit()
            self.search(self._query)
        else:
            # The reply belongs to the chat it was asked in, even if another chat is open now.
            # A limited UI cue for explicit clarification language, not an inferred emotion.
            if self._reply_chat == self._chat:
                self._set_clarification(bool(re.search(
                    r"\b(?:could you clarify|can you clarify|please clarify|could you explain|"
                    r"what do you mean|i (?:don['’]t|do not) understand|"
                    r"i['’]m not sure (?:what|which)|i am not sure (?:what|which))\b",
                    reply, re.IGNORECASE)))
            self.memory.add('assistant', reply, self._reply_chat, reply_to=self._reply_request)
            self.chatsChanged.emit()
            self.search(self._query)
            local_mode = getattr(self.provider, 'mode', '') == 'local'
            openai_mode = getattr(self.provider, 'mode', '') == 'openai'
            self._status = (f'OpenAI {getattr(self.provider, "model", "")} · Type paid usage for spending · Start with local: for free local AI'
                            if openai_mode and getattr(self.provider, 'last_paid', False) is True else
                            'Local AI answered this local: message · Next message uses OpenAI'
                            if openai_mode else
                            'OpenAI task finished · Next message uses local AI · Type paid usage for limits'
                            if getattr(self.provider, 'last_paid', False) is True else
                            'Local AI · No paid model fallback · Conversation saved on this device'
                            if local_mode else 'Ano · Conversation saved on this device')
            if self.speech:
                self.speech.speak(reply)
        if self.jobs and self._active_job:
            state = self.jobs.get(self._active_job)['state']
            if state == 'cancelling':
                self.jobs.finish(self._active_job, 'cancelled')
            elif self._discard:
                self.jobs.pause(self._active_job)
            else:
                self.jobs.finish(self._active_job, 'failed' if error else 'answered')
            self.last_completed_job = self._active_job
            self._active_job = None
            self.jobsChanged.emit()
            if self.queuedCount:
                QTimer.singleShot(0, self._dispatch_next)
        self._active_cancelled = False
        self.statusChanged.emit()
        self.replyFinished.emit(reply, bool(error or self._discard))

    @Slot(str)
    def copyText(self, text):
        """Local UI action only; no clipboard access is exposed to the AI tools."""
        QApplication.clipboard().setText(text)

    @Slot(str)
    def search(self, query):
        self._query = query
        self._messages = self.memory.search(query) if query.strip() else self.memory.recent(chat=self._chat)
        self.messagesChanged.emit()


def make_icon():
    return QIcon(str(Path(__file__).parent / 'assets' / 'ano-ai.ico'))


def load_fonts():
    for font in sorted((Path(__file__).parent / 'assets' / 'fonts').glob('*.ttf')):
        QFontDatabase.addApplicationFont(str(font))


def dock(window, margin=12, position=None):
    """Place the panel in the bottom-right corner of its screen, above the taskbar, sized to fit."""
    area = window.screen().availableGeometry()
    window.resize(min(400, area.width() - 2 * margin), min(600, int(area.height() * 0.70)))
    if isinstance(position, dict) and type(position.get('x')) is int and type(position.get('y')) is int:
        point = QPoint(position['x'], position['y'])
        saved_screen = next((screen for screen in QApplication.screens() if screen.availableGeometry().contains(point)), None)
        if saved_screen:
            area = saved_screen.availableGeometry()
            window.setPosition(QPoint(max(area.left()+margin, min(point.x(), area.right()-window.width()-margin+1)),
                                      max(area.top()+margin, min(point.y(), area.bottom()-window.height()-margin+1))))
            return
    frame = window.frameGeometry()
    window.setFramePosition(QPoint(area.right() - frame.width() - margin + 1,
                                   area.bottom() - frame.height() - margin + 1))


def main():
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Ano.Desktop')
    QQuickStyle.setStyle('Basic')
    app = QApplication(sys.argv)
    app.setOrganizationName('Ano')
    app.setApplicationName('Ano')
    app.setWindowIcon(make_icon())
    load_fonts()
    data = data_directory()
    instance = SingleInstance(data)
    if not instance.acquire():
        return 0
    from .supervisor import resume as resume_supervisor
    resume_supervisor(data)
    try:
        memory = Memory(data / 'memory.sqlite3')
    except sqlite3.DatabaseError:
        QMessageBox.critical(None, 'Ano memory needs repair',
                             'The local database did not pass its integrity check. Ano has stopped to protect your records. '
                             'Keep the database and its -wal/-shm files together and follow the recovery steps in README.md.')
        instance.close()
        return 1
    backup_error = False
    daily_backup = data / 'backups' / f'memory-{datetime.now():%Y-%m-%d}.sqlite3'
    if not daily_backup.exists():
        try:
            memory.backup_to(daily_backup)
        except (OSError, sqlite3.Error):
            backup_error = True
    # Local inference is the default. The separate paid: route loads its key only on explicit opt-in.
    policy_error = ''
    try:
        model_policy = ModelPolicy.load(memory, os.environ)
    except ValueError as error:
        policy_error = str(error)
        model_policy = None
    windows = WindowsActivity()
    activity = ActivityTracker(memory, windows.foreground, windows.unlocked)
    files = FileAccess(memory)
    allowed = lambda: controller.work_allowed and not activity.locked
    reviews = Reviews(allowed)
    desktop_backend = WindowsDesktop()
    desktop_preview = PreviewProvider()
    control = DesktopControl(desktop_backend, reviews, lambda: allowed() and not saved_logins.private, desktop_preview)
    try:
        credential_store = LoginStore()
    except Exception:
        credential_store = None
    saved_logins = LoginVault(credential_store, reviews, desktop_backend, allowed, control.stop)
    outbox = Outbox(allowed)
    fixes = Fixes(allowed)
    notebook = Notebook(data / 'memory.sqlite3')
    try:
        gmail_login, calendar_url, browser_token = load_gmail(), load_calendar(), load_browser_token()
    except Exception:
        gmail_login = calendar_url = browser_token = None
    # Same rule as browser-task navigation: Full permission/Free roam run unattended; otherwise the user reviews.
    outbound = OutboundReview(reviews, automatic=lambda: control.permitted() and control.permissionMode in ('full', 'roam'),
                              homepages=SITES.values())
    web = Web(outbound=outbound)
    workshop_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / 'AnoWorkspace'
    # Research reads only the links its own search returned, through web.page(), so it needs no review.
    workshop = Workshop(workshop_root, web, lambda: allowed() and not saved_logins.private, roots=files.paths)
    plugins = [System(fixes, outbound=outbound), Windows(), Apps(), Media(), notebook, GoogleCalendar(calendar_url, webbrowser.open), Video(), WebSearch(outbound=outbound), web, workshop, control, saved_logins]
    plugins.append(WordDocuments(workshop))
    plugins.append(Artifacts(workshop))
    plugins.append(PaperTrading(workshop))
    from .plugins import outlook
    if outlook.installed():  # classic Outlook: inbox and calendar, read-only, no password needed
        plugins.append(outlook.OutlookDesktop())
    action_log = ActionLog(data / 'action-log.sqlite3')
    plugins.append(action_log)
    plugins.append(LocalStatus(data))
    facts = FactStore(data / 'facts.sqlite3')
    plugins.append(FactSearch(facts))
    bridge = BrowserBridge(browser_token) if browser_token else None
    saved_logins.bridge = bridge
    plugins.append(Browser(bridge, outbound=outbound))
    control_mode = lambda: ('off' if not control.enabled else control.permissionMode)
    browser_health = BrowserHealth(bool(browser_token), bridge, control_mode)
    if bridge:
        browser_tasks = BrowserTasks(data / 'memory.sqlite3', bridge, reviews, control)
        control.stopped.connect(browser_tasks.stop)
        plugins.append(browser_tasks)
        browser_forms = BrowserForms(bridge, control)
        control.stopped.connect(browser_forms.stop)
        plugins.append(browser_forms)
    if gmail_login:
        gmail = Gmail(*gmail_login, outbox)
        outbox.deliver = gmail.deliver
        plugins.append(gmail)
    plugins.append(LocalImages(allowed=lambda: allowed() and not saved_logins.private))
    plugins.append(Startup())
    scorpred_plugin = ScorPred(data / 'scorpred', allowed=lambda: allowed() and not saved_logins.private,
                              authorized=outbound.is_local_command)
    plugins.append(scorpred_plugin)
    from .reliability import Reliability,ReliabilityMonitor
    from .plugins.reliability import ReliabilityTools
    reliability=Reliability(data)
    reliability_monitor=ReliabilityMonitor(reliability,allowed=lambda:allowed() and not saved_logins.private)
    plugins.append(ReliabilityTools(reliability,allowed=lambda:allowed() and not saved_logins.private,authorized=outbound.is_local_command))
    toolbox = Toolbox(files.paths, lambda: allowed() and not saved_logins.private, plugins=plugins,
                      free_roam=lambda: control.freeRoam and control.permitted(), outbound=outbound,
                      audit=action_log.record)
    workshop.reader = toolbox.read_file
    rules = load_rules(data / 'ano_rules.md')  # user-editable standing instructions (CLAUDE.md-style)
    # The local Ollama brain is the only AI provider; no automatic paid fallback.
    local_model = model_policy.model if model_policy else ''
    local_url = model_policy.url if model_policy else LOCAL_URL
    role_models = None
    if local_model:
        try:
            available_models = installed_models()
        except (OSError, ValueError):
            available_models = []
        from .reliability_defaults import apply as apply_reliability_defaults
        apply_reliability_defaults(memory, available_models)
        role_models = select_model_roles(memory, local_model, available_models)
    local = AnoBrain('local', toolbox, model=role_models.agent, rules=rules,
                      base_url=local_url, name=f'Local ({role_models.agent})',
                      vision_model=role_models.vision if role_models.vision != role_models.agent else None,
                      output_tokens=model_policy.output_tokens, profile=model_policy.profile,
                      memory_context=facts.context) if role_models else None
    writer_config = local_writer_config(role_models.writer if role_models else local_model, local_url) if model_policy else None
    if writer_config:
        writer_model, writer_url = writer_config
        draft_client = GPTClient('local', model=writer_model, base_url=writer_url, output_tokens=768, profile='writer',
                                 rules='Write clear prose for the user. Do not claim to browse or perform computer actions. No tools are available in this writing request.')
        workshop.writer = lambda prompt: draft_client.reply([{'role': 'user', 'body': prompt}])
    if local:
        conversation = AnoBrain('local', toolbox, model=role_models.conversation, rules=rules,
                                base_url=local_url, output_tokens=min(256, model_policy.output_tokens), profile='chat', memory_context=facts.context)
        local = LocalConversationRouter(local, conversation)
    models = Brains(local=local, mode='local') if local else None
    # Do not queue a competing generation at startup. The first real request
    # uses its small task-specific tool set and keeps the model resident.
    folder_locations = {'documents': QStandardPaths.StandardLocation.DocumentsLocation,
                        'downloads': QStandardPaths.StandardLocation.DownloadLocation,
                        'desktop': QStandardPaths.StandardLocation.DesktopLocation}
    routine_folders = {name: path for name, location in folder_locations.items()
                       if (path := QStandardPaths.writableLocation(location))}
    scheduled = ScheduledActions(data / 'routines.sqlite3')
    monitors = Monitors(data / 'routines.sqlite3')
    monitor_worker = MonitorWorker(monitors)
    monitor_worker.allowed = lambda: allowed() and not saved_logins.private
    routines = LocalRoutines(toolbox, RoutineStore(data / 'routines.sqlite3'), routine_folders,
                             scheduled=scheduled, monitors=monitors)
    from .ict_watch import IctWatch
    def ict_feed():
        from .credentials import load_paper_credentials
        from .paper_feed import AlpacaPaperFeed
        keys = load_paper_credentials()
        if not keys:
            raise RuntimeError('Alpaca paper keys are not set up: run python -m ano.configure paper')
        return AlpacaPaperFeed(*keys)
    ict_watch = IctWatch(ict_feed, memory.setting, memory.set_setting)
    routines.ict_watch = ict_watch
    from .credentials import load_paper_credentials
    try:
        paper_keys = load_paper_credentials()
    except Exception:
        paper_keys = None
    # The trading bots talk only to the broker APIs, never the desktop, so a locked screen or private mode must
    # not stop them (the user asked that they run regardless). Only the user's explicit Pause stops new entries.
    trading_allowed = lambda: not controller.paused
    if paper_keys:  # gap fill paper execution (rule sheet v1.2); off until the user types "start paper trading the gap fill"
        from .strategy_execution import StrategyExecutor
        from .paper_orders import PaperOrderStore, PaperOrderTransport
        order_store = PaperOrderStore(data / 'paper-orders.sqlite3')
        from .paper_execution import ExecutionWorker
        executor = StrategyExecutor(
            lambda: PaperOrderTransport(*load_paper_credentials(), order_store),
            data / 'paper-execution.sqlite3', Path.home() / 'Documents' / 'AnoWorkspace' / 'Trading' / 'JOURNAL.md',
            allowed=trading_allowed)
        executor.enabled = bool(ict_watch._paper_saved)
        executor.symbols = ict_watch._paper_symbols
        executor.strategy_enabled=ict_watch._strategy_saved
        ict_watch.executor = executor
        # Its own thread: recovery first (no entries until done), then management every 15 s, independent of
        # the chat, the UI and the alert watcher.
        execution_worker = ExecutionWorker(executor, ict_feed)
        execution_worker.start()
    else:
        execution_worker = None
    from .forex_runner import ForexRunner
    from .oanda_practice import credentials as forex_credentials, PAIRS as forex_pairs
    forex_runner = None
    try:
        forex_credentials()
        forex_runner = ForexRunner(data, enabled=bool(memory.setting('forex_paper_enabled', False)),
                                   allowed=trading_allowed)
        routines.forex_runner = forex_runner
        memory.set_setting('forex_requested', {'pairs':list(forex_pairs),
            'preferred_provider':'OANDA v20 practice','state':'adapter_loaded',
            'execution_enabled':forex_runner.enabled})
    except Exception as error:
        # Missing/invalid forex credentials do not interrupt ETF protection.
        forex_runner = None
        from .reliability import atomic_json
        atomic_json(data/'forex-execution.health.json', {'at':datetime.now(timezone.utc).isoformat(),
            'enabled':False,'ready':False,'error':type(error).__name__+': forex adapter startup failed'})
    if ict_watch.symbols or ict_watch.gap_symbols or any(ict_watch.research.values()):  # on before Ano restarted
        ict_watch.start()
    routines.install_defaults()
    brains = RoutineBrain(routines, models)
    from .paid import PaidTasks, SpendLedger, MODEL as OPENAI_MODEL, TOTAL_USD as PAID_TOTAL_CAP
    brains = PaidTasks(brains, toolbox, SpendLedger(data / 'paid-usage.sqlite3'), rules,
                       model=memory.setting('openai_model', OPENAI_MODEL), mode=memory.setting('brain', 'local'))
    spend_ledger = brains.ledger  # kept before the wrapper below, for the spending line and challenge tests
    brains = FactMemoryBrain(facts, brains, allowed=lambda: allowed() and not saved_logins.private)
    jobs = JobStore(data / 'jobs.sqlite3')
    jobs.recover_interrupted(include_queued=True)
    controller = Controller(memory, brains, jobs=jobs)
    toolbox.lifecycle = WorkLedger(jobs, lambda: controller._active_job, output_roots=(workshop_root,))
    from .supervisor import suspend as suspend_supervisor
    controller.quitRequested.connect(lambda: suspend_supervisor(data))
    controller.quitRequested.connect(app.quit)
    if bridge:
        controller.busyChanged.connect(lambda: browser_tasks.turn_finished() if not controller.busy else None)
    controller.activity = activity
    controller.desktop_control = control
    controller.browser_tasks = browser_tasks if bridge else None
    if controller.browser_tasks:
        controller.browser_tasks.progress_changed = controller.taskProgressChanged.emit
    controller.login_vault = saved_logins
    if brains.mode == 'openai':
        controller._status = f'OpenAI {brains.model} brain · Start with local: for free local AI · Type paid usage for spending'
    elif policy_error:
        controller._status = f'Inference configuration blocked: {policy_error}'
    elif model_policy.local_only:
        controller._status = f'Local ({role_models.agent}) · Chat ({role_models.conversation}) · No paid fallback'
        if role_models.warnings:
            controller._status += ' · Model override unavailable; check local AI status'
    elif bridge and not bridge.listening:
        controller._status = 'Browser connection unavailable · Quit other Ano copies from the tray, then restart one copy'
    if backup_error:
        controller._status += ' · Daily local backup could not be created; existing records were not replaced'
    engine = QQmlApplicationEngine()
    preview = PreviewProvider()
    capture = ScreenCapture(lambda: not controller.paused and not controller.busy and not activity.locked and not saved_logins.private
                            and not (controller.voice and controller.voice.busy), preview)
    controller.screen_capture = capture
    recognizer = OfflineRecognizer(Path(os.environ.get('ANO_VOSK_MODEL') or data / 'speech' / MODEL_NAME))
    speaker = SapiSpeaker()
    voice = VoiceService(recognizer, lambda: not controller.paused and not controller.busy
                         and not activity.locked and not saved_logins.private and not capture.ready and not capture.capturing)
    speech = SpeechService(memory, speaker, lambda: not controller.paused and not activity.locked and not saved_logins.private and not voice.busy)
    controller.voice = voice
    controller.speech = speech
    conversation_voice = VoiceConversation(voice, speech, controller,
        lambda: not controller.paused and not activity.locked and not saved_logins.private
        and not capture.ready and not capture.capturing)
    controller.voice_conversation = conversation_voice
    conversation_voice.changed.connect(controller.voiceSessionChanged)
    voice.starting.connect(speech.stop)
    capture.error.connect(controller.screenError)
    engine.addImageProvider('capture', preview)
    engine.addImageProvider('control', desktop_preview)
    engine.rootContext().setContextProperty('ano', controller)
    engine.rootContext().setContextProperty('screenCapture', capture)
    engine.rootContext().setContextProperty('activity', activity)
    engine.rootContext().setContextProperty('voice', voice)
    engine.rootContext().setContextProperty('speech', speech)
    engine.rootContext().setContextProperty('files', files)
    engine.rootContext().setContextProperty('outbox', outbox)
    engine.rootContext().setContextProperty('fixes', fixes)
    engine.rootContext().setContextProperty('reviews', reviews)
    engine.rootContext().setContextProperty('desktopControl', control)
    engine.rootContext().setContextProperty('browserHealth', browser_health)
    engine.rootContext().setContextProperty('savedLogins', saved_logins)
    def private_changed():
        activity.set_paused(controller.paused or saved_logins.private)
        if saved_logins.private:
            controller.stopVoiceConversation()
            capture.cancel()
            voice.cancel()
            speech.stop()
    saved_logins.privateChanged.connect(private_changed)
    outbox.finished.connect(controller.setStatus)
    controller.pausedChanged.connect(lambda: (outbox.discard(), fixes.discard()) if controller.paused else None)

    def fix_finished(title, output):
        # Claude reads the result and explains whether it worked; without Claude it is just shown.
        if controller.provider and not controller.busy and not controller.paused:
            controller.send(f'I approved and ran the fix “{title}”. Output:\n{output}')
        else:
            controller.add_message('assistant', f'Fix “{title}” finished:\n{output}')

    fixes.finished.connect(fix_finished)
    fixes.cancelled.connect(lambda title: controller.setStatus(f'Fix cancelled · “{title}” did not run'))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml' / 'Main.qml')))
    if not engine.rootObjects():
        voice.close()
        speech.close()
        memory.close()
        return 1
    window = engine.rootObjects()[0]
    dock(window, position=memory.setting('panel_position', None))
    capture.window = window
    control.frames.window = window
    session_events = SessionEvents(int(window.winId()), activity.session_changed)
    app.installNativeEventFilter(session_events)
    activity.stateChanged.connect(lambda: capture.cancel() if activity.locked else None)
    activity.stateChanged.connect(lambda: controller.stopVoiceConversation() if activity.locked else None)
    activity.stateChanged.connect(lambda: (voice.cancel(), speech.stop(), outbox.discard(), fixes.discard()) if activity.locked else None)
    activity.stateChanged.connect(lambda: (control.stop(), reviews.cancel()) if activity.locked else None)
    activity.timer.start()
    activity.tick()

    def show():
        if capture.capturing:
            return
        window.showNormal()
        window.raise_()
        window.requestActivate()

    instance.activated.connect(show)
    reviews.changed.connect(lambda: show() if reviews.ready else None)

    def toggle_window():
        if control.enabled:
            control.stop()
        # A minimised window still counts as visible; the shortcut brings it back instead of hiding it.
        if window.isVisible() and window.visibility() != QWindow.Visibility.Minimized:
            window.hide()
        else:
            show()
    shortcut = GlobalShortcut(toggle_window)
    app.installNativeEventFilter(shortcut)
    tray = QSystemTrayIcon(app.windowIcon(), app)
    menu = QMenu()
    open_action = QAction('Open Ano', menu)
    open_action.triggered.connect(show)
    menu.addAction(open_action)
    pause_action = QAction('Pause', menu)
    pause_action.setCheckable(True)
    pause_action.triggered.connect(controller.togglePause)
    controller.pausedChanged.connect(lambda: pause_action.setChecked(controller.paused))
    menu.addAction(pause_action)
    startup_action = QAction('Start with Windows', menu)
    startup_action.setCheckable(True)

    def set_startup(on):
        try:
            autostart.enable() if on else autostart.disable()
        except OSError:
            controller.setStatus('Windows did not allow changing the startup setting.')
        startup_action.setChecked(autostart.enabled())
    startup_action.setChecked(autostart.enabled())
    startup_action.toggled.connect(lambda on: set_startup(on) if on != autostart.enabled() else None)
    menu.addAction(startup_action)
    menu.addSeparator()
    quit_action = QAction('Quit Ano', menu)
    quit_action.triggered.connect(controller.requestQuit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.setToolTip('Ano · Ctrl+Alt+A' if shortcut.registered else 'Ano · Shortcut unavailable')
    tray.activated.connect(lambda reason: show() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    available = tray.isSystemTrayAvailable()
    app.setQuitOnLastWindowClosed(not available)
    window.setProperty('trayAvailable', available)
    window.setProperty('shortcutAvailable', shortcut.registered)
    if available:
        tray.show()
        if '--tray' in sys.argv[1:]:  # started at sign-in: stay in the tray until called
            window.hide()

    def remind():
        for text, due in notebook.take_due():
            late = datetime.now().astimezone() - due > timedelta(minutes=2)
            message = text + (f' (was due {due:%a %d %b %H:%M})' if late else '')
            tray.showMessage('Ano reminder', message, app.windowIcon(), 15000)
            controller.add_message('assistant', 'Reminder: ' + message)
            speech.speak('Reminder: ' + text)
        for action in scheduled.take_due():
            run_scheduled(action)
        tick_codex()

    running = {}  # QProcess objects must outlive this function until they finish
    codex = {'watch': None, 'process': None, 'awake': False}
    helper_python = Path(sys.executable).with_name('python.exe')  # pythonw has no stdout to report through

    def keep_awake(on):
        # The system stays awake for an overnight watch; the display may still turn off.
        if sys.platform == 'win32' and on != codex['awake']:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
            codex['awake'] = on

    def codex_status(watch):
        if watch is None:
            return 'watching · starting'
        if codex.get('found') is False:
            return 'ChatGPT closed'
        if watch.resume_at is not None:
            return 'limited · resumes ' + f'{watch.resume_at:%I:%M %p}'.lstrip('0')
        return 'watching · running'

    def tick_codex():
        if not scheduled.watching('codex'):
            codex['watch'] = None
            keep_awake(False)
            controller.set_watch_status(codex='')
            return
        keep_awake(True)
        now = datetime.now().astimezone()
        watch = codex['watch'] = codex['watch'] or CodexWatch(now)
        controller.set_watch_status(codex=codex_status(watch))
        kind = watch.due(now)
        if kind is None or codex['process'] is not None or controller.paused:
            return
        process = codex['process'] = QProcess(app)

        def finished(code, _status):
            output = bytes(process.readAllStandardOutput()).decode('utf-8', 'replace').strip().splitlines()
            try:
                result = json.loads(output[-1]) if code == 0 and output else {}
            except ValueError:
                result = {}
            done = datetime.now().astimezone()
            if kind == 'check':
                if result:
                    codex['found'] = bool(result.get('found'))
                message = watch.checked(result, done) if result else None
                # A restart forgets what was announced; the saved setting does not, so the same news is said once.
                if message and message == memory.setting('codex_last_announcement', ''):
                    message = None
                elif message:
                    memory.set_setting('codex_last_announcement', message)
                scheduled.note('codex', message or ('Codex is not open.' if result and not result.get('found') else
                                                    'Limited; waiting to resume.' if watch.resume_at else 'Codex is running normally.'))
            else:
                message = watch.resumed(result or {'pressed': False, 'error': 'the helper stopped unexpectedly'}, done)
                scheduled.note('codex', message)
            if message:
                tray.showMessage('Ano · Codex watch', message, app.windowIcon(), 15000)
                controller.add_message('assistant', message)
            controller.set_watch_status(codex=codex_status(watch))
            codex['process'] = None
            process.deleteLater()
        process.finished.connect(finished)
        process.errorOccurred.connect(lambda error: error == QProcess.ProcessError.FailedToStart and finished(1, None))
        process.setWorkingDirectory(str(Path(__file__).parent.parent))
        process.start(str(helper_python if helper_python.exists() else sys.executable), ['-m', 'ano.codex_watch', kind])

    def report_scheduled(action, ok, result):
        scheduled.finish(action['id'], ok, result)
        name = SCHEDULE_TARGETS[action['target']][0]
        message = f'Scheduled action #{action["id"]} ({name}, “{action["text"]}”): {result}'
        tray.showMessage('Ano scheduled action', message, app.windowIcon(), 15000)
        controller.add_message('assistant', message)

    def run_scheduled(action):
        if action['missed']:
            name = SCHEDULE_TARGETS[action['target']][0]
            message = (f'Scheduled action #{action["id"]} ({name}, “{action["text"]}”) was missed: '
                       'Ano was not running at that time, so nothing was typed.')
            tray.showMessage('Ano scheduled action', message, app.windowIcon(), 15000)
            controller.add_message('assistant', message)
            return
        if not allowed():
            report_scheduled(action, False, 'Skipped because Ano was paused or the screen was locked. Nothing was typed.')
            return
        process = QProcess(app)
        running[action['id']] = process

        def finished(code, _status):
            output = bytes(process.readAllStandardOutput()).decode('utf-8', 'replace').strip().splitlines()
            result = output[-1] if output else 'The typing script stopped without a result.'
            report_scheduled(action, code == 0 and result.startswith('OK'), result.removeprefix('OK: ').removeprefix('FAILED: '))
            running.pop(action['id'], None)
            process.deleteLater()
        process.finished.connect(finished)
        process.errorOccurred.connect(lambda error: error == QProcess.ProcessError.FailedToStart and finished(1, None))
        process.start('powershell.exe', ScheduledActions.arguments(action))

    def run_monitor(rule, event):
        """Carry out one rule the user typed, on the main thread, and say exactly what was done."""
        notes = []
        for action in rule['actions']:
            kind = action['kind']
            try:
                if kind == 'fly':
                    controller.alive.emit(event.kind, event.message[:40])
                elif kind == 'speak':
                    speech.speak(action['text'])
                elif kind == 'read':  # the notification's own words, read out; never followed as instructions
                    note = event.payload
                    speech.speak(f'{note.get("app", "")}: {note.get("title", "")}. {note.get("body", "")}'[:300])
                elif kind == 'handle':  # only files a request; Ano acts after the user taps Approve
                    number = controller.request_handle(event.payload, rule['id'])
                    notes.append(f'Handle request #{number}: hold Approve to let Ano deal with it, or Dismiss.')
                elif kind == 'type':
                    scheduled.queue_now(action['target'], action['text'])
                    notes.append(f'Typing “{action["text"]}” in {SCHEDULE_TARGETS[action["target"]][0]} (result follows).')
                elif kind == 'open':
                    with local_command(toolbox):  # the user typed this rule; no model chose it
                        result, _, failed = (toolbox.run('open_website', {'url': action['site']}) if 'site' in action
                                             else toolbox.run('open_app', {'name': action['app']}))
                    notes.append(('Could not open ' if failed else 'Opened ') + action.get('label', ''))
                elif kind == 'open_download':
                    path = event.payload.get('path', '')
                    inside = path and Path(path).resolve().parent == monitor_worker.sensors.downloads.resolve()
                    if inside and safe_to_open(path):
                        os.startfile(path)
                        notes.append(f'Opened {Path(path).name}')
                    else:
                        notes.append(f'Did not open {Path(path).name or "the file"}: programs and scripts need your own click.')
                elif kind == 'codex_watch':
                    scheduled.command('watch codex')
                    notes.append('Codex watch is on; Ano will press Resume goal after the reset.')
            except Exception as error:
                notes.append(f'{kind} failed: {error}')
        message = f'Monitor #{rule["id"]}: {event.message}' + ''.join('\n· ' + note for note in notes)
        if event.kind == 'notification':
            note = event.payload
            message = (f'Monitor #{rule["id"]}: notification from {note.get("app", "an app")}\n'
                       f'**{note.get("title", "")}**' + (f'\n{note["body"]}' if note.get('body') else '')
                       + ''.join('\n· ' + line for line in notes))
        tray.showMessage('Ano monitor', event.message, app.windowIcon(), 10000)
        controller.add_message('assistant', message)

    def tick_monitor():
        if forex_runner:
            if memory.setting('forex_paper_enabled', False) != forex_runner.enabled:
                memory.set_setting('forex_paper_enabled', forex_runner.enabled)
            for alert in forex_runner.poll():
                from .trading_notifications import friendly
                alert = friendly(alert)
                tray.showMessage('Ano · forex demo', alert[:200], app.windowIcon(), 15000)
                controller.add_message('assistant', alert)
        for message in reliability_monitor.poll():
            if memory.setting('trading_session_summary_only',True):continue
            tray.showMessage('Ano · Reliability',message,app.windowIcon(),10000)
            controller.add_message('assistant',message)
        for result in scorpred_plugin.poll():
            controller.add_message('assistant', result)
        for rule, event in monitor_worker.poll():
            run_monitor(rule, event)
        for alert in ict_watch.poll():  # Scanning continues; routine observations stay in status.
            if memory.setting('trading_session_summary_only', True):continue
            tray.showMessage('Ano · Trading research', alert.splitlines()[0], app.windowIcon(), 15000)
            controller.add_message('assistant', alert)
        for alert in (execution_worker.poll() if execution_worker else []):  # critical problems and session summary
            from .trading_notifications import friendly, should_notify
            if memory.setting('trading_session_summary_only', True) and not should_notify(alert):continue
            alert = friendly(alert)
            tray.showMessage('Ano · paper trading', alert[:200], app.windowIcon(), 15000)
            controller.add_message('assistant', alert)
        controller.set_watch_status(monitor=monitors.status(monitor_worker.claude))
        # "live": something is really being checked right now. A rule waiting for ChatGPT to open,
        # or for notifications to resume, is kept but not counted as watching.
        notifications_off = monitors.notifications_paused
        def waiting_on(rule):
            if rule['condition'].startswith('codex') and monitor_worker.codex is None:
                return 'waiting for ChatGPT to open'
            if rule['condition'].startswith('notification') and notifications_off:
                return 'notifications paused'
            return ''
        items = []
        for rule in monitors.active():
            waiting = waiting_on(rule)
            items.append(dict(kind='monitor', id=rule['id'], title=monitors.describe(rule), live=not waiting,
                              detail=('every time' if rule['repeat'] else 'once') + (f' · {waiting}' if waiting else '')))
        items += [dict(kind='scheduled', id=action['id'], title=f'Type “{action["text"]}” in {action["app"]}', live=True,
                       detail=f'{datetime.fromisoformat(action["due"]):%a %I:%M %p}') for action in scheduled.pending()]
        if scheduled.watching('codex'):
            items.append(dict(kind='codex', id=0, title='Codex watch: resume after usage limits',
                              live=codex.get('found') is not False, detail=codex_status(codex['watch'])))
        try:
            today, total = spend_ledger.totals()
            spent = f'Today ${today:.2f} · total ${total:.2f} of the ${PAID_TOTAL_CAP:.2f} cap' if total else ''
        except Exception:
            spent = ''
        controller.set_watch_details(items, monitors.paused, spent)

    reminders = QTimer(app)
    reminders.setInterval(20000)
    reminders.timeout.connect(remind)
    reminders.start()
    remind()
    monitor_worker.start()
    monitor_timer = QTimer(app)
    monitor_timer.setInterval(3000)
    monitor_timer.timeout.connect(tick_monitor)
    monitor_timer.start()
    # "run challenge tests": real tasks through this Controller, each checked against its actual outcome.
    from .challenges import ChallengeRunner
    from .plugins.images import pictures_folder
    challenge_runner = ChallengeRunner(controller, dict(
        workspace=workshop_root, challenge_dir=workshop_root / 'Challenges', notes_db=data / 'memory.sqlite3',
        action_log=data / 'action-log.sqlite3', scheduled=scheduled, monitors=monitors, speech=speech, voice=voice,
        ledger=spend_ledger, pictures=pictures_folder), data / 'challenges')
    routines.challenges = challenge_runner
    controller.challenge_summary = challenge_runner.summary
    controller.challengesChanged.emit()
    from .goals import GoalRunner
    from .goal_verification import GoalVerifier
    routines.goals = GoalRunner(controller, memory, verifier=GoalVerifier(jobs))
    from .background import BackgroundTasks
    routines.background = BackgroundTasks(controller, memory, brains)
    # "fix the soccer email" / "claude: …": a hidden Claude Code session does the work; results are posted here
    from .claude_code import ClaudeJobs
    routines.claude_jobs = ClaudeJobs(data, notify=lambda text: controller.add_message('assistant', text))
    claude_timer = QTimer(app)
    claude_timer.timeout.connect(routines.claude_jobs.safe_poll)
    claude_timer.start(3000)
    from .proactive import ProactiveCare
    care = ProactiveCare(data / 'proactive-health.json',
        allowed=lambda: not controller.paused and not controller.busy and not activity.locked and not saved_logins.private,
        notify=lambda text: controller.add_message('assistant', text))
    routines.care = care
    from .repairs import RepairCare
    repair_care = RepairCare(data / 'repairs.json',scorpred_plugin,
        allowed=lambda: not controller.paused and not controller.busy and not activity.locked and not saved_logins.private,
        notify=lambda text: controller.add_message('assistant',text))
    routines.repairs = repair_care
    from .sports_mail import resume_configured_service
    resume_configured_service(data/'sports-mail')
    reliability_monitor.start()
    from .forecast_watch import ForecastWatch
    forecast_watch=ForecastWatch(data,scorpred_plugin.root,allowed=lambda: allowed() and not saved_logins.private)
    forecast_watch.start()
    from .memory_manager import MemoryManager
    memory_manager=MemoryManager(data,
        (role_models.agent,role_models.conversation,role_models.writer,role_models.vision) if role_models else (),
        chat_models=(role_models.conversation,) if role_models else ())
    memory_manager.start()
    # the safe fixes Ano may run when the wallpaper crawler flags a station (ano/repairs.py CRAWLER_FIXES)
    from .repairs import free_memory,restart_mail
    repair_care.fixes.update(system=lambda: free_memory(memory_manager),mail=lambda: restart_mail(data/'sports-mail'))
    if forex_runner:
        forex_runner.start()
    def ui_heartbeat():
        from .reliability import atomic_json
        try:
            atomic_json(data/'ano-ui.health.json', {'at':datetime.now(timezone.utc).isoformat(),
                'pid':os.getpid(),'paused':controller.paused})
        except OSError:
            pass
    ui_health_timer=QTimer(app)
    ui_health_timer.setInterval(15000)
    ui_health_timer.timeout.connect(ui_heartbeat)
    ui_health_timer.start()
    ui_heartbeat()
    from .supervisor import ensure_running as ensure_supervisor
    def check_supervisor():
        from .wallpaper import ensure_running as ensure_wallpaper
        try:ensure_wallpaper()
        except (OSError,ValueError):pass
        try:ensure_supervisor(data)
        except (OSError,ValueError):pass
    supervisor_timer=QTimer(app)
    supervisor_timer.setInterval(30000)
    supervisor_timer.timeout.connect(check_supervisor)
    supervisor_timer.start()
    QTimer.singleShot(1000,check_supervisor)
    try:
        return app.exec()
    finally:
        memory_manager.close()
        forecast_watch.close()
        if forex_runner:
            forex_runner.close()
        repair_care.close()
        care.close()
        monitor_worker.close()
        ict_watch.close()
        reliability_monitor.close()
        if execution_worker:
            execution_worker.close()
        instance.close()
        controller.stopVoiceConversation()
        control.stop()
        reviews.cancel()
        outbox.close()
        fixes.close()
        browser_health.close()
        if bridge:
            bridge.close()
        voice.close()
        speech.close()
        activity.close()
        session_events.close()
        capture.cancel()
        tray.hide()
        shortcut.close()
        memory.close()
