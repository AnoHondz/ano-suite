"""Windows Credential Manager storage; never falls back to a plaintext file."""
import os
import sys

SERVICE = 'Ano/Anthropic'
ACCOUNT = 'api-key'
GMAIL = 'Ano/Gmail'
CALENDAR = 'Ano/GoogleCalendar'
BROWSER = 'Ano/BrowserExtension'
OPENAI = 'Ano/OpenAI'
PAPER = 'Ano/AlpacaPaper'


def load_paper_credentials():
    import json
    value = vault().get_password(PAPER, 'credentials')
    if not value:
        return None
    data = json.loads(value)
    return data['key'], data['secret']


def save_paper_credentials(key, secret):
    import json
    vault().set_password(PAPER, 'credentials', json.dumps({'key':key,'secret':secret}))


def vault():
    if sys.platform != 'win32':
        raise RuntimeError('Credential storage requires Windows.')
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()


def load_key():
    return os.environ.get('ANTHROPIC_API_KEY') or vault().get_password(SERVICE, ACCOUNT)


def save_key(value):
    value = value.strip()
    if not value.startswith('sk-ant-') or any(char.isspace() for char in value):
        raise ValueError('Enter a Claude API key beginning with sk-ant-.')
    vault().set_password(SERVICE, ACCOUNT, value)


def load_gmail():
    """Return (address, app password) or None."""
    store = vault()
    address = store.get_password(GMAIL, 'address')
    password = store.get_password(GMAIL, 'app-password')
    return (address, password) if address and password else None


def save_gmail(address, password):
    store = vault()
    store.set_password(GMAIL, 'address', address)
    store.set_password(GMAIL, 'app-password', password)


def load_openai_key():
    return os.environ.get('OPENAI_API_KEY') or vault().get_password(OPENAI, 'api-key')


def save_openai_key(value):
    value = value.strip()
    if not value.startswith('sk-') or any(char.isspace() for char in value):
        raise ValueError('Enter an OpenAI API key beginning with sk-.')
    vault().set_password(OPENAI, 'api-key', value)


def load_browser_token():
    return vault().get_password(BROWSER, 'token')


def save_browser_token(token):
    vault().set_password(BROWSER, 'token', token)


def load_calendar():
    return vault().get_password(CALENDAR, 'ical-url')


def save_calendar(url):
    vault().set_password(CALENDAR, 'ical-url', url)
