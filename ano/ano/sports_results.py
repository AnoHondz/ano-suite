"""Verify completed public scoreboards; never infer outcomes from predictions."""
import json,re
from decimal import Decimal,InvalidOperation
from urllib.request import build_opener,ProxyHandler,HTTPRedirectHandler
from .report_ledger import timestamp


def _get(url):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with build_opener(ProxyHandler({}),NoRedirect()).open(url,timeout=5) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError('Oversized scoreboard response')
    return json.loads(raw)


def verified_result(evidence,*,now,getter=None):
    """Final observed_at is our verification time, not a claimed exact end time.

    Unsupported providers, changed kickoff times and unmatched teams stay pending.
    No historical prediction-tracker outcome is trusted as final-score evidence.
    """
    sport=evidence.get('sport');event=str(evidence.get('event') or '')
    route={'nba':'basketball/nba','nfl':'football/nfl'}.get(sport)
    if sport=='soccer':
        league={39:'eng.1',140:'esp.1',78:'ger.1',135:'ita.1',61:'fra.1',2:'uefa.champions',3:'uefa.europa'}.get(evidence.get('league'))
        if league:route='soccer/'+league
    if not route or not re.fullmatch(r'[0-9]{1,12}',event):return None
    url=f'https://site.api.espn.com/apis/site/v2/sports/{route}/summary?event={event}'
    try:
        payload=(getter or _get)(url);header=payload['header']
        if str(header.get('id'))!=event:return None
        competitions=header.get('competitions') or []
        if len(competitions)!=1:return None
        game=competitions[0];status=game['status']['type']
        if status.get('completed') is not True or status.get('state')!='post':return None
        # Soccer winner probabilities are regulation-time 1X2. Extra-time and
        # penalty results cannot certify that market without a 90-minute score.
        if sport=='soccer' and status.get('name')!='STATUS_FULL_TIME':return None
        expected=timestamp(evidence.get('kickoff'));actual=timestamp(game.get('date'))
        if not expected or not actual or expected>now or abs((expected-actual).total_seconds())>60:return None
        teams=evidence.get('teams') or []
        if len(teams)!=2 or len(set(teams))!=2:return None
        normalize=lambda value:' '.join(str(value).casefold().split())
        labels={normalize(team):team for team in teams};scores={}
        for item in game.get('competitors') or []:
            label=labels.get(normalize((item.get('team') or {}).get('displayName')))
            if label is None or label in scores:return None
            score=Decimal(str(item.get('score')))
            if not score.is_finite() or score<0 or score!=score.to_integral_value():return None
            scores[label]=score
        if set(scores)!=set(teams):return None
        if scores[teams[0]]==scores[teams[1]]:
            if sport=='nba':return None
            winner='Draw'
        else:winner=max(scores,key=scores.get)
        return {'winner':winner,'source':url,'observed_at':now.isoformat(),'verified':True}
    except (ValueError,KeyError,TypeError,InvalidOperation,OSError):return None
