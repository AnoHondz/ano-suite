"""Whole-request, read-only sports/date grammar. Never authorizes email or orders."""
from datetime import datetime,timedelta
import re
from zoneinfo import ZoneInfo


def parse(text,today=None):
    today=today or datetime.now(ZoneInfo('America/New_York')).date()
    key=re.sub(r'\s+',' ',text.casefold().replace('’',"'")).strip(' .!?')
    if re.search(r'\b(email|mail|trade|bet|buy|sell|cancel|delete|start|stop|disable|enable)\b',key):return None
    key=re.sub(r'^(?:(?:can|could|would) you (?:please )?|please )','',key)
    key=re.sub(r'^(?:show me|show|get|check|pull up|find|give me|send me) (?:the )?','',key)
    key=re.sub(r'^(?:scorpred|scopred|scorped) ','',key)
    key=re.sub(r' (?:please|for me)$','',key)
    # Date tokens may lead or follow the requested sport; every remaining token
    # still must match, so embedded commands and multi-step text are not coerced.
    dates=re.findall(r'\b(?:today|tomorrow|yesterday|\d{4}-\d{2}-\d{2})\b',key)
    if len(dates)>1:return None
    day=None
    if dates:
        token=dates[0]
        try:
            day=(today+timedelta(days={'today':0,'tomorrow':1,'yesterday':-1}[token])).isoformat() if token in ('today','tomorrow','yesterday') else datetime.strptime(token,'%Y-%m-%d').date().isoformat()
        except ValueError:return None
        key=re.sub(r'\b'+re.escape(token)+r"(?:'s)?\b",'',key)
    key=re.sub(r'\b(?:for|on)\b','',key)
    key=re.sub(r'\s+',' ',key).strip()
    match=re.fullmatch(r'(nba|soccer|nfl)(?: (premier league|bundesliga|la liga))?(?: (?:picks|predictions|slate|games|setups|insights|matchups|match ups))?',key)
    if not match:return None
    sport,league=match.groups()
    if league and sport!='soccer':return None
    result={'sport':sport}
    if day:result['date']=day
    if league:result['league']={'premier league':39,'bundesliga':78,'la liga':140}[league]
    return result
