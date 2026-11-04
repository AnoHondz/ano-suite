"""NBA points research from verified quotes; no AI or wager execution."""
from datetime import UTC, datetime
import math
from services.prediction_lifecycle import parse_kickoff, freshness_state
from services.prop_market_value import compare_decimal_market
from services.nba_opportunity import stable_role

VERSION = 'nba-prop-research-v2'


def forecast_details(pick, quote, comparison, kickoff):
    """Expose estimates on WAIT without disguising reference lines as book offers."""
    if comparison and quote:
        line=quote.get('line');over=comparison['over']['model_win_probability']
        under=comparison['under']['model_win_probability'];push=comparison['push_probability']
        basis='supplied sportsbook line'
    else:
        line=pick.get('line');chance=pick.get('chance')
        if (isinstance(line,bool) or not isinstance(line,(int,float)) or not math.isfinite(line) or line%1!=.5
                or isinstance(chance,bool) or not isinstance(chance,(int,float)) or not math.isfinite(chance)
                or not 0<=chance<=1 or pick.get('side') not in ('over','under')):return None
        over=chance if pick['side']=='over' else 1-chance
        under=1-over;push=0;basis='historical reference line, not a verified bet365 offer'
    team=pick.get('team_id',pick.get('team'));start=parse_kickoff(kickoff)
    history=[]
    for item in (pick.get('opportunity_evidence') or {}).get('recent_games',[]):
        value=item.get(pick.get('market'));when=parse_kickoff(item.get('date'))
        if (team is not None and str(item.get('team_id'))==str(team) and start and when and when<start
                and isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0):
            history.append((when,value))
    history.sort();history=history[-10:];count=len(history)
    over_hits=sum(value>line for _,value in history);under_hits=sum(value<line for _,value in history)
    return {'line':line,'line_basis':basis,'over_probability':over,'under_probability':under,'push_probability':push,
        'model_direction':'OVER' if over>under else 'UNDER' if under>over else 'EVEN',
        'historical':{'games':count,'over_hits':over_hits,'under_hits':under_hits,'pushes':count-over_hits-under_hits,
            'over_hit_rate':over_hits/count if count else None,'under_hit_rate':under_hits/count if count else None,
            'first_game':history[0][0].isoformat() if count else None,'last_game':history[-1][0].isoformat() if count else None,
            'scope':'Last up to 10 recorded current-team appearances before this game; may span seasons. Pushes remain in the denominator.'},
        'notice':'Unvalidated model estimates. Historical hit rates are observations, not future win probabilities. WAIT warnings still apply.'}


def projected_range(ladder):
    if not isinstance(ladder,dict) or ladder.get('start')!=0 or isinstance(ladder.get('start'),bool):return None
    values=ladder.get('over')
    if not isinstance(values,list) or not values:return None
    if any(isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1 for p in values):return None
    if any(a<b for a,b in zip(values,values[1:])):return None
    quantiles=[next((i for i,p in enumerate(values) if 1-p+1e-9>=q),None) for q in (.1,.9)]
    return {'p10':quantiles[0],'p90':quantiles[1],
            'basis':'Raw count-model central 80% range; coverage not yet validated. Missing endpoints exceed the stored distribution.'}


def points_candidates(projection, *, rule_shrink, learned_version=None, learned_shrink=None, markets=('points',)):
    """Every eligible points forecast, independent of legacy top-five rankings."""
    candidates=[]
    for player in projection.get('players',[]):
        for market in player.get('markets',[]):
            if market.get('market') not in markets:continue
            shrink=learned_shrink if market.get('model')==learned_version and learned_shrink is not None else rule_shrink
            probability=market.get('probability')
            if (isinstance(probability,bool) or not isinstance(probability,(int,float))
                    or not math.isfinite(probability) or not 0<=probability<=1):continue
            if not isinstance(shrink,(int,float)) or not 0<=shrink<=1:continue
            candidates.append({**market,'player_id':player.get('id'),'name':player.get('name'),
                'team':player.get('team'),'team_id':player.get('team'),'position':player.get('position'),
                'chance':round(.5+shrink*(probability-.5),4)})
    return candidates


def build(result, *, game_id, kickoff, injury_source=None, now=None, markets=('points',)):
    now = now or datetime.now(UTC)
    start = parse_kickoff(kickoff)
    rows = []
    for pick in result.get('picks', []):
        if pick.get('market') not in markets:
            continue
        quotes = pick.get('sportsbook_quotes') or []
        for quote in quotes or [None]:
            reasons = []
            warnings=[]
            role=stable_role(pick,kickoff)
            environment=result.get('game_environment') or {}
            if environment.get('status')!='available' or freshness_state(environment.get('source'),now=now)!='fresh':
                reasons.append('Current game-environment evidence is unavailable or stale')
            if environment.get('lineup_status') not in ('confirmed','projected'):
                if role['supported']:
                    warnings.append('Full lineup is unverified; this player has stable recent current-team minutes. Late rotation changes remain possible.')
                else:reasons.append('Starting lineup evidence is unverified')
            if not start or now >= start:
                reasons.append('Game is not verified pregame')
            if not injury_source or freshness_state(injury_source, now=now) != 'fresh':
                reasons.append('Current injury evidence is unavailable or stale')
            roster = result.get('roster_verification')
            if not roster or roster.get('status') != 'verified' or freshness_state(roster, now=now) != 'fresh':
                reasons.append('Current roster identity is unverified or stale')
            probability = quote.get('model_chance') if quote else None
            expiry = parse_kickoff(quote.get('expires_at')) if quote else None
            if not quote or not expiry or expiry <= now:
                reasons.append('No fresh, matched sportsbook quote')
            if isinstance(probability, bool) or not isinstance(probability, (int,float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                reasons.append('No supported model distribution at the quoted line')
                probability = None
            price = quote.get('decimal_odds') if quote else None
            valid_price = isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price) and price > 1
            if not valid_price:
                reasons.append('Exact sportsbook price unavailable')
            ev = probability * price - 1 if valid_price and probability is not None else None
            push=quote.get('model_push_probability',0) if quote else 0
            comparison = None
            if probability is not None and quote:
                try:
                    if quote.get('side') not in ('over', 'under') or float(quote['line']) % 1 not in (0,.5):
                        raise ValueError('Unsupported side or push settlement')
                    if float(quote['line'])%1==0 and 'model_push_probability' not in quote:raise ValueError('Integer-line push probability required')
                    p_over = probability if quote['side'] == 'over' else 1 - probability - push
                    comparison = compare_decimal_market(quote.get('over_decimal'), quote.get('under_decimal'), p_over, 1-p_over-push,push)
                    ev=comparison[quote['side']]['expected_profit_per_unit']
                except (ValueError, TypeError, KeyError):
                    reasons.append('Both prices and a supported win/loss/push distribution are required for two-sided value analysis')
            # Historical-target backtests do not validate probability at book
            # lines. Do not invent a release gate or a calibrated confidence.
            decision = 'WAIT' if reasons else 'PASS'
            model=pick.get('model') or 'minutes-rate-count baseline'
            calibration_key=model if pick.get('market')=='points' else model+':'+pick['market']
            if quote and quote.get('market','').endswith('_alternate'):calibration_key+=':alternate'
            if quote and isinstance(quote.get('line'),(int,float)) and quote['line']%1==0:calibration_key+=':integer'
            validation=(result.get('validation_by_model') or {}).get(calibration_key,{})
            model_lean=None
            if not reasons and comparison:
                options=[side for side in ('over','under') if comparison[side]['expected_profit_per_unit']>=.03
                         and comparison[side]['model_win_probability']/(1-push)-comparison[side]['market_no_vig_probability']>=.02]
                if options:
                    lean=max(options,key=lambda side:comparison[side]['expected_profit_per_unit'])
                    model_lean={'side':lean.upper(),'line':quote['line'],'decimal_odds':comparison[lean]['decimal_odds'],
                                'validated':validation.get('validated') is True,
                                'notice':'Model lean only. Calibration and profitability at these prices are not established.'}
            if not reasons:
                if validation.get('validated') is not True:
                    reasons.append('Probability calibration at sportsbook lines is not yet validated')
                elif comparison:
                    sides=[side for side in ('over','under') if comparison[side]['expected_profit_per_unit']>=.03
                           and comparison[side]['model_win_probability']/(1-push)-comparison[side]['market_no_vig_probability']>=.02]
                    if sides:
                        decision=max(sides,key=lambda side:comparison[side]['expected_profit_per_unit']).upper()
                        side=decision.lower()
                        probability=comparison[side]['model_win_probability']
                        ev=comparison[side]['expected_profit_per_unit']
                        quote={**quote,'side':side,'decimal_odds':comparison[side]['decimal_odds'],
                               'model_chance':probability}
                        reasons.append('Research thresholds passed at the verified current price; outcome remains uncertain')
                    else:reasons.append('Neither side passed the research price thresholds')
            opportunity=pick.get('opportunity_evidence') or {}
            recent=(opportunity.get('windows') or {}).get('5') or {}
            explanation=[f"Model projection: {pick.get('projection','unavailable')} {pick.get('market')}; historical minutes estimate: {pick.get('minutes','unavailable')}."]
            if recent.get('games'):
                explanation.append(f"Across {recent['games']} recent recorded appearances: {recent.get('mean_points')} points and {recent.get('mean_minutes')} minutes on average. These are observations, not a guarantee of current role.")
            explanation.extend(reasons)
            opponent=(pick.get('matchup_context') or {}).get('opponent_name')
            dependencies=[f"player:{pick.get('player_id')}"] if pick.get('player_id') else []
            dependencies.extend('team:'+str(team) for team in (pick.get('team'),opponent) if team)
            rows.append({'player':pick.get('name'), 'player_id':pick.get('player_id'),
                'team':pick.get('team'),'dependency_tags':dependencies,
                'explanation':explanation,'warnings':warnings,'role_evidence':role,'model_lean':model_lean,
                'forecast':forecast_details(pick,quote,comparison,kickoff),
                'calibration_validated':validation.get('validated') is True,'validation':validation,
                'market':pick.get('market'), 'calibration_key':calibration_key, 'decision':decision, 'reasons':reasons,
                'projected_value':pick.get('projection'),
                'projected_points':pick.get('projection') if pick.get('market')=='points' else None, 'expected_minutes':pick.get('minutes'),
                'projected_range':projected_range(pick.get('ladder')),
                'sample_games':pick.get('games'), 'quote':quote,
                'matchup_context':pick.get('matchup_context'),
                'opportunity_evidence':pick.get('opportunity_evidence'),
                'projection_model':pick.get('model') or 'minutes-rate-count baseline',
                'quoted_side_probability':probability,
                'expected_profit_per_unit':ev,
                'two_sided_value':comparison,
                'counterargument':'Minutes, role and shooting outcomes can differ from the historical model; an estimated positive return is not demonstrated value.',
                'lineup_status':environment.get('lineup_status','unverified'),
                'distribution':pick.get('ladder'),
                'range_notice':'The supplied distribution is bounded; no unsupported tail extrapolation is shown.'})
    return {'version':VERSION,'game_id':str(game_id),'recorded_at':now.isoformat(),
            'game_environment':result.get('game_environment'),
            'market_evidence':result.get('sportsbook'),
            'kickoff':start.isoformat() if start else None,'sport':'nba','market':'points' if markets==('points',) else 'multiple','markets':list(markets),
            'research_only':True,'rows':rows,'injury_source':injury_source,
            'status':'available' if rows else 'WAIT',
            'notice':'Research only. No bets placed. Missing data means WAIT; unvalidated pricing probabilities mean PASS.'}
