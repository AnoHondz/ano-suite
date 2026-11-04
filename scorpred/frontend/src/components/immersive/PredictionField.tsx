import { ArrowUpRight } from 'lucide-react';
import type { Decision } from '../DecisionCard';
import { DataBadge } from '../DecisionCard';
import { calendarFormat } from '../../navigation';
import LiteField from './LiteField';
import type { ArenaSport } from './SportsArena';

const percent = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100;

export default function PredictionField({ sport, decision, primary = false, onSelect }: {
  sport: ArenaSport; decision: Decision; primary?: boolean; onSelect?: (decision: Decision) => void;
}) {
  const home = decision.teamA || decision.matchup?.split(' vs ')[0] || 'Home team';
  const away = decision.teamB || decision.matchup?.split(' vs ')[1] || 'Away team';
  const matchup = decision.matchup || `${home} vs ${away}`;
  const [competition, venue] = (decision.support || '').split(' | ');
  const available = decision.side !== 'Unavailable';
  const nfl = sport === 'nfl';
  const probability = nfl ? decision.scenarioShare : decision.winProbability;
  const useProbability = percent(probability);
  const value = available ? useProbability ? probability : nfl ? undefined : decision.confidence : undefined;
  const measure = nfl ? 'Scenario share' : useProbability ? 'Win probability' : 'Rule confidence';
  const Heading = primary ? 'h1' : 'h2';
  const selectedSide = decision.side.replace(/ Win!?$/i, '');
  const draw = selectedSide.trim().toLowerCase() === 'draw';
  const probabilityLabel = draw && !nfl && useProbability ? 'Draw probability' : measure;
  return <section className={`matchup-stage prediction-field stage-${sport}`} aria-label={`${sport === 'soccer' ? 'Soccer' : sport.toUpperCase()} matchup prediction`}>
    <div className="prediction-meta"><span>{competition || sport.toUpperCase()}</span>
      <span>{decision.matchDate ? `${calendarFormat(decision.matchDate, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET` : 'Kickoff unavailable'}</span></div>
    <Heading className="prediction-matchup" aria-label={matchup}>
      <span className="prediction-team prediction-home">{decision.teamALogo && <img src={decision.teamALogo} alt="" />}<span><small>Home</small>{home}</span></span>
      <span className="prediction-versus">vs</span>
      <span className="prediction-team prediction-away"><span><small>Away</small>{away}</span>{decision.teamBLogo && <img src={decision.teamBLogo} alt="" />}</span>
    </Heading>
    <div className="prediction-pitch">
      <LiteField sport={sport} />
      <div className={`field-result${available ? '' : ' field-result-unavailable'}`} aria-label="Match prediction" aria-live="polite">
        <p className="field-result-label">{nfl ? 'Experimental scenario lean' : 'Prediction'}</p>
        <p className="field-pick">{available ? nfl && draw ? 'Tied scoring scenarios' : selectedSide : 'Prediction unavailable'}</p>
        {available && <div className="field-measure" role="img" aria-label={percent(value) ? `${value!.toFixed(1)}% ${probabilityLabel.toLowerCase()}` : `${probabilityLabel} unavailable`}>
          <strong>{percent(value) ? `${value!.toFixed(1)}%` : 'Unavailable'}</strong><span>{probabilityLabel}</span>
        </div>}
        {!available && <p className="field-unavailable-reason">{decision.reason}</p>}
        {nfl && <p className="field-method-note">Descriptive scenarios · not calibrated win probabilities</p>}
        {available && !nfl && !useProbability && <p className="field-method-note">{draw ? 'Draw' : 'Win'} probability unavailable</p>}
        {decision.dataFreshness?.warning && <p className="field-method-note" role="status">{decision.dataFreshness.warning}</p>}
        {decision.market?.home_spread != null && <p className="field-market">{decision.market.bookmaker} · {home} {decision.market.home_spread > 0 ? '+' : ''}{decision.market.home_spread}</p>}
      </div>
    </div>
    <div className="prediction-footer"><DataBadge label={decision.data} />
      <span className={`action-badge badge-${decision.action.toLowerCase()}`} aria-label={`Betting decision: ${decision.action}`}>{decision.action === 'SKIP' ? 'Skip bet' : decision.action === 'BET' ? 'Bet' : 'Consider'}</span>
      {venue && <span>{venue}</span>}
      {decision.dataFreshness?.observed_at && <span>Source observed {calendarFormat(decision.dataFreshness.observed_at, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET</span>}
      {onSelect && <button className="stage-action" type="button" onClick={() => onSelect(decision)}>Analyze matchup <ArrowUpRight size={15} /></button>}
    </div>
  </section>;
}
