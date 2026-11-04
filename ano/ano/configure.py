"""Configure Ano's local brain and connected services.

python -m ano.configure            Configure your free local brain
python -m ano.configure gmail      Gmail address and app password
python -m ano.configure calendar   Google Calendar secret iCal address
python -m ano.configure paper      Alpaca paper credentials; read-only connection check
python -m ano.configure browser    Prepare the Ano extension for Brave/Chrome
python -m ano.configure local      Enable a downloaded Ollama model; disable paid AI
python -m ano.configure autostart on|off|status   Start Ano in the tray at Windows sign-in
python -m ano.configure brain openai [model]|local|status   Choose who answers normal messages
"""
from getpass import getpass
import imaplib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import urllib.request

from .credentials import save_gmail, save_calendar, load_browser_token, save_browser_token
from .plugins.browser import PORT


def gmail():
    print('Create an app password at https://myaccount.google.com/apppasswords '
          '(2-Step Verification must be on). It only works for mail and can be revoked there.')
    address = input('Gmail address: ').strip()
    password = getpass('App password (hidden): ').replace(' ', '')
    try:
        with imaplib.IMAP4_SSL('imap.gmail.com', timeout=20) as imap:
            imap.login(address, password)
    except imaplib.IMAP4.error:
        sys.exit('Gmail rejected that address or app password. Nothing was saved.')
    except OSError:
        sys.exit('Could not reach Gmail. Check the internet connection. Nothing was saved.')
    save_gmail(address, password)
    print('Gmail connected and saved in Windows Credential Manager. Restart Ano to use it.')


def paper():
    from .credentials import save_paper_credentials
    from .paper_feed import AlpacaPaperFeed
    from .tools import ToolError
    print('Use an Alpaca PAPER ONLY account: https://app.alpaca.markets/\n'
          'Enter paper API credentials here, not in chat. This verifies a read-only connection; no orders run.')
    key, secret = getpass('Paper API key (hidden): ').strip(), getpass('Paper secret (hidden): ').strip()
    try:
        result = AlpacaPaperFeed(key,secret).verify()
        if not result['account_active']:
            sys.exit('Paper account is not active. Nothing saved.')
    except ToolError as error:
        sys.exit(str(error))
    save_paper_credentials(key,secret)
    print('Paper credentials saved in Windows Credential Manager. Execution remains disabled.')


def forex():
    """Store a practice credential only after read-only account verification."""
    import re
    import httpx
    from .credentials import vault
    from .oanda_practice import PAIRS
    print('OANDA PRACTICE only. Enter a newly generated token here, never in chat.')
    account = input('V20 demo account ID: ').strip()
    token = getpass('Replacement API token (hidden): ').strip()
    if not re.fullmatch(r'101-\d{3}-\d{8}-\d{3}', account) or not token or any(c.isspace() for c in token):
        sys.exit('Invalid practice account ID or token. Nothing saved.')
    try:
        with httpx.Client(base_url='https://api-fxpractice.oanda.com',
                          headers={'Authorization': 'Bearer '+token},
                          timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get('/v3/accounts/'+account+'/summary')
            response.raise_for_status()
            if response.json()['account']['id'] != account:
                raise ValueError('Account identity mismatch')
            response = client.get('/v3/accounts/'+account+'/instruments',
                                  params={'instruments':','.join(PAIRS)})
            response.raise_for_status()
            if {i['name'] for i in response.json()['instruments']} != set(PAIRS):
                raise ValueError('Requested pairs unavailable')
    except Exception:
        sys.exit('Practice account/pair verification failed. Nothing saved; token was not printed.')
    vault().set_password('Ano/OandaPractice', 'credentials', json.dumps({'account':account,'token':token}))
    print('Practice connection verified; credentials saved in Windows Credential Manager. '
          'No orders submitted. Forex strategy execution is not installed or enabled yet.')


def calendar():
    print('In Google Calendar on the web: Settings > your calendar > Integrate calendar > '
          'Secret address in iCal format. Anyone with this address can see your calendar; keep it private.')
    url = getpass('Secret iCal address (hidden): ').strip()
    if not url.startswith('https://') or not url.endswith('.ics'):
        sys.exit('That does not look like a secret iCal address (https://… .ics). Nothing was saved.')
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            valid = response.read(15) == b'BEGIN:VCALENDAR'
    except OSError:
        sys.exit('Could not download that calendar. Nothing was saved.')
    if not valid:
        sys.exit('That address did not return a calendar. Nothing was saved.')
    save_calendar(url)
    print('Calendar connected and saved in Windows Credential Manager. Restart Ano to use it.')


def browser():
    # The token proves to Ano that a connection comes from this extension copy, not a web page or other app.
    token = load_browser_token() or secrets.token_urlsafe(32)
    save_browser_token(token)
    from .paths import data_directory
    data = data_directory()
    target = data / 'browser-extension'
    shutil.copytree(Path(__file__).parent / 'extension', target, dirs_exist_ok=True)
    (target / 'config.js').write_text(f'const ANO = {json.dumps({"port": PORT, "token": token})};\n', encoding='utf-8')
    print(f'Extension ready in: {target}\n')
    print('In Brave (brave://extensions) or Chrome (chrome://extensions):')
    print('  1. Turn on "Developer mode" (top right).')
    print('  2. Click "Load unpacked" and choose the folder above.')
    print('Do this once in each browser you use. Ano connects whenever both are running.')


def local():
    from .local_runtime import configure_local, data_path
    from .memory import Memory
    model = input('Downloaded Ollama model [ano-local:latest]: ').strip() or 'ano-local:latest'
    memory = Memory(data_path() / 'memory.sqlite3')
    try:
        capabilities = configure_local(model, memory)
    except (OSError, ValueError) as error:
        sys.exit(f'Local setup was not enabled: {error}')
    finally:
        memory.close()
    print('Your free local brain is configured. Images and voice also run locally. Restart Ano to use it.')
    if 'vision' not in capabilities:
        print('This is a text/tool model. Screenshot understanding needs a vision-capable model.')
    print('Restart Ano. Check ANO_LOCAL_* environment overrides if you previously set them.')


def autostart():
    from . import autostart as startup
    choice = sys.argv[2] if len(sys.argv) > 2 else 'status'
    if choice == 'on':
        startup.enable()
        print('Ano will start in the tray when you sign in to Windows, so reminders keep working.')
    elif choice == 'off':
        startup.disable()
        print('Ano will no longer start automatically.')
    elif choice == 'status':
        print('Start with Windows: ' + ('on' if startup.enabled() else 'off'))
    else:
        sys.exit('Use: python -m ano.configure autostart on|off|status')


def brain():
    from .credentials import load_openai_key
    from .local_runtime import data_path
    from .memory import Memory
    from .paid import MODEL, PRICES
    choice = sys.argv[2] if len(sys.argv) > 2 else 'status'
    model = sys.argv[3] if len(sys.argv) > 3 else MODEL
    if choice not in ('openai', 'local', 'status'):
        sys.exit('Use: python -m ano.configure brain openai [model]|local|status')
    if choice == 'openai' and model not in PRICES:
        sys.exit(f'Choose one of: {", ".join(PRICES)}. Nothing was changed.')
    if choice == 'openai' and not load_openai_key():
        sys.exit('No OpenAI key saved. Run python -m ano.configure openai first. Nothing was changed.')
    memory = Memory(data_path() / 'memory.sqlite3')
    try:
        if choice == 'openai':
            memory.set_setting('openai_model', model)
        if choice != 'status':
            memory.set_setting('brain', choice)
        mode = memory.setting('brain', 'local')
        model = memory.setting('openai_model', MODEL)
    finally:
        memory.close()
    if mode == 'openai':
        print(f'Brain: OpenAI {model} (paid). Start a message with local: to use free local AI. Restart Ano to apply.')
    else:
        print('Brain: local (free). Start a message with paid: for one OpenAI task. Restart Ano to apply.')


def main():
    choice = sys.argv[1] if len(sys.argv) > 1 else 'local'
    if choice == 'openai':
        from .credentials import save_openai_key
        save_openai_key(getpass('OpenAI API key (hidden; saved in Windows Credential Manager): '))
        print('Key saved. Local AI remains the default. Type paid: followed by one hard task to opt in. No paid request was sent.')
        return
    if choice in ('claude', 'cloud'):
        sys.exit('Cloud AI brains have been removed. Use: python -m ano.configure local')
    actions = {'paper': paper, 'forex': forex, 'gmail': gmail, 'calendar': calendar, 'browser': browser,
               'local': local, 'autostart': autostart, 'brain': brain}
    if choice not in actions:
        sys.exit(__doc__)
    actions[choice]()


if __name__ == '__main__':
    main()
