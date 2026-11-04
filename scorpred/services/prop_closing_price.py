"""Exact-line price movement, never a fabricated closing quote."""
from datetime import timedelta
import math
from services.prediction_lifecycle import parse_kickoff


def last_observed_price(store, row, kickoff):
    quote=row.get('quote') or {}
    start=parse_kickoff(kickoff)
    event=quote.get('provider_event_id');player=quote.get('provider_player')
    price=quote.get('decimal_odds');side=quote.get('side')
    line=quote.get('line')
    unavailable={'status':'unavailable','notice':'No matching, sufficiently recent pregame price observation. Actual closing price is not established.'}
    if (not start or not event or not player or not quote.get('bookmaker_key') or not quote.get('market')
            or isinstance(line,bool) or not isinstance(line,(int,float)) or not math.isfinite(line) or line<0
            or side not in ('over','under') or isinstance(price,bool)
            or not isinstance(price,(int,float)) or not math.isfinite(price) or price<=1):
        return unavailable
    observed=[]
    for evidence in store.as_of('basketball_nba:'+str(event),start):
        candidate=evidence['payload']
        if (evidence['kind']!='sportsbook_quote' or candidate.get('player')!=player
                or candidate.get('bookmaker_key')!=quote.get('bookmaker_key')
                or candidate.get('market')!=quote.get('market') or candidate.get('line')!=quote.get('line')):
            continue
        published=parse_kickoff(evidence['published_at'])
        received=parse_kickoff(evidence['received_at'])
        original=parse_kickoff(quote.get('fetched_at'))
        if not published or not received or not original or received<original or start-published>timedelta(minutes=15):
            continue
        last=candidate.get(side+'_decimal')
        if isinstance(last,bool) or not isinstance(last,(int,float)) or not math.isfinite(last) or last<=1:continue
        observed.append((published,received,evidence['id'],last))
    if not observed:return unavailable
    published,received,identifier,last=max(observed)
    return {'status':'last_observed_pregame','evidence_id':identifier,'published_at':published.isoformat(),
        'received_at':received.isoformat(),'seconds_before_tipoff':(start-published).total_seconds(),
        'initial_decimal_odds':price,'last_observed_decimal_odds':last,
        'initial_to_last_price_ratio':price/last-1,
        'notice':'Same bookmaker, player, market and line. Positive ratio means the initial price paid more. This is observed price movement, not verified closing-line value or demonstrated profit.'}
