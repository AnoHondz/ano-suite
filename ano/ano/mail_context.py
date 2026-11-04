"""Bounded email follow-ups. Context is selected by known reply IDs, not subject."""
from datetime import datetime,timedelta
import re
from zoneinfo import ZoneInfo
from .sports_query import parse as sports_query


def resolve(text, parent=None, today=None):
    from .sports_mail import parse_command
    today=today or datetime.now(ZoneInfo('America/New_York')).date()
    first=next((line.strip() for line in text.splitlines() if line.strip()),'')
    key=re.sub(r'\s+',' ',first.casefold().replace('’',"'")).strip(' .!?')
    command=parse_command(first)
    if command:return {'command':command,'day':None}
    # Same whole-request read grammar as the desktop; props changes only which
    # public report is read, not the ability to act on the computer.
    read_key=re.sub(r'^email me (?:the )?','',key)
    query=sports_query(re.sub(r'\b(?:player )?props\b','picks',read_key),today)
    if query:return {'command':query['sport']+(' props' if 'props' in key else ' picks'),'day':query.get('date')}
    if re.search(r'\b\d{4}-\d{2}-\d{2}\b',key) and re.search(r'\b(nba|soccer|nfl)\b',key):
        return {'command':'clarify:date','day':None}
    follow=re.fullmatch(r'(?:same (?:for|on) |what about )?(today|tomorrow|yesterday)(?: instead)?',key)
    props=key in ('props too','player props too','add props','include props')
    swap=re.fullmatch(r'(?:same for|what about) (nba|soccer|nfl)',key)
    if follow or props or swap:
        base=(parent or {}).get('report_command','')
        if base not in ('picks','props',*{f'{s} {k}' for s in ('nba','soccer','nfl') for k in ('picks','props')}):
            return {'command':'clarify:context','day':None}
        day=(parent or {}).get('day')
        if follow:day=(today+timedelta(days={'today':0,'tomorrow':1,'yesterday':-1}[follow[1]])).isoformat()
        if props:base=base.split()[0]+' props' if ' ' in base else 'props'
        if swap:base=swap[1]+(' props' if base.endswith('props') else ' picks')
        return {'command':base,'day':day}
    if not first or first.startswith(('>','<')):return {'command':'help','day':None}
    return {'command':'ask:'+first[:400],'day':None}
