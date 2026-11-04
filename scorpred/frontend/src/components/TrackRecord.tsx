import { ShieldCheck, Scale } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import RequestError from './RequestError';
import { CardSkeleton } from './DecisionCard';

interface SportRecord {
  graded: number;
  correct: number;
  accuracy: number | null;
  accuracy_95ci: [number, number] | null;
  pending: number;
  excluded: { recorded_after_kickoff: number; no_kickoff_time: number; preseason: number };
  sample_status: 'sufficient' | 'insufficient';
  probability: { scored: number; log_loss: number | null; brier: number | null };
  market: { games: number; model_log_loss: number | null; market_log_loss: number | null; model_minus_market: number | null };
  closing_line_value: { picks: number; mean: number | null; positive_share: number | null };
  score_forecast: { graded: number; team_error: number | null; total_error: number | null; exact_score_hits: number; exact_score_rate: number | null };
}

interface Score { a: number; b: number; likely?: { a: number; b: number } }

interface RecentPick {
  id: string;
  sport: 'soccer' | 'nba';
  kickoff: string;
  home: string;
  away: string;
  pick: string;
  pick_probability: number | null;
  predicted_score: Score | null;
  final_score: { a: number; b: number } | null;
  result: 'pending' | 'correct' | 'wrong';
}

interface Learning {
  active: boolean | null;
  picks: number | null;
  reason: string | null;
  params: { temperature: number; offset: number } | null;
}

interface MarketBenchmark {
  test_window: { start: string; end: string };
  matches: number;
  model_accuracy: number;
  market_accuracy: number;
  log_loss_gap: number;
  log_loss_gap_95ci: [number, number];
  model_pick_roi: number;
  model_pick_roi_95ci: [number, number];
  value_bet_roi: number;
  value_bet_roi_95ci: [number, number];
  value_bets: number;
  model_log_loss: number;
  previous_engine_log_loss: number;
}

interface TrackRecordData {
  minimum_sample: number;
  sports: Record<'soccer' | 'nba', SportRecord>;
  market_benchmark: MarketBenchmark | null;
  recent: RecentPick[];
  learning: { minimum_picks: number; sports: Record<'soccer' | 'nba', Learning> };
  notes: string[];
}

const pct = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const signedPct = (value: number) => `${value > 0 ? '+' : ''}${pct(value)}`;
const range = (values: [number, number], format: (value: number) => string) => `${format(values[0])} to ${format(values[1])}`;

function SportRow({ label, record, minimum }: { label: string; record: SportRecord; minimum: number }) {
  const excluded = record.excluded.recorded_after_kickoff + record.excluded.no_kickoff_time + record.excluded.preseason;
  return <tr>
    <th scope="row">{label}</th>
    <td>{record.graded ? `${record.correct} of ${record.graded}` : 'None yet'}</td>
    <td>{record.accuracy == null ? '—' : pct(record.accuracy)}</td>
    <td>{record.accuracy_95ci ? range(record.accuracy_95ci, value => pct(value, 0)) : '—'}</td>
    <td>{record.sample_status === 'sufficient' ? 'Sufficient' : `Needs ${minimum - record.graded} more`}</td>
    <td>{record.pending}</td>
    <td>{excluded}</td>
  </tr>;
}

function ProbabilityRow({ label, record }: { label: string; record: SportRecord }) {
  const { probability, market, closing_line_value: clv } = record;
  return <tr>
    <th scope="row">{label}</th>
    <td>{probability.log_loss == null ? '—' : `${probability.log_loss.toFixed(3)} (${probability.scored})`}</td>
    <td>{market.model_minus_market == null ? 'No prices yet'
      : `${Math.abs(market.model_minus_market).toFixed(3)} ${market.model_minus_market > 0 ? 'worse' : 'better'} (${market.games})`}</td>
    <td>{clv.mean == null ? 'No closing prices yet' : `${signedPct(clv.mean)} · ${pct(clv.positive_share ?? 0, 0)} beat the close (${clv.picks})`}</td>
  </tr>;
}

const RESULT_LABEL = { pending: 'Pending', correct: 'Correct', wrong: 'Wrong' } as const;

function predictedScore(pick: RecentPick) {
  const score = pick.predicted_score;
  if (!score) return '—';
  if (pick.sport === 'nba') return `${Math.round(score.a)}–${Math.round(score.b)}`;
  return `${score.a.toFixed(1)}–${score.b.toFixed(1)} xG${score.likely ? ` (likeliest ${score.likely.a}–${score.likely.b})` : ''}`;
}

function RecentPredictions({ picks }: { picks: RecentPick[] }) {
  return <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title">Recent predictions</h3><span className="quiet-label">Recorded before kickoff · predicted against what happened</span></div>
    {picks.length === 0 ? <p className="quiet-label">No verified pregame picks yet. Picks are recorded automatically every 30 minutes.</p>
      : <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Recent verified pregame predictions</caption>
        <thead><tr><th scope="col">Kickoff</th><th scope="col">Match</th><th scope="col">Pick</th><th scope="col">Predicted score</th><th scope="col">Final score</th><th scope="col">Result</th></tr></thead>
        <tbody>{picks.map(pick => <tr key={pick.id}>
          <td>{new Date(pick.kickoff).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
          <th scope="row">{pick.home} vs {pick.away}</th>
          <td>{pick.pick}{pick.pick_probability != null ? ` (${pct(pick.pick_probability, 0)})` : ''}</td>
          <td>{predictedScore(pick)}</td>
          <td>{pick.final_score ? `${pick.final_score.a}–${pick.final_score.b}` : '—'}</td>
          <td>{RESULT_LABEL[pick.result]}</td>
        </tr>)}</tbody>
      </table></div>}
  </div>;
}

function ScoreRow({ label, record, unit }: { label: string; record: SportRecord; unit: string }) {
  const score = record.score_forecast;
  return <tr>
    <th scope="row">{label}</th>
    <td>{score.graded || '—'}</td>
    <td>{score.team_error == null ? '—' : `${score.team_error.toFixed(2)} ${unit}`}</td>
    <td>{score.total_error == null ? '—' : `${score.total_error.toFixed(2)} ${unit}`}</td>
    <td>{score.exact_score_rate == null ? '—' : `${score.exact_score_hits} (${pct(score.exact_score_rate, 0)})`}</td>
  </tr>;
}

function LearningStatus({ learning }: { learning: TrackRecordData['learning'] }) {
  const line = (label: string, state: Learning) => state.active && state.params
    ? `${label}: correcting published probabilities (temperature ${state.params.temperature.toFixed(2)}, offset ${state.params.offset.toFixed(2)}).`
    : `${label}: ${state.reason || `waiting for ${learning.minimum_picks} graded picks`}`;
  return <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title">Learning from results</h3></div>
    <p className="quiet-label">The ratings update after every result. Separately, graded picks are checked daily for systematic over- or under-confidence; a correction is applied only when it improves predictions it was not fitted on.</p>
    <p>{line('Soccer', learning.sports.soccer)}</p>
    <p>{line('NBA', learning.sports.nba)}</p>
  </div>;
}

export default function TrackRecord() {
  const { data, loading, error, retry } = useFetch<TrackRecordData>('/api/performance/track-record');
  if (loading) return <CardSkeleton />;
  if (error || !data) return <RequestError message={error ?? 'The track record is unavailable.'} onRetry={retry} />;
  const { sports, market_benchmark: market, minimum_sample: minimum } = data;
  return <section className="section historical-performance" aria-label="Verified track record">
    <div className="section-heading-row"><div><p className="page-eyebrow"><ShieldCheck size={14} />VERIFIED TRACK RECORD</p>
      <h2 className="section-title">Picks graded against real results.</h2></div></div>
    <p className="page-description">Only picks recorded before kickoff count. Picks logged after a game started are excluded and listed below, so the record cannot be improved after the fact.</p>

    <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title">Rule-engine winner picks</h3><span className="quiet-label">{minimum} graded picks needed per sport before accuracy is meaningful</span></div>
      <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Verified pregame rule-engine picks by sport</caption>
        <thead><tr><th scope="col">Sport</th><th scope="col">Correct</th><th scope="col">Accuracy</th><th scope="col">95% range</th><th scope="col">Sample</th><th scope="col">Pending</th><th scope="col">Excluded</th></tr></thead>
        <tbody><SportRow label="Soccer" record={sports.soccer} minimum={minimum} /><SportRow label="NBA (regular season)" record={sports.nba} minimum={minimum} /></tbody>
      </table></div>
      <p className="quiet-label">Excluded: {sports.soccer.excluded.recorded_after_kickoff + sports.nba.excluded.recorded_after_kickoff} recorded after kickoff, {sports.nba.excluded.preseason} NBA preseason. With samples this small the 95% range is wide; treat accuracy as unproven.</p>
    </div>

    <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title">Probability quality and price movement</h3><span className="quiet-label">Lower log loss is better · games counted in brackets</span></div>
      <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Verified pregame probability scores by sport</caption>
        <thead><tr><th scope="col">Sport</th><th scope="col">Log loss</th><th scope="col">Versus the price when picked</th><th scope="col">Closing line value</th></tr></thead>
        <tbody><ProbabilityRow label="Soccer" record={sports.soccer} /><ProbabilityRow label="NBA (regular season)" record={sports.nba} /></tbody>
      </table></div>
      <p className="quiet-label">Log loss scores the full probabilities, not just the pick. Closing line value is how far the market moved toward the pick between when it was made and kickoff; consistently positive values are the clearest early sign of a real edge.</p>
    </div>

    <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title">Score forecasts</h3><span className="quiet-label">Average miss per team and on the total</span></div>
      <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Predicted against final scores</caption>
        <thead><tr><th scope="col">Sport</th><th scope="col">Graded</th><th scope="col">Per team</th><th scope="col">Total</th><th scope="col">Exact score</th></tr></thead>
        <tbody><ScoreRow label="Soccer" record={sports.soccer} unit="goals" /><ScoreRow label="NBA" record={sports.nba} unit="points" /></tbody>
      </table></div>
    </div>

    <RecentPredictions picks={data.recent ?? []} />
    {data.learning && <LearningStatus learning={data.learning} />}

    {market && <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title"><Scale size={15} /> Soccer model versus bookmaker odds</h3><span className="quiet-label">{market.matches.toLocaleString()} top-five league matches · seasons {market.test_window.start} to {market.test_window.end}, replayed week by week</span></div>
      <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Soccer model compared with closing market prices</caption>
        <thead><tr><th scope="col">Measure</th><th scope="col">Result</th><th scope="col">95% range</th></tr></thead>
        <tbody>
          <tr><th scope="row">Log loss: ratings model / previous rules engine</th><td>{market.model_log_loss.toFixed(3)} / {market.previous_engine_log_loss.toFixed(3)}</td><td>—</td></tr>
          <tr><th scope="row">Winners picked: model / bookmakers</th><td>{pct(market.model_accuracy)} / {pct(market.market_accuracy)}</td><td>—</td></tr>
          <tr><th scope="row">Probability error versus bookmakers (log loss gap)</th><td>{market.log_loss_gap.toFixed(3)} worse</td><td>{range(market.log_loss_gap_95ci, value => value.toFixed(3))}</td></tr>
          <tr><th scope="row">Return betting every model pick</th><td>{signedPct(market.model_pick_roi)}</td><td>{range(market.model_pick_roi_95ci, signedPct)}</td></tr>
          <tr><th scope="row">Return on "value" bets ({market.value_bets.toLocaleString()} bets, 5%+ edge)</th><td>{signedPct(market.value_bet_roi)}</td><td>{range(market.value_bet_roi_95ci, signedPct)}</td></tr>
        </tbody>
      </table></div>
      <p className="quiet-label">Each match was predicted using only results before it. Flat one-unit stakes at pre-weekend market-average prices. The ratings model is far better calibrated than the rules engine it replaced, but the bookmakers were still more accurate and bets where the model disagreed with the odds lost money. No betting edge is established, so soccer picks are never labelled BET.</p>
    </div>}
  </section>;
}
