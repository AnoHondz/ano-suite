"""Read-only, model-free sports snapshots and email-friendly Ano cards."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError

NY = ZoneInfo('America/New_York')
SPORTS = ('nba', 'soccer', 'nfl')
LOGO = Path(__file__).parent / 'assets' / 'ano-mark.png'


def player_label(p):
    """'Name (Team)' for a saved prop; older snapshots without a team show the name alone."""
    return f"{p['player']} ({p['team']})" if p.get('team') else p['player']


def sportsbook_lines(p, now=None):
    """The real offered lines on the pick's side, each with the model's own chance at that line.

    A historical reference line is not what a book offers; when quotes exist they are shown beside it.
    Expired quotes are dropped rather than presented as current prices."""
    from datetime import timezone
    now = now or datetime.now(timezone.utc)
    by_line = {}
    for q in p.get('sportsbook_quotes') or []:
        try:
            if q.get('side') != p.get('side') or datetime.fromisoformat(q['expires_at']) <= now: continue
            by_line.setdefault(float(q['line']), []).append(q)
        except (KeyError, TypeError, ValueError):
            continue
    rows = []
    for line, quotes in sorted(by_line.items()):
        books = ', '.join(f'{escape(str(q.get("bookmaker")))} {float(q["decimal_odds"]):.2f}' for q in quotes if q.get('decimal_odds'))
        implied = [q['implied_probability'] for q in quotes if isinstance(q.get('implied_probability'), (int, float))]
        model = next((q['model_chance'] for q in quotes if isinstance(q.get('model_chance'), (int, float))), None)
        detail = []
        if model is not None: detail.append(f'model {model*100:.1f}% at this line')
        if implied: detail.append(f'book implied {min(implied)*100:.1f}%' + (f'–{max(implied)*100:.1f}%' if max(implied) != min(implied) else ''))
        fetched = next((q['fetched_at'] for q in quotes if q.get('fetched_at')), None)
        if fetched: detail.append('prices as of ' + escape(time_label(fetched)))
        rows.append(f'<b>Sportsbook: {escape(str(p.get("side", "")).title())} {line:g}</b> — {books}'
                    + (f' ({"; ".join(detail)})' if detail else ''))
    return rows


def frame(title, content, token='', when=None):
    from .email_theme import premium_frame
    return premium_frame(title, content, token=token, when=when)



class SportsReports:
    def __init__(self, root, getter=None,ledger=None,channel='report'):
        self.root = Path(root)
        self.getter = getter or self._get
        self.ledger,self.channel=ledger,channel

    def snapshot(self,sport,day,league=None):
        if sport not in SPORTS:raise ValueError('Unsupported sport')
        params={'date':day}
        if league is not None:params['league']=league
        payload,code=self.getter('/api/dashboard/'+sport,params)
        if code==200 and isinstance(payload,dict):
            from .data_quality import guard
            payload={**payload,'slate':[guard(r) for r in payload.get('slate') or []]}
        if self.ledger is not None and code==200:
            import sqlite3
            from datetime import timezone
            from .reliability import atomic_json
            status='healthy'
            try:self.ledger.record_snapshot(sport,payload,self.channel)
            except (OSError,ValueError,TypeError,sqlite3.Error):status='error'
            try:atomic_json(self.ledger.path.with_suffix('.health.json'),{'at':datetime.now(timezone.utc).isoformat(),'state':status})
            except OSError:pass  # Diagnostic disk trouble cannot break report delivery.
        return payload,code

    @staticmethod
    def _get(path, params):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=build_opener(ProxyHandler({}),NoRedirect())
        try:
            response=opener.open(Request('http://127.0.0.1:5001'+path+'?'+urlencode(params)),timeout=25)
        except HTTPError as error:
            response=error
        with response:
            raw=response.read(2_000_001);code=response.code
        if len(raw) > 2_000_000:
            raise ValueError('Oversized local response')
        return json.loads(raw), code

    def identity(self):
        data, code = self.getter('/api/local-runtime', {})
        if code != 200 or data.get('app') != 'ScorPred' or Path(data.get('root', '')).resolve() != self.root.resolve():
            raise ValueError('Local Scorpred identity could not be verified')
        return data

    def nba_comparison(self, card, day):
        try:
            payload,code=self.getter(f'/api/nba/matches/{card["matchId"]}/context',{'date':day})
            result=payload.get('result') or {}
            if code!=200 or not result.get('teams'):raise ValueError()
            rows=[]
            for side in ('home','away'):
                team=result['teams'].get(side) or {}; name=(team.get('team') or {}).get('name','Unknown')
                for label,key in (('Current season','current'),('Previous season','previous')):
                    stats=team.get(key) or {}
                    rows.append('<tr>'+''.join('<td style="padding:8px;border-bottom:1px solid #dbe8f1">'+escape(str(v))+'</td>' for v in
                        (name,label,stats.get('games',0),f'{stats.get("wins",0)}–{stats.get("losses",0)}',stats.get('points_for'),stats.get('points_against')) )+'</tr>')
            return '<h3>Matchup comparison</h3><table style="width:100%;font-size:13px"><tr><th>Team</th><th>Sample</th><th>Games</th><th>W–L</th><th>Scored/game</th><th>Allowed/game</th></tr>'+''.join(rows)+'</table><p><small>Descriptive context only. Games precede tipoff by at least 24 hours; retrospective archives do not prove pregame availability. This table does not raise the model probability.</small></p>'
        except Exception:
            return '<p><small>Matchup comparison unavailable; no statistics invented.</small></p>'

    def build(self, sport='all', props=False, day=None):
        deadline=time.monotonic()+180
        self.identity()
        day = day or datetime.now(NY).date().isoformat()
        if sport not in (*SPORTS, 'all'):
            raise ValueError('Unsupported sport')
        selected = SPORTS if sport == 'all' else (sport,)
        partitions = [(s, {'date': day, **({'league': league} if league else {})})
                      for s in selected for league in ((39, 78, 140) if s == 'soccer' else (None,))]
        def read(part):
            s, params = part
            try:
                if time.monotonic()>deadline:raise TimeoutError()
                data, code = self.snapshot(s,params['date'],params.get('league'))
                # One bounded retry lets a provider's first analysis warm up.
                if time.monotonic()<deadline and (data.get('refresh_pending') or (data.get('slate') and all(not r.get('pregameAvailable') for r in data['slate']))):
                    data, code = self.snapshot(s,params['date'],params.get('league'))
                return s, data, code
            except ImportError:
                raise  # a broken install must fail loudly, not email an empty report
            except Exception:
                return s, {'error':'Local analysis unavailable or timed out.'}, 503
        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(read, partitions))
        if sport=='nba' and props:
            # Use the same two-sided saved forecasts as chat, rather than the
            # legacy top-five list that can contain only unders.
            try:
                brief,code=self.getter('/api/nba/props/brief',{'date':day})
                result=brief.get('result',{});choices=result.get('choices')
                if code==200 and isinstance(choices,dict) and all(isinstance(choices.get(k),list) for k in ('over','under')):
                    from .sports_chat import compact_insights
                    _,payload,status=snapshots[0]
                    summary=compact_insights('nba',day,payload,status)
                    # Email has its own controls; remove chat command hints.
                    summary='\n'.join(line for line in summary.splitlines() if not line.startswith('Ask '))
                    parts=['<p>'+escape(summary).replace('\n','<br>')+'</p>'];count=0
                    for side in ('over','under'):
                        parts.append('<h2>'+side.upper()+'S to review</h2>')
                        for p in choices[side]:
                            parts.append('<p><b>'+escape(f"{player_label(p)} {side.upper()} {p['line']:g} {p['label']}")+
                                '</b> — '+f"{100*p['probability']:.1f}% unvalidated model estimate.</p>")
                            count+=1
                        if not choices[side]:parts.append('<p>No saved forecast favouring this side is available.</p>')
                    parts.append('<p><small>'+escape(result['notice'])+'</small></p>')
                    return {'title':f'NBA summary, overs and unders for {day}.','content':''.join(parts),'date':day,'count':count}
            except (KeyError,ValueError,TypeError,OSError):pass
        sections, count, seen = [], 0, set()
        for s in selected:
            cards, notes = [], []
            for kind, data, code in snapshots:
                if kind != s: continue
                if code != 200 or data.get('error') or data.get('refresh_pending'):
                    notes.append(str(data.get('error') or 'Analysis still warming up.'))
                for row in data.get('slate') or []:
                    key = (s, row.get('matchId'))
                    if key not in seen:
                        cards.append(row); seen.add(key)
            sections.append(f'<h2 style="border-bottom:2px solid #dbe8f1;padding-bottom:8px">{s.upper()}</h2>')
            qualified = [r for r in cards if r.get('pregameAvailable') and (r.get('action') in ('BET','CONSIDER') or r.get('researchPick'))]
            qualified.sort(key=lambda r: -(r.get('winProbability') or 0))
            for r in qualified:
                prob = r.get('winProbability')
                measure = f'{prob}% experimental model win estimate' if prob is not None else f'{r.get("scenarioShare")}% scoring-scenario share (not win probability)' if r.get('scenarioShare') is not None else 'Model measure unavailable'
                sections.append('<div style="background:#f4f8fb;border-radius:12px;padding:16px;margin:12px 0">'
                    f'<b>{escape(str(r.get("side")))}</b> · {escape(str(r.get("pickLabel") or r.get("action")))}<br>{escape(str(r.get("matchup")))}<br>'
                    f'{escape(time_label(r.get("matchDate")))} · {escape(measure)}<p>{escape(str(r.get("reason", "")))}</p>'
                    f'<small>Source observed: {escape(str((r.get("dataFreshness") or {}).get("observed_at", "unknown")))}</small></div>')
                count += 1
                if s=='nba' and count<=4 and time.monotonic()<deadline:sections.append(self.nba_comparison(r,day))
            if not qualified:
                sections.append('<p>No fresh pregame model pick is available yet. Missing or expired model evidence stays on WAIT.</p>')
            skipped = len(cards)-len(qualified)
            if skipped: sections.append(f'<p style="color:#526576">{skipped} other games: SKIP, started, or analysis unavailable.</p>')
            waits=sorted({reason for r in cards for reason in (r.get('dataQuality') or {}).get('reasons',[])})
            if waits:sections.append('<p>Unverified inputs: '+escape('; '.join(waits))+'. Provisional research picks are not verified betting edges.</p>')
            if props:
                # Read at most four upcoming games per sport, including no-bet winner games.
                candidates = [r for r in cards if r.get('pregameAvailable')][:4]
                picks, below = [], []
                for card in candidates:
                    if time.monotonic()>deadline:
                        notes.append('Report time budget reached. Remaining props were not checked.');break
                    try:
                        data, code = self.getter(f'/api/{s}/matches/{card["matchId"]}/props', {'date': day})
                        result = data.get('result') or {}
                        if code != 200:
                            notes.append(str(data.get('error') or 'Props unavailable.')); continue
                        roster = result.get('roster_verification') or {}
                        if roster.get('status') != 'verified':
                            notes.append('Props withheld: current roster identity not verified.'); continue
                        for pick in result.get('picks') or []:
                            if isinstance(pick.get('chance'), (float, int)) and pick['chance'] >= .6:
                                picks.append({**pick, 'matchup':card.get('matchup'), 'kickoff':card.get('matchDate'),
                                              'source_freshness':pick.get('data_freshness') or result.get('dataFreshness') or result.get('data_freshness') or {},
                                              'match_freshness':card.get('dataFreshness') or {},
                                              'roster_source':roster})
                            elif isinstance(pick.get('chance'), (float, int)):below.append(pick['chance'])
                    except Exception:
                        notes.append('Props unavailable or timed out for one game.')
                picks.sort(key=lambda p: -(p.get('edge') or 0))
                sections.append('<h3>Player-prop model leans</h3>')
                unique = set()
                for p in picks:
                    player = (p.get('name'),p.get('team'))
                    if player in unique: continue
                    unique.add(player)
                    line = p.get('line_source', 'historical')
                    books = sportsbook_lines(p)
                    source = ('Sportsbook line (experimental comparison)' if line == 'sportsbook' else
                              ('Model target' if line == 'model_target' else 'Historical reference line') + ('; real book lines below' if books else '; no verified offer'))
                    sample = f' · Sample: {escape(str(p["games"]))} games' if p.get('games') else ''
                    sections.append('<div style="border:1px solid #dbe8f1;border-radius:12px;padding:16px;margin:10px 0">'
                        f'<b>{escape(str(p.get("name")))}</b> · {escape(str(p.get("team")))}<br>'
                        f'{escape(str(p.get("side", "")).title())} {escape(str(p.get("line")))} {escape(str(p.get("label")))}<br>'
                        f'Projection: {escape(str(p.get("projection")))} · Model estimate: {p["chance"]*100:.1f}%<br>'
                        + ''.join(row + '<br>' for row in books) +
                        f'<small>{escape(source)}{sample}<br>{escape(str(p.get("matchup")))} · {escape(time_label(p.get("kickoff")))}<br>'
                        f'Source observed: {escape(str(p["source_freshness"].get("observed_at", "unavailable")))}; '
                        f'expires: {escape(str(p["source_freshness"].get("expires_at", "unavailable")))}<br>'
                        f'Match snapshot observed: {escape(str(p["match_freshness"].get("observed_at", "unavailable")))}<br>'
                        f'Roster source observed: {escape(str(p["roster_source"].get("observed_at", "unavailable")))}</small></div>')
                    context=p.get('matchup_context') or {}
                    if context:
                        factor=context.get('rule_opponent_multiplier')
                        change=f'{(factor-1)*100:+.1f}%' if isinstance(factor,(float,int)) else 'unavailable'
                        sections.append('<p style="font-size:13px;color:#526576">'
                            f'Opponent: {escape(str(context.get("opponent_name", "unknown")))}. '
                            f'Opponent allowed {escape(str(context.get("opponent_allowed_per_game")))} team {escape(str(p.get("label")))} per game '
                            f'over {escape(str(context.get("opponent_sample_games",0)))} observed games; sample baseline {escape(str(context.get("sample_league_allowed_per_game")))}. '
                            f'Rule-baseline matchup adjustment: {escape(change)}. '
                            f'Player vs this opponent: {escape(str(context.get("player_vs_opponent_games",0)))} games, average {escape(str(context.get("player_vs_opponent_average") if context.get("player_vs_opponent_average") is not None else "unavailable"))}. '
                            f'Projected minutes: {escape(str(p.get("minutes","unknown")))}. '
                            f'Final projection: {escape(str(context.get("final_projection_source","unknown")))}. '
                            'Team totals are not a verified individual defensive matchup.</p>')
                    elif s=='nba':sections.append('<p><small>Detailed player matchup evidence unavailable in this snapshot; no extra adjustment assumed.</small></p>')
                    count += 1
                    if len(unique) >= 8: break
                if not unique and below:
                    sections.append(f'<p>{len(below)} prop estimates were checked; none reached the 60% model threshold '
                                    f'(best {max(below)*100:.1f}%). No lean is shown rather than a weak one.</p>')
                elif not unique: sections.append('<p>No prop estimates with verified roster identity and complete source evidence are available in the checked games.</p>')
                sections.append('<p><small>Props check covers up to four upcoming games per sport. Percentages are model estimates, not measured accuracy. Ranking is the model uplift over recent player history, not proven sportsbook value.</small></p>')
            for note in dict.fromkeys(notes): sections.append(f'<p style="color:#865900">{escape(note)}</p>')
        summary=(f'<div style="margin:20px 0;padding:18px;background:#edf1e8;border:1px solid #d8e1cf;border-radius:12px">'
                 f'<b>{count} research entries</b> · {len(selected)} sport{ "s" if len(selected)!=1 else ""}<br>'
                 f'<span style="font-size:12px;color:#526576">Report date {escape(day)} · Picks and prop estimates remain separate below.</span></div>')
        return {'title':f"here are your {sport.upper() if sport != 'all' else 'all-sports'} picks{' and props' if props else ''} for {day}.",
                'content':summary+''.join(sections), 'count':count, 'date':day}


def time_label(raw):
    try:
        return datetime.fromisoformat(str(raw).replace('Z','+00:00')).astimezone(NY).strftime('%a %I:%M %p ET')
    except (ValueError, TypeError):
        return 'Start time unavailable'
