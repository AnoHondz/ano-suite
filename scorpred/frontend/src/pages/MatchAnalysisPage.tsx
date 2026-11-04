import { EmptyState, type Decision } from '../components/DecisionCard';
import ScorelinePredictor from '../components/ScorelinePredictor';
import NFLAnalysis from '../components/NFLAnalysis';
import NFLProps from '../components/NFLProps';
import InjuryImpact from '../components/InjuryImpact';
import SoccerProps from '../components/SoccerProps';
import NBAProps from '../components/NBAProps';
import NFLWinnerResearch from '../components/NFLWinnerResearch';
import { decisionAt } from '../decisionLifecycle';
import { useNow } from '../hooks/useNow';
import NBAContext from '../components/NBAContext';
import { MatchupStage } from '../components/immersive/CommandCenter';
import SavePick from '../components/SavePick';

function PredictionReason({ decision }: { decision: Decision }) {
  const reasons = (decision.reason || 'No explanation available.').split(/\s+\|\s+|(?<=[.!?])\s+(?=[A-Z])/).filter(Boolean);
  return <section className="card prediction-explanation" aria-label="Prediction explanation">
    <h2 className="section-title">{decision.side === 'Unavailable' ? 'Why unavailable' : 'Why this prediction'}</h2>
    <ul className="prediction-reasons">{reasons.slice(0, 4).map(reason => <li key={reason}>{reason}</li>)}</ul>
    <details className="prop-about"><summary>Full analysis & sources</summary>
      {reasons.slice(4).map(reason => <p key={reason}>{reason}</p>)}
      <p>Method: {decision.modelSource || 'Current dashboard method'}.</p>
      {decision.dataFreshness?.source && <p>Source: {decision.dataFreshness.source}.</p>}
      {decision.market && <p>{decision.market.bookmaker} · Decimal moneyline: {decision.market.home_decimal?.toFixed(2) ?? 'Unavailable'} / {decision.market.away_decimal?.toFixed(2) ?? 'Unavailable'}{decision.market.total != null ? ` · Total ${decision.market.total}` : ''}.</p>}
    </details>
  </section>;
}

export default function MatchAnalysisPage({ decision: rawDecision, sport = 'soccer', teamAId, teamBId, leagueId }: {
  decision: Decision | null;
  sport?: 'soccer' | 'nba' | 'nfl';
  teamAId?: number | string;
  teamBId?: number | string;
  leagueId?: number | string;
}) {
  const now = useNow();
  const decision = rawDecision ? decisionAt(rawDecision, now) : null;
  const matchup = decision?.matchup || 'Match Analysis';
  const teamAName = decision?.teamA || matchup.split(' vs ')[0] || 'Home';
  const teamBName = decision?.teamB || matchup.split(' vs ')[1] || 'Away';
  return <div className="page-stack match-analysis-page">
    {decision ? <>
      <MatchupStage sport={sport} decision={decision} primary />
      <SavePick decision={decision} />
      <div className="match-support-grid">
        <PredictionReason decision={decision} />
        {sport === 'nfl' ? <NFLProps decision={decision} /> : sport === 'nba' ? <NBAProps decision={decision} /> : <SoccerProps decision={decision} />}
      </div>
      {sport === 'nba' && <NBAContext decision={decision} />}
      <details className="card analysis-expansion"><summary>Injury reports</summary><InjuryImpact decision={decision} /></details>
      <details className="card analysis-expansion"><summary>Score forecast & analysis</summary>
      {sport === 'nfl' ? <><NFLAnalysis decision={decision} />
        <details className="prop-about"><summary>Research models</summary><NFLWinnerResearch /></details></>
        : <ScorelinePredictor sport={sport} teamAName={teamAName} teamBName={teamBName} teamAId={teamAId} teamBId={teamBId} leagueId={leagueId} kickoff={decision.matchDate} matchId={decision.matchId} eventStatus={decision.eventStatus} dataFreshness={decision.dataFreshness} />}
      </details>
    </> : <><h1 className="page-title">Match analysis</h1><EmptyState title="No match selected" body="Go to Soccer, NBA or NFL and click Analyze Matchup on any card." /></>}
  </div>;
}
