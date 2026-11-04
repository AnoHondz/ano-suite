import { useState } from 'react';
import { useFetch } from '../hooks/useFetch';
import type { Decision } from './DecisionCard';
import { calendarDate } from '../navigation';
import { ScoreProjection } from './ScoringResearch';

interface Metrics { games: number; log_loss: number; brier: number; accuracy: number; points_mae?: number }
interface Study { available: boolean; research_enabled: boolean; report?: {
  version: string; evaluated_games: number; conclusion: string;
  metrics: Record<string, Metrics>; coverage: Record<string, { early_games: number; winner_rule_gate: number; research_available: number }>;
} }
interface Preview { result: { version: string; notice: string; home_probability: number; away_probability: number;
  projected_score: { home: number; away: number }; intervals: Record<string, { low: number; high: number }> } }

export default function NBAEarlySeasonResearch({ decision }: { decision: Decision }) {
  const { data } = useFetch<Study>('/api/nba/research/early-season');
  const [requested, setRequested] = useState(false);
  const day = decision.slateDate || (decision.matchDate ? calendarDate(decision.matchDate) : '');
  const preview = useFetch<Preview>(requested && data?.research_enabled
    ? `/api/nba/research/early-season/matches/${encodeURIComponent(decision.matchId || '')}?date=${day}` : null);
  if (!data?.available || !data.report) return null;
  const report = data.report;
  const guarded = Object.entries(report.coverage).filter(([season]) => season !== '2023').reduce((sum, [, row]) => sum + row.early_games - row.winner_rule_gate, 0);
  return <details className="card" aria-label="NBA early-season research study"><summary>Early-season research · {report.evaluated_games} games tested</summary>
    <p className="research-badge">Research only · production predictions unchanged</p>
    <p className="quiet-label">The study starts with previous-season scoring and conceding, then adds current observations over six games. It produced estimates for {guarded} tested games that failed the former two-game availability requirement.</p>
    <div className="table-scroll"><table className="evaluation-table"><caption>Chronological historical results · not prospective accuracy</caption>
      <thead><tr><th>Method</th><th>Games</th><th>Log loss ↓</th><th>Brier ↓</th><th>Winner accuracy</th></tr></thead>
      <tbody>{Object.entries(report.metrics).map(([name, metric]) => <tr key={name}><td>{name === 'blend' ? 'Gradual season blend' : name === 'home_prior' ? 'Home baseline' : 'Previous season only'}</td><td>{metric.games}</td><td>{metric.log_loss.toFixed(3)}</td><td>{metric.brier.toFixed(3)}</td><td>{(metric.accuracy * 100).toFixed(1)}%</td></tr>)}</tbody>
    </table></div>
    <p className="coverage-note">{report.conclusion}</p>
    <p className="quiet-label">The combined result includes games with three to five current observations. It does not prove improvement for every smaller-sample group or justify simply removing either minimum.</p>
    <p className="quiet-label">Original historical pregame receipt times are unverified. No roster or injury adjustment. Prospective validation is still required. Version: {report.version}.</p>
    {data.research_enabled ? <>
      <button className="text-button" onClick={() => setRequested(true)}>Preview experimental estimate</button>
      {preview.loading && <p role="status">Loading separate research preview…</p>}
      {preview.error && <p role="status">Research preview unavailable: {preview.error}</p>}
      {preview.data?.result && <div className="scoring-research"><p className="section-label">Experimental preview · never a production pick</p>
        <ScoreProjection home={preview.data.result.projected_score.home} away={preview.data.result.projected_score.away} teamA={decision.teamA || 'Home'} teamB={decision.teamB || 'Away'} sport="nba" />
        <p className="quiet-label">Experimental home / away probabilities: {(preview.data.result.home_probability * 100).toFixed(1)}% / {(preview.data.result.away_probability * 100).toFixed(1)}%.</p>
        <p className="coverage-note">{preview.data.result.notice}</p>
      </div>}
    </> : <p className="quiet-label">Experimental previews are disabled by default. These historical results do not enable a production probability.</p>}
  </details>;
}
