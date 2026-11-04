import { useFetch } from '../hooks/useFetch';
import RequestError from './RequestError';
import type { Decision, SourceFreshness } from './DecisionCard';
import ScoringResearch, { ScoreProjection } from './ScoringResearch';
import { numericFields, record, validResearch, type ResearchForecast } from '../scoringContracts';
import { calendarDate, calendarFormat } from '../navigation';
import { useNow } from '../hooks/useNow';
import { marketAt, pregameFailure } from '../decisionLifecycle';

interface Analysis {
  data_freshness?: SourceFreshness;
  projected_score: { home: number; away: number };
  probabilities: { home: number; draw: number; away: number };
  samples: { home: number; away: number };
  notice: string;
  market?: Decision['market'];
  data_source?: string;
  research?: ResearchForecast;
}

export default function NFLAnalysis({ decision }: { decision: Decision }) {
  const now = useNow();
  const parentFailure = pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now);
  const params = new URLSearchParams();
  if (decision.slateDate || decision.matchDate) params.set('date', decision.slateDate || calendarDate(decision.matchDate!));
  const { data, loading, error, retry, status } = useFetch<{ result: Analysis }>(decision.matchId && !parentFailure
    ? `/api/nfl/matches/${encodeURIComponent(decision.matchId)}/analysis?${params}` : null);
  const result = data?.result;
  const unavailable = parentFailure || pregameFailure(decision.matchDate, decision.eventStatus, result?.data_freshness, now);
  const market = marketAt(result?.market, now);
  const valid = record(result) && numericFields(result.projected_score, ['home', 'away']) && numericFields(result.probabilities, ['home', 'draw', 'away']) && numericFields(result.samples, ['home', 'away']) && validResearch(result.research);
  const teams = [decision.teamA || 'Home', decision.teamB || 'Away'];
  const source = result?.data_source === 'nflverse' ? 'nflverse · local historical store' : result?.data_source === 'espn' ? 'ESPN public schedule fallback' : result?.data_source || 'Source unavailable';
  return <section className="section score-predictor" aria-label="Scoring predictor" id="scoring-analysis">
    <div className="section-heading-row"><div><p className="page-eyebrow">DESCRIPTIVE ANALYSIS</p><h2 className="section-title">Recent scoring scenarios</h2></div><span className="research-badge">Experimental</span></div>
    <p className="coverage-note">These are descriptive scenarios, not calibrated win probabilities. A tied scenario does not estimate a final-game tie probability.</p>
    {unavailable && <p className="coverage-note" role="status">{unavailable}</p>}
    {!unavailable && loading && <div className="card"><p className="quiet-label" role="status">Loading recent scoring records…</p></div>}
    {!unavailable && error && (status === 422 ? <p className="coverage-note" role="status">{error}</p> : <RequestError message={error} onRetry={retry} />)}
    {!unavailable && data && !valid && <RequestError message="The scoring service returned incomplete data. Please try again." onRetry={retry} />}
    {!unavailable && result && valid && <>
      <div className="grid-2">
        <section className="card scenario-panel" aria-label="Recent scoring scenario shares"><h3 className="panel-title">Share of recent-scoring scenarios</h3>
          <div className="scenario-bars">{(['home', 'draw', 'away'] as const).map((key, index) => {
            const value = result.probabilities[key];
            const label = index === 1 ? 'Tied scenarios' : `${teams[index === 0 ? 0 : 1]} ahead`;
            return <div className="scenario-row" key={key}><div><span>{label}</span><strong>{value.toFixed(1)}%</strong></div><div className="measure-track" role="img" aria-label={`${label}: ${value.toFixed(1)}% descriptive scenario share`}><span style={{ width: `${value}%` }} /></div></div>;
          })}</div><p className="quiet-label">{result.notice}</p>
        </section>
        <section className="card scenario-panel"><h3 className="panel-title">Recent-scoring mean points</h3>
          <p className="quiet-label">Projected points: {teams[0]} {result.projected_score.home.toFixed(1)} – {result.projected_score.away.toFixed(1)} {teams[1]}</p>
          <ScoreProjection home={result.projected_score.home} away={result.projected_score.away} teamA={teams[0]} teamB={teams[1]} sport="nfl" />
          <p className="quiet-label">Descriptive averages from the current dashboard method. Separate trained scoring research appears below.</p>
        </section>
      </div>
      <section className="card"><div className="section-heading-row"><h3 className="panel-title">Team comparison</h3><span className="quiet-label">Available source statistics</span></div>
        <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">NFL recent scoring comparison</caption><thead><tr><th scope="col">Metric</th><th scope="col">{teams[0]}</th><th scope="col">{teams[1]}</th></tr></thead><tbody>
          <tr><th scope="row">Completed games sampled</th><td>{result.samples.home}</td><td>{result.samples.away}</td></tr>
          <tr><th scope="row">Scenario mean points</th><td>{result.projected_score.home.toFixed(1)}</td><td>{result.projected_score.away.toFixed(1)}</td></tr>
        </tbody></table></div><p className="score-model-note">Up to eight recent games per team. Scenario mean points combine both teams’ scoring and conceding; they are not observed offensive averages. Efficiency, passing, and rushing metrics are not supplied by this analysis endpoint.</p>
        <p className="quiet-label mt-3">Source: {source}.</p>
      </section>
      {market && <section className="card"><div className="section-heading-row"><h3 className="panel-title">Market context</h3><span className="quiet-label">{market.bookmaker}</span></div>
        <div className="trust-stat"><span className="trust-stat-label">Moneyline · decimal odds</span><span className="trust-stat-value">{market.home_decimal?.toFixed(2) ?? 'Unavailable'} / {market.away_decimal?.toFixed(2) ?? 'Unavailable'}</span></div>
        {market.total != null && <div className="trust-stat"><span className="trust-stat-label">Market total</span><span className="trust-stat-value">{market.total} points</span></div>}
        {market.home_spread != null && <div className="trust-stat"><span className="trust-stat-label">Home spread</span><span className="trust-stat-value">{market.home_spread > 0 ? '+' : ''}{market.home_spread}</span></div>}
        <p className="score-model-note">Quote updated {calendarFormat(market.updated_at, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET. Prices do not change the experimental scoring scenarios.</p>
      </section>}
      <section className="card section" aria-label="Experimental NFL scoring research"><h3 className="panel-title">Trained scoring forecast · separate research</h3>
        <p className="quiet-label">This experimental point forecast is distinct from the dashboard’s recent-scoring scenarios and from the winner model.</p>
        {result.research?.available && result.research.projected_score && <><p className="quiet-label">Research mean points: {teams[0]} {result.research.projected_score.home.toFixed(1)} – {result.research.projected_score.away.toFixed(1)} {teams[1]}</p><ScoreProjection home={result.research.projected_score.home} away={result.research.projected_score.away} teamA={teams[0]} teamB={teams[1]} sport="nfl" /></>}
        <ScoringResearch forecast={result.research} teamA={teams[0]} teamB={teams[1]} sport="nfl" />
      </section>
    </>}
  </section>;
}
