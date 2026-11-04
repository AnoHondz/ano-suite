"""Whole-request desktop sports email grammar; context comes from user turns.

This selects only fixed self-report parameters. It never selects recipients,
copies an assistant answer into an email, or treats quoted content as a command.
"""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from .sports_query import parse as sports_query


def report_context(text, today=None):
    key=' '.join(text.casefold().replace('’', "'").split()).strip(' .!?')
    key=re.sub(r'^(?:local|paid):\s*','',key)
    key=re.sub(r'\b(?:scorpred|scopred)\b','',key)
    key=re.sub(r'\s+',' ',key).strip()
    props=bool(re.search(r'\b(?:players?|props|highlights)\b',key))
    key=re.sub(r'\b(?:player highlights|player props|players|player|props|highlights)\b','picks',key)
    query=sports_query(key,today=today)
    if query:
        return {'sport':query['sport'],'day':query.get('date'),'props':props}
    return None


def email_request(text, history=None, today=None):
    today=today or datetime.now(ZoneInfo('America/New_York')).date()
    key=' '.join(text.casefold().replace('’', "'").split()).strip(' .!?')
    prefix=r'(?:(?:hey )?ano[, ]+)?(?:please )?(?:(?:can|could|would) you (?:please )?)?'
    match=re.fullmatch(prefix+r'(?:email|mail)(?: me)?(?: (?:the )?(.+))?',key)
    if not match:
        return None
    target=(match[1] or '').strip()
    # No inferred forwarding, CC, quotes, or multi-step commands.
    if re.search(r'[@<>;\n]|\b(?:to|cc|bcc|forward|then|delete|buy|sell|bet|trade)\b',target):
        return None
    previous=None
    prior=list(history or [])
    if prior and prior[-1].get('role')=='user' and re.sub(r'^\s*(?:local|paid):\s*','',prior[-1].get('body',''),flags=re.I).strip()==text.strip():
        prior.pop()
    # Only the immediately preceding user request supplies context. Unrelated
    # turns and assistant-generated/provider text cannot authorize a report.
    for item in reversed(prior):
        if item.get('role')=='user':
            previous=report_context(item.get('body',''),today)
            break
    if not target or target in ('that','this','it','the report','scorpred','scopred','scorpred report','scorpred results','scorpred predictions'):
        if previous:
            return previous
        if target.startswith(('scorpred','scopred')):
            return {'sport':'all','day':None,'props':True}
        return {'question':'Which sports report should I email? Say “email me NBA picks” or “email me NBA props”. No email was sent.'}
    query=report_context(target,today)
    if query:
        # A general NBA report includes both the game summary and player props.
        # Explicit "NBA picks" remains picks-only; chat parsing is unchanged.
        if query['sport']=='nba' and not re.search(r'\b(picks|predictions|games|slate|matchups)\b',target):
            query={**query,'props':True}
        return query
    if target in ('picks','all picks','all sports picks','all sports','props','player props'):
        return {'sport':'all','day':None,'props':True}
    if re.search(r'\b(?:nba|soccer|nfl|scorpred|scopred)\b',target):
        return {'question':'Use one sport (NBA, soccer, NFL, or all sports), with today, tomorrow, or a valid YYYY-MM-DD date. No email was sent.'}
    return None
