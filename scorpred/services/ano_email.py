"""Ano visual identity for the existing daily digest; no changes to forecast gates."""
import json
from pathlib import Path
import re

LOGO = Path(__file__).resolve().parents[1]/'static'/'ano-mail-logo.png'


def token():
    try:
        cfg=json.loads((Path.home()/'Documents'/'AnoWorkspace'/'.ano-state'/'sports-mail'/'config.json').read_text())
        value=cfg.get('token','')
        return value if cfg.get('enabled') and re.fullmatch('[a-f0-9]{12}',value) else ''
    except (OSError,ValueError):return ''


def mail_subject(subject):
    key=token()
    return (f'[ANO:{key}] ' if key else '')+subject.replace('ScorPred','Ano · Sports desk',1)


def brand(legacy, generated_at=None, metrics=()):
    from .ano_email_theme import premium_frame
    from datetime import datetime
    from zoneinfo import ZoneInfo
    match=re.search(r'<body[^>]*>(.*)</body>',legacy,re.S)
    body=match[1] if match else legacy
    body=body.replace('<h1>ScorPred daily sports digest</h1>', '')
    return premium_frame('here are your sports insights.', body, token=token(),
                         when=datetime.fromisoformat(generated_at.replace('Z','+00:00')) if generated_at else datetime.now(ZoneInfo('America/New_York')),
                         subtitle='Your daily sports briefing', metrics=metrics)
