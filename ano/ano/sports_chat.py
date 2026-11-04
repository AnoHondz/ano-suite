"""Short sports replies in everyday words, with the evidence kept in the source report."""
import math,re
from datetime import date
from .sports_reports import time_label

# ScorPred's decision codes, said the way a friend would
STATUS_WORDS={'WAIT':'hold off for now','CONSIDER':'worth a look','BET':'worth a look','SKIP':"I'd skip it",'NO BET':"I'd skip it"}


def day_words(day):
    """'2026-11-04' -> 'Wed, Nov 4'."""
    try:
        d=date.fromisoformat(str(day))
    except ValueError:
        return str(day)
    return f'{d:%a}, {d:%b} {d.day}'


def compact_insights(sport,day,data,code):
    lines=[f'{sport.upper()} for {day_words(day)}']
    if code!=200 or data.get('error'):return '\n'.join(lines+["I can't get the game reads right now. Give it a minute and ask again."])
    rows=data.get('slate') or []
    if not rows:return '\n'.join(lines+['No games on that day.'])
    if data.get('refresh_pending'):lines.append("ScorPred is still updating, so treat these as first looks.")
    def priority(row):
        p=row.get('winProbability')
        return (row.get('action')=='CONSIDER',row.get('pregameAvailable',False),p if type(p) in (int,float) else -1)
    for row in sorted(rows,key=priority,reverse=True)[:3]:
        name=f'{row.get("teamA","?")} vs {row.get("teamB","?")}'
        p=row.get('winProbability');side=row.get('side')
        valid=type(p) in (int,float) and math.isfinite(p) and 0<=p<=100 and side not in (None,'Unavailable')
        early=row.get('researchPick') or (row.get('dataQuality') or {}).get('state')=='provisional'
        if valid:lines.append(f'• {name}: I lean {side}, about a {p:.0f}% chance to win'+(' (early read, data still coming in).' if early else '.'))
        else:lines.append(f'• {name}: no read on this one yet.')
    lines.append('Check injuries and the odds in your app before betting anything; these are early reads, not sure things.')
    lines.append('You can ask me “show NBA props” or “check Naz Reid over 20 points and assists”.' if sport=='nba'
                 else f'You can ask me “show {sport} props” for player picks.')
    return '\n'.join(lines)


def parse_player_prop(text):
    key=' '.join(text.casefold().replace('’',"'").split()).strip(' .!?')
    if re.search(r'\b(email|mail|send|trade|buy|sell|cancel|delete)\b',key):return None
    key=re.sub(r'\bassits\b','assists',key)
    key=re.sub(r'\b(over|under)(?=\d)',r'\1 ',key)
    key=re.sub(r'(\d)(?=pts\b|points\b)',r'\1 ',key)
    match=re.fullmatch(r'(?:(?:check|analyze|analyse|scorpred nba|nba) )?'
        r'([a-z][a-z .\-\']{2,60}?) (over|under) (\d+(?:\.5)?) (?:combined )?'
        r'(points (?:and|plus|\+) assists|pts (?:and|plus|\+) (?:assists|asts)|points|pts|rebounds|assists)'
        r'(?: (\d{4}-\d{2}-\d{2}))?',key)
    if not match:return None
    player,side,line,market,day=match.groups()
    result={'player':player,'side':side,'line':float(line),
            'market':'pa' if any(x in market for x in (' and ',' plus ',' + ')) else 'points' if market=='pts' else market}
    if day:result['date']=day
    return result


def player_prop_reply(result):
    if not result.get('forecast'):
        return "I couldn't check that one: "+str(result.get('reason') or "I couldn't match that player and line.")
    f=result['forecast'];side=result['side'];h=f['historical'];count=h['games'];hits=h[side+'_hits']
    other='under' if side=='over' else 'over'
    history=(f"He's gone {side} in {hits} of his last {count} games with his current team." if count
             else "I don't have enough games with his current team to show a record.")
    call=STATUS_WORDS.get(str(result['status']).upper(),str(result['status']).lower())
    reason=str(result['reason'] or '').strip().rstrip('.')
    return '\n'.join([f"{result['player']} {side} {result['line']:g} {result['label'].lower()}",
        f"I'd give it about a {100*f[side+'_probability']:.0f}% chance ({100*f[other+'_probability']:.0f}% for the {other}). {history}",
        *([f"There's about a {100*f['push_probability']:.0f}% chance he lands exactly on {result['line']:g}, a push, so you'd get your money back."]
          if f['push_probability'] else []),
        f"I'm expecting around {result['projection']:.0f} in about {result['minutes']:.0f} minutes.",
        f"My call: {call}"+(f" ({reason[:1].lower()+reason[1:]})." if reason else '.'),
        "That's your line, and I can't see the sportsbook's price for it, so check it in your app first. This is my best guess, not a sure thing."])
