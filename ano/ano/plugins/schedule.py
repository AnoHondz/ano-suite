"""Google Calendar: read through the private iCal address; new events open pre-filled for the user to save."""
from datetime import date, datetime, time as day_time, timedelta
import time
from urllib.parse import urlencode
import urllib.request

from ..tools import ToolError

CACHE_SECONDS = 300
MAX_DAYS = 31
MAX_EVENTS = 100


class GoogleCalendar:
    def __init__(self, ical_url, open_url, download=None, today=date.today):
        self.ical_url = ical_url
        self.open_url = open_url
        self.download = download or fetch
        self.today = today
        self._cache = (0, None)

    def definitions(self):
        return [
            {'name': 'calendar_events',
             'description': "List events in the user's Google Calendar, in local time.",
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'start_date': {'type': 'string', 'description': 'YYYY-MM-DD, default today'},
                 'days': {'type': 'integer', 'description': f'1 to {MAX_DAYS}, default 7'}}}},
            {'name': 'add_calendar_event',
             'description': 'Open Google Calendar in the browser with a new event filled in. The user checks it '
                            'and clicks Save; it is not saved until then.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'title': {'type': 'string'},
                 'start': {'type': 'string', 'description': 'Local YYYY-MM-DDTHH:MM, or YYYY-MM-DD for all day'},
                 'end': {'type': 'string', 'description': 'Same format; default one hour later or one day'},
                 'location': {'type': 'string'},
                 'details': {'type': 'string'}}, 'required': ['title', 'start']}},
        ]

    def run(self, name, arguments):
        return getattr(self, name)(**arguments)

    def calendar_events(self, start_date='', days=7):
        import recurring_ical_events
        if not self.ical_url:
            raise ToolError('Google Calendar is not connected. The user can run: python -m ano.configure calendar')
        first = parse_day(start_date) if start_date else self.today()
        days = max(1, min(int(days), MAX_DAYS))
        start = datetime.combine(first, day_time()).astimezone()
        events = recurring_ical_events.of(self._calendar()).between(start, start + timedelta(days=days))
        rows = sorted((event_row(event) for event in events), key=lambda row: row[0])[:MAX_EVENTS]
        span = f'{first:%a %d %b}' + (f' to {first + timedelta(days=days - 1):%a %d %b}' if days > 1 else '')
        note = f'Checked your calendar for {span}'
        return ('\n'.join(row[1] for row in rows) or f'No events {span}.'), note

    def add_calendar_event(self, title, start, end='', location='', details=''):
        if 'T' in start:
            begin = parse_time(start)
            finish = parse_time(end) if end else begin + timedelta(hours=1)
            dates = f'{begin:%Y%m%dT%H%M%S}/{finish:%Y%m%dT%H%M%S}'
        else:
            begin = parse_day(start)
            finish = parse_day(end) + timedelta(days=1) if end else begin + timedelta(days=1)
            dates = f'{begin:%Y%m%d}/{finish:%Y%m%d}'
        if finish <= begin:
            raise ToolError('The event must end after it starts.')
        query = {'action': 'TEMPLATE', 'text': title, 'dates': dates}
        query.update({key: value for key, value in (('location', location), ('details', details)) if value})
        if not self.open_url('https://calendar.google.com/calendar/render?' + urlencode(query)):
            raise ToolError('Windows could not open the browser.')
        return ('Google Calendar is open with the event filled in. It is NOT saved until the user clicks Save.',
                f'Opened Google Calendar to add “{title}”')

    def _calendar(self):
        from icalendar import Calendar
        fetched, calendar = self._cache
        if calendar is None or time.monotonic() - fetched > CACHE_SECONDS:
            try:
                calendar = Calendar.from_ical(self.download(self.ical_url))
            except OSError:
                raise ToolError('Could not reach Google Calendar. Check the internet connection.') from None
            except ValueError:
                raise ToolError('The calendar address did not return a calendar. Reconnect it.') from None
            self._cache = (time.monotonic(), calendar)
        return calendar


def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read()


def parse_day(text):
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        raise ToolError('Use dates like 2026-10-27.') from None


def parse_time(text):
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        raise ToolError('Use times like 2026-10-27T14:30.') from None


def event_row(event):
    start = event.get('DTSTART').dt
    end = event.get('DTEND').dt if event.get('DTEND') else None
    title = str(event.get('SUMMARY', '(no title)'))
    location = str(event.get('LOCATION', '') or '')
    if isinstance(start, datetime):
        start = start.astimezone()  # Floating (naive) times are already local.
        when = f'{start:%a %d %b %H:%M}'
        if isinstance(end, datetime):
            when += f'–{end.astimezone():%H:%M}'
        key = start
    else:
        when = f'{start:%a %d %b} (all day)'
        key = datetime.combine(start, day_time()).astimezone()
    return key, ' · '.join(filter(None, [when, title, location]))
