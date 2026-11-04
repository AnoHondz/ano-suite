"""Background tasks: "in the background: …" runs a task in its own chat while the main chat stays free.

Up to MAX_RUNNING run at once, each on its own thread named ano-background-N. The toolbox refuses screen,
keyboard and browser-task tools on those threads (tools.Toolbox.run), so background work never fights the main
chat for the mouse. All chat records are written on the main thread.
"""
import itertools
import re
import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot

MAX_RUNNING = 2
PATTERNS = [r'(?:in the background|background task|in background)[:,]?\s+(?P<task>.{3,2000})',
            r'(?:do|run) (?:this|that) in the background[:,]?\s+(?P<task>.{3,2000})',
            r'(?P<task>.{3,2000}?),? in the background']


class BackgroundTasks(QObject):
    _done = Signal(int, int, str, str, str)  # chat, request id, task, reply, error

    def __init__(self, controller, memory, provider):
        super().__init__()
        self.controller, self.memory, self.provider = controller, memory, provider
        self.lock = threading.Lock()
        self.requested, self.running = [], {}  # running: chat -> task
        self.numbers = itertools.count(1)
        self._done.connect(self._finished)
        controller.replyFinished.connect(self._reply_finished)

    # -------------------------------------------------- command (called from LocalRoutines on a worker thread)
    def command(self, text, local=False):
        """local: the user typed "local:" on the OpenAI brain, so the task uses the free brain."""
        clean = ' '.join(text.split()).rstrip('.')
        key = clean.lower()
        if key in ('background tasks', 'show background tasks', 'what is running in the background',
                   "what's running in the background", 'what is in the background'):
            with self.lock:
                tasks = list(self.running.values()) + self.requested
            return ('Running in the background:\n' + '\n'.join(f'· {task}' for task in tasks)) if tasks else 'Nothing is running in the background.'
        for pattern in PATTERNS:
            if match := re.fullmatch(pattern, clean, re.I | re.S):
                task = match['task'].strip(' :,')
                with self.lock:
                    if len(self.running) + len(self.requested) >= MAX_RUNNING:
                        return (f'{MAX_RUNNING} background tasks are already running. Wait for one to finish '
                                '(say “background tasks” to see them).')
                    self.requested.append(('local: ' if local else '') + task)
                return (f'Started in the background: “{task}”. Keep chatting here; the result goes into its own chat and '
                        'I will tell you when it is done. Background tasks cannot use the screen or browser tasks.')
        return None

    # -------------------------------------------------- main thread
    @Slot(str, bool)
    def _reply_finished(self, reply, failed):
        if self.requested:
            QTimer.singleShot(300, self._start_requested)

    def _start_requested(self):
        with self.lock:
            tasks, self.requested = self.requested, []
        for task in tasks:
            chat = self.memory.new_chat()
            request = self.memory.add('user', task, chat)
            history = self.memory.history_for_request(request, chat)
            with self.lock:
                self.running[chat] = task
            threading.Thread(target=self._work, args=(chat, request, task, history),
                             name=f'ano-background-{next(self.numbers)}', daemon=True).start()
        self.controller.chatsChanged.emit()

    def _work(self, chat, request, task, history):
        try:
            reply = self.provider.reply(history)
            self._done.emit(chat, request, task, reply or '', '')
        except Exception as error:  # the provider's own messages are user-safe (ClaudeError); others are hidden
            message = str(error) if type(error).__name__ == 'ClaudeError' else 'The AI request failed.'
            self._done.emit(chat, request, task, '', message)

    @Slot(int, int, str, str, str)
    def _finished(self, chat, request, task, reply, error):
        with self.lock:
            self.running.pop(chat, None)
        self.memory.add('assistant', reply if not error else 'Background task stopped without verified completion.\n\n' + error,
                        chat, reply_to=request)
        self.controller.chatsChanged.emit()
        task = re.sub(r'^local:\s*', '', task, flags=re.I)
        short = task if len(task) <= 60 else task[:57] + '…'
        self.controller.add_message('assistant', (f'Background task {"finished" if not error else "stopped"}: “{short}”. '
                                                  'Its result is in its own chat in the Chats list.'))
