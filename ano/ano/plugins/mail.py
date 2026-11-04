"""Gmail over IMAP/SMTP with an app password. Sending always waits for the user's click."""
import email
from email.message import EmailMessage
from email.policy import default as email_policy
from email.utils import getaddresses, formataddr
from html import escape
from html.parser import HTMLParser
import imaplib
import re
import smtplib

from PySide6.QtCore import QObject, Property, Signal, Slot, QRunnable, QThreadPool

from ..tools import ToolError

IMAP_HOST = 'imap.gmail.com'
SMTP_HOST = 'smtp.gmail.com'
MAX_RESULTS = 25
MAX_BODY = 20000


def sports_self_draft(address, draft):
    recipients=addresses(draft.get('to',''))
    if len(recipients)!=1 or recipients[0].casefold()!=address.casefold() or addresses(draft.get('cc','')):
        return False
    text=draft.get('subject','')+'\n'+draft.get('body','')
    return bool(re.search(r'\bscorpred\b',text,re.I) or (
        re.search(r'\b(?:nba|soccer|nfl)\b',text,re.I) and
        re.search(r'\b(?:picks?|props?|forecasts?|insights?|highlights?|predictions?)\b',text,re.I)))


def sports_summary_body(body):
    body=re.sub(r'\bno bets placed or email sent\b','no bets placed',body,flags=re.I)
    # Summarized historical text is not a fresh provider read. Make that
    # distinction in both the editable plain body and its HTML alternative.
    notice=('User-reviewed sports summary, not a fresh verified report. '
            'Any source timestamps or sample sizes not stated above are unavailable. '
            'Request a fresh sports report for current evidence; no bets placed.')
    if notice not in body:
        body=body.rstrip()+'\n\n'+notice
    return body


class Gmail:
    def __init__(self, address, password, outbox):
        self.address = address
        self.password = password
        self.outbox = outbox

    def definitions(self):
        return [
            {'name': 'search_email',
             'description': f"Search the user's Gmail ({self.address}) with Gmail search syntax, e.g. "
                            '"is:unread", "from:alex newer_than:7d", "invoice in:inbox". Empty query lists the '
                            'newest inbox mail. Returns id, date, sender and subject, newest first. Does not mark '
                            'anything as read.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'query': {'type': 'string'},
                 'limit': {'type': 'integer', 'description': f'1 to {MAX_RESULTS}, default 10'}}}},
            {'name': 'read_email',
             'description': 'Read one email by the id from search_email: headers, text and attachment names.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
            {'name': 'send_email',
             'description': 'Prepare an email from the user\'s Gmail. It is NOT sent: Ano shows it to the user, '
                            'who can edit it and must click Send. For a reply, pass reply_to_id so it threads.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'to': {'type': 'string', 'description': 'Comma-separated addresses'},
                 'cc': {'type': 'string'},
                 'subject': {'type': 'string'},
                 'body': {'type': 'string', 'description': 'Plain text'},
                 'reply_to_id': {'type': 'string'}}, 'required': ['to', 'subject', 'body']}},
        ]

    def run(self, name, arguments):
        try:
            return getattr(self, name)(**arguments)
        except imaplib.IMAP4.error:
            raise ToolError('Gmail refused the request. The app password may have been revoked.') from None
        except OSError:
            raise ToolError('Could not reach Gmail. Check the internet connection.') from None

    def search_email(self, query='', limit=10):
        limit = max(1, min(int(limit), MAX_RESULTS))
        query = query.strip() or 'in:inbox'
        with self._connect() as imap:
            if query.isascii():
                status, data = imap.uid('SEARCH', 'X-GM-RAW', quote(query))
            else:
                imap.literal = query.encode('utf-8')
                status, data = imap.uid('SEARCH', 'CHARSET', 'UTF-8', 'X-GM-RAW')
            if status != 'OK':
                raise ToolError('Gmail could not run that search.')
            uids = data[0].split()[-limit:][::-1]
            if not uids:
                return 'No matching emails.', f'Searched Gmail for “{query}”'
            status, data = imap.uid('FETCH', b','.join(uids).decode(),
                                    '(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
        found = {}
        for item in data:
            if isinstance(item, tuple):
                uid = re.search(rb'UID (\d+)', item[0])
                if uid:
                    found[uid.group(1)] = email.message_from_bytes(item[1], policy=email_policy)
        lines = [f'id {uid.decode()} · {found[uid]["date"]} · {found[uid]["from"]} · {found[uid]["subject"]}'
                 for uid in uids if uid in found]
        return '\n'.join(lines), f'Searched Gmail for “{query}”'

    def read_email(self, id):
        message = self._fetch(id)
        body = message.get_body(preferencelist=('plain', 'html'))
        text = ''
        if body is not None:
            text = body.get_content()
            if body.get_content_type() == 'text/html':
                text = html_text(text)
        if len(text) > MAX_BODY:
            text = text[:MAX_BODY] + f'\n[Showing the first {MAX_BODY:,} of {len(text):,} characters.]'
        attachments = [part.get_filename() for part in message.iter_attachments() if part.get_filename()]
        header = '\n'.join(f'{field}: {message[field]}' for field in ('From', 'To', 'Cc', 'Date', 'Subject')
                           if message[field])
        if attachments:
            header += '\nAttachments: ' + ', '.join(attachments)
        return f'{header}\n\n{text.strip()}', f'Read email “{message["subject"]}”'

    def send_email(self, to, subject, body, cc='', reply_to_id=''):
        recipients = addresses(to)
        if not recipients:
            raise ToolError('No valid recipient address.')
        draft = {'to': ', '.join(recipients), 'cc': ', '.join(addresses(cc)),
                 'subject': ' '.join(subject.split()), 'body': body, 'inReplyTo': '', 'references': ''}
        if sports_self_draft(self.address,draft):
            draft['body']=sports_summary_body(body)
        if reply_to_id:
            original = self._fetch(reply_to_id)
            draft['inReplyTo'] = original['message-id'] or ''
            draft['references'] = ' '.join(filter(None, [original['references'], original['message-id']]))
        self.outbox.propose(draft)
        return ('The email is now shown in Ano’s local outbox for review. It is NOT a Gmail draft and has NOT been sent; '
                'tell the user to check Ano’s outbox and click Send.'), f'Prepared an email to {draft["to"]} for you to review'

    def deliver(self, draft):
        message = EmailMessage()
        message['From'] = self.address
        message['To'] = draft['to']
        if draft['cc']:
            message['Cc'] = draft['cc']
        message['Subject'] = draft['subject']
        if draft['inReplyTo']:
            message['In-Reply-To'] = draft['inReplyTo']
            message['References'] = draft['references']
        if sports_self_draft(self.address,draft):
            from ..email_theme import premium_frame
            from ..sports_reports import LOGO
            body=sports_summary_body(draft['body'])
            message.replace_header('From',formataddr(('Ano',self.address)))
            message['Auto-Submitted']='auto-generated'
            message['X-Ano-Report']='sports-summary-v2'
            message.set_content(body)
            markup='<p style="white-space:pre-wrap">'+escape(body).replace('\n','<br>')+'</p>'
            message.add_alternative(premium_frame('here is your sports summary.',markup,
                subtitle='Reviewed sports summary',
                reply_help='Replies to this reviewed summary are not monitored. Ask Ano for a fresh sports report to use the authenticated reply channel.'),subtype='html')
            message.get_payload()[-1].add_related(LOGO.read_bytes(),maintype='image',subtype='png',
                                                  cid='<ano-logo>',filename='ano-logo.png')
        else:
            message.set_content(draft['body'])
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30) as smtp:
            smtp.login(self.address, self.password)
            smtp.send_message(message)

    def _connect(self):
        imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=20)
        imap.login(self.address, self.password)
        imap.select(all_mail(imap), readonly=True)
        return imap

    def _fetch(self, identifier):
        if not str(identifier).isdigit():
            raise ToolError('Use an id from search_email.')
        with self._connect() as imap:
            status, data = imap.uid('FETCH', str(identifier), '(BODY.PEEK[])')
        raw = next((item[1] for item in data if isinstance(item, tuple)), None)
        if status != 'OK' or raw is None:
            raise ToolError('That email was not found.')
        return email.message_from_bytes(raw, policy=email_policy)


def all_mail(imap):
    """Gmail's All Mail folder has a localised name, so find it by its \\All flag."""
    status, boxes = imap.list()
    for line in boxes if status == 'OK' else []:
        match = re.search(rb'\\All\b.*"([^"]+)"$', line or b'')
        if match:
            return quote(match.group(1).decode('ascii'))
    return 'INBOX'


def quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def addresses(text):
    return [address for _, address in getaddresses([text or '']) if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', address)]


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        elif tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(markup):
    parser = _TextOnly()
    parser.feed(markup)
    return re.sub(r'\n\s*\n+', '\n\n', ''.join(parser.parts))


class SendSignals(QObject):
    completed = Signal(str)


class SendJob(QRunnable):
    def __init__(self, deliver, draft):
        super().__init__()
        self.deliver = deliver
        self.draft = draft
        self.signals = SendSignals()

    def run(self):
        try:
            self.deliver(self.draft)
            self.signals.completed.emit('')
        except smtplib.SMTPAuthenticationError:
            self.signals.completed.emit('Gmail rejected the app password. Run python -m ano.configure gmail.')
        except Exception:
            self.signals.completed.emit('The email could not be sent. Check the address and your connection.')


class Outbox(QObject):
    """Holds one prepared email until the user sends or discards it (main thread only)."""
    changed = Signal()
    finished = Signal(str)
    _proposed = Signal('QVariantMap')

    def __init__(self, allowed=lambda: True):
        super().__init__()
        self.allowed = allowed
        self.deliver = None
        self._draft = None
        self._sending = False
        self._job = None
        self.pool = QThreadPool(self)
        # Emitted from the Claude worker thread; delivered to this object on the main thread.
        self._proposed.connect(self._show)

    def propose(self, draft):
        self._proposed.emit(draft)

    @Slot('QVariantMap')
    def _show(self, draft):
        if not self._sending and self.allowed():
            self._draft = dict(draft)
            self.changed.emit()

    @Property(bool, notify=changed)
    def ready(self):
        return self._draft is not None

    @Property(bool, notify=changed)
    def sending(self):
        return self._sending

    @Property('QVariantMap', notify=changed)
    def draft(self):
        return self._draft or {}

    @Slot(str, str)
    def send(self, subject, body):
        if self._draft is None or self._sending or not self.deliver or not self.allowed():
            return
        draft = dict(self._draft, subject=' '.join(subject.split()), body=body)
        self._sending = True
        self.changed.emit()
        self._job = SendJob(self.deliver, draft)
        self._job.signals.completed.connect(self._sent)
        self.pool.start(self._job)

    @Slot(str)
    def _sent(self, error):
        recipients = self._job.draft['to']
        self._sending = False
        self._job = None
        if not error:
            self._draft = None
        self.changed.emit()
        self.finished.emit(error or f'Email sent to {recipients}')

    @Slot()
    def discard(self):
        if not self._sending and self._draft is not None:
            self._draft = None
            self.changed.emit()
            self.finished.emit('Email discarded · Nothing was sent')

    def close(self):
        self.pool.waitForDone(30000)
