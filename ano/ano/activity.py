"""Local task intervals. No screenshots, keystrokes, or page contents."""
from datetime import datetime, timedelta, timezone
import json
import re

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds')


def is_activity_question(text):
    text = text.casefold().replace('’', "'")
    return bool(re.search(
        r'\b(what (was|were|have|did)|where did|remind me|summari[sz]e|show (my|me))\b', text)
        and re.search(r'\b(working|work|doing|do|activity|tasks?|apps?|windows?|earlier)\b', text)
        and re.search(r'\b(earlier|before|today|yesterday|recent|recently|activity|history|last)\b', text))


class ActivityTracker(QObject):
    changed = Signal()
    stateChanged = Signal()

    def __init__(self, memory, reader, desktop_unlocked, clock=utcnow):
        super().__init__()
        self.memory = memory
        self.reader = reader
        self.desktop_unlocked = desktop_unlocked
        self.clock = clock
        self._enabled = memory.setting('activity_enabled', True)
        self._paused = False
        self._locked = True  # Unknown desktop state always blocks tracking.
        self._session_blocked = False
        self._failed = False
        self._current = None
        self._identifier = None
        self._last = None
        self._flushed = None
        self._suppressed = None
        self._query = ''
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.tick)

    @Property(bool, notify=stateChanged)
    def enabled(self):
        return self._enabled

    @Property(bool, notify=stateChanged)
    def locked(self):
        return self._locked

    @Property(str, notify=stateChanged)
    def status(self):
        if not self._enabled:
            return 'Activity tracking off'
        if self._paused:
            return 'Activity tracking paused'
        if self._locked:
            return 'Activity stopped · Windows locked or desktop unavailable'
        if self._failed:
            return 'Activity tracking unavailable · Retrying'
        return 'Activity tracking on · App and title only'

    @Property('QVariantList', notify=changed)
    def entries(self):
        result = self.memory.activities(self._query)
        for row in result:
            start = datetime.fromisoformat(row['started_at'])
            end = datetime.fromisoformat(row['ended_at'])
            seconds = max(0, int((end - start).total_seconds()))
            row['when'] = start.astimezone().strftime('%a %b %d, %I:%M %p')
            row['duration'] = f'{seconds // 60}m {seconds % 60}s'
        return result

    @Slot(bool)
    def setEnabled(self, enabled):
        self._enabled = enabled
        self.memory.set_setting('activity_enabled', enabled)
        if not enabled:
            self._finish()
        self.stateChanged.emit()

    def set_paused(self, paused):
        self._paused = paused
        if paused:
            self._finish()
        self.stateChanged.emit()

    def session_changed(self, locked):
        self._session_blocked = locked
        self._locked = locked
        if locked:
            self._finish()
        self.stateChanged.emit()

    def _finish(self):
        if self._identifier is not None:
            self.memory.touch_activity(self._identifier, stamp(self._last))
            self._current = self._identifier = None
            self.changed.emit()

    def tick(self):
        try:
            locked = self._session_blocked or not self.desktop_unlocked()
            if locked != self._locked:
                self._locked = locked
                self.stateChanged.emit()
            if locked or self._paused or not self._enabled:
                self._finish()
                return
            now = self.clock()
            # Sleep/resume gaps are not evidence of time spent in the old app.
            if self._last and (now - self._last).total_seconds() > 10:
                self._finish()
            current = self.reader()
            if not self.desktop_unlocked():
                self._locked = True
                self._finish()
                self.stateChanged.emit()
                return
            if current != self._current:
                if self._identifier is not None:
                    self._last = now
                self._finish()
                if current and current != self._suppressed:
                    self._suppressed = None
                    self._current = current
                    self._identifier = self.memory.start_activity(*current, stamp(now))
                    self._flushed = now
                    self.changed.emit()
            self._last = now
            if self._identifier is not None and (now - self._flushed).total_seconds() >= 10:
                self.memory.touch_activity(self._identifier, stamp(now))
                self._flushed = now
                self.changed.emit()
            if self._failed:
                self._failed = False
                self.stateChanged.emit()
        except Exception:
            try:
                self._finish()
            except Exception:
                self._current = self._identifier = None
            self._failed = True
            self.stateChanged.emit()

    @Slot(str)
    def search(self, query):
        self._query = query
        self.changed.emit()

    @Slot(int)
    def deleteEntry(self, identifier):
        if identifier == self._identifier:
            self._suppressed = self._current
            self._current = self._identifier = None
        self.memory.delete_activity(identifier)
        self.changed.emit()

    @Slot()
    def deleteAll(self):
        self._suppressed = self._current
        self._current = self._identifier = None
        self.memory.delete_activity()
        self.changed.emit()

    def recall(self, question):
        if self._identifier is not None:
            self.memory.touch_activity(self._identifier, stamp(self._last))
        now = self.clock().astimezone()
        if 'yesterday' in question.casefold():
            until = now.replace(hour=0, minute=0, second=0, microsecond=0)
            since = until - timedelta(days=1)
            label = 'yesterday'
        elif 'today' in question.casefold():
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            until = now
            label = 'today'
        else:
            since, until, label = now - timedelta(hours=24), now, 'the last 24 hours'
        query_until = until if label == 'yesterday' else until + timedelta(milliseconds=1)
        entries = self.memory.activities(limit=40, since=stamp(since), until=stamp(query_until))
        records = [dict(app=row['app'], title=row['title'][:240],
                        start=max(datetime.fromisoformat(row['started_at']), since).astimezone().isoformat(),
                        end=min(datetime.fromisoformat(row['ended_at']), until).astimezone().isoformat()) for row in reversed(entries)]
        context = json.dumps({'period': label, 'records': records}, ensure_ascii=False)
        fallback = f'I have no recorded activity for {label}.' if not records else (
            f'Your recorded activity for {label} (app and window titles only):\n' + '\n'.join(
                f"• {row['app']}: {row['title']}" for row in records[-8:]))
        return context, fallback

    def close(self):
        self.timer.stop()
        self._finish()
