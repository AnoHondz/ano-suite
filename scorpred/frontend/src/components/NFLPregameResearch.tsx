import { FlaskConical, LockKeyhole } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';

interface FrozenModel {
  model_id: string; model_version: string; training_games: number; training_cutoff: string;
  artifact_sha256: string; feature_mapping_fingerprint: string; training_evidence_class: string;
}
interface Game {
  game_id: string; home_team: string; away_team: string; scheduled_kickoff: string;
  deadline: string; status: string; reserved_holdout: boolean;
}
interface Status {
  research_only: boolean; enabled: boolean; production_active: boolean; registered: boolean;
  models: FrozenModel[]; upcoming: Game[]; archived_forecasts?: number; archived_games?: number;
  missed_games?: number; rejected_attempts?: number; evidence_quality?: string; evaluation_locked: boolean;
  holdout_games?: number; earliest_review_at?: string;
  last_scheduler_receipt?: { created_at: string; result: { status: string } } | null;
  collector?: { state: string; heartbeat_fresh: boolean; forecast_scheduler_loaded: boolean };
  scheduler_receipt_fresh?: boolean;
  data_quality_problems?: { created_at: string; game_id: string; kind: string; detail: { reason?: string } }[];
}
interface Response { research_only: boolean; model_enabled_in_production: boolean; result: Status }
const utc = (value: string) => new Date(value).toISOString().replace('T', ' ').replace('.000Z', ' UTC');

export default function NFLPregameResearch() {
  const { data, loading, error, retry } = useFetch<Response>('/api/nfl/research/winner/forecasts/status');
  const result = data?.research_only === true && data.model_enabled_in_production === false && data.result?.research_only === true && data.result.enabled === false && data.result.production_active === false ? data.result : null;
  return <section className="page-stack" aria-label="Experimental NFL Winner Research">
    <div className="section-heading-row"><div><p className="page-eyebrow"><FlaskConical size={14} /> PREGAME FORECAST ARCHIVE</p>
      <h2 className="section-title">Experimental NFL Winner Research — Not Used for Production Recommendations</h2></div><span className="research-badge"><LockKeyhole size={14} /> Evaluation locked</span></div>
    {!result && <div className="card"><h3 className="panel-title">{loading ? 'Checking research availability…' : 'Research disabled or unavailable'}</h3><p className="score-model-note">Access requires the existing research feature flags and a signed-in account. This screen cannot enable models, generate forecasts, or unlock results.</p>{error && <p className="coverage-note">{error}</p>}<button className="text-button mt-3" onClick={retry}>Refresh status</button></div>}
    {result && <>
      <div className="research-stat-grid">
        <div className="card stat-card"><span>Archived predictions</span><strong>{result.archived_forecasts ?? 0}</strong><small>{result.archived_games ?? 0} games · paired Elo and logistic</small></div>
        <div className="card stat-card"><span>Missed games</span><strong>{result.missed_games ?? 0}</strong><small>Missing forecasts are never filled after deadline</small></div>
        <div className="card stat-card"><span>Rejected attempts</span><strong>{result.rejected_attempts ?? 0}</strong><small>Retries preserve every committed prediction</small></div>
        <div className="card stat-card"><span>Holdout status</span><strong className="research-status">{result.evaluation_locked ? 'Sealed' : 'Unavailable'}</strong><small>{result.holdout_games ?? 0} games · no outcomes shown</small></div>
      </div>
      <div className="card"><h3 className="panel-title">Frozen research models</h3><p className="score-model-note">{result.evidence_quality || 'No frozen model pair is registered.'}</p>
        <div className="table-scroll"><table className="evaluation-table"><thead><tr><th>Model version</th><th>Training games</th><th>Training cutoff (UTC)</th><th>Artifact SHA-256</th></tr></thead><tbody>{result.models.map(m => <tr key={m.model_id}><th scope="row">{m.model_version}</th><td>{m.training_games} · Class {m.training_evidence_class}</td><td>{utc(m.training_cutoff)}</td><td><code title={m.artifact_sha256}>{m.artifact_sha256.slice(0, 16)}…</code></td></tr>)}</tbody></table></div>
        <p className="score-model-note">Collector: {result.collector?.heartbeat_fresh && result.collector.forecast_scheduler_loaded ? result.collector.state : 'Not verified active'}. Forecast scheduler: {result.last_scheduler_receipt ? `${result.scheduler_receipt_fresh ? result.last_scheduler_receipt.result.status : 'Stale receipt'} · ${utc(result.last_scheduler_receipt.created_at)}` : 'Awaiting first forecast collection cycle'}. Review requires explicit authorization{result.earliest_review_at ? ` on or after ${utc(result.earliest_review_at)}` : ''}.</p>
      </div>
      <div className="card"><div className="section-heading-row"><h3 className="panel-title">Upcoming games and forecast status</h3><button className="text-button" onClick={retry}>Refresh status</button></div><p className="score-model-note">The first valid cycle within 72 hours of kickoff archives both models. The deadline is 60 minutes before each game’s independently observed kickoff. No probabilities or outcomes are used as production recommendations.</p>
        <div className="table-scroll"><table className="evaluation-table"><thead><tr><th>Game</th><th>Kickoff (UTC)</th><th>Deadline (UTC)</th><th>Status</th></tr></thead><tbody>{result.upcoming.map(g => <tr key={g.game_id}><th scope="row">{g.away_team} at {g.home_team}{g.reserved_holdout ? ' · reserved' : ''}</th><td>{utc(g.scheduled_kickoff)}</td><td>{utc(g.deadline)}</td><td>{g.status.replaceAll('_', ' ')}</td></tr>)}</tbody></table></div>{!result.upcoming.length && <p className="score-model-note">No eligible upcoming schedule was returned. No successful forecasts are implied.</p>}
      </div>
      {!!result.data_quality_problems?.length && <div className="card"><h3 className="panel-title">Collection and data-quality events</h3><ul>{result.data_quality_problems.map((p, i) => <li key={`${p.created_at}-${i}`}>{p.game_id}: {p.kind}{p.detail.reason ? ` · ${p.detail.reason}` : ''} · {utc(p.created_at)}</li>)}</ul></div>}
    </>}
  </section>;
}
