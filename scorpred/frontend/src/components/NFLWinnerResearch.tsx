import { useState } from 'react';
import { FlaskConical } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { navigate } from '../navigation';
import { record, numericFields } from '../scoringContracts';
import RequestError from './RequestError';

interface RegistryResponse {
  research_only: boolean;
  model_enabled_in_production: boolean;
  registry: { runs: Record<string, { run_id: string; research_only: boolean; enabled: boolean; production_active: boolean }> };
}
interface ResearchReport {
  research_only: boolean;
  result: { research_only: boolean; enabled: boolean; production_active: boolean; evidence_class?: string; evaluated_games?: number; models?: Record<string, unknown> };
}

export default function NFLWinnerResearch() {
  const catalog = useFetch<RegistryResponse>('/api/nfl/research/winner/models');
  const [run, setRun] = useState('');
  const authorized = catalog.data?.research_only === true && catalog.data.model_enabled_in_production === false;
  const runs = authorized ? Object.values(catalog.data?.registry?.runs ?? {}).filter(item => item.research_only === true && item.enabled === false && item.production_active === false) : [];
  const selected = runs.find(item => item.run_id === run);
  const report = useFetch<ResearchReport>(selected ? `/api/nfl/research/winner/evaluations/${encodeURIComponent(selected.run_id)}` : null);
  const result = selected && report.data?.research_only === true && report.data.result?.research_only === true && report.data.result.enabled === false && report.data.result.production_active === false ? report.data.result : null;
  const rows = Object.entries(result?.models ?? {}).filter(([key, value]) => key.endsWith(':selected') && numericFields(value, ['log_loss', 'brier', 'accuracy', 'count']));
  return <section className="winner-research" aria-label="NFL winner model research">
    <div className="section-heading-row"><div><p className="page-eyebrow"><FlaskConical size={14} />SEPARATE RESEARCH WORKSPACE</p><h2 className="section-title">NFL Winner Model Research — Experimental</h2></div><span className="research-badge">No production decisions</span></div>
    <div className="card"><h3 className="panel-title">{catalog.loading ? 'Checking research availability…' : authorized ? 'Read-only research evaluations' : 'Research disabled or unavailable'}</h3>
      <p className="score-model-note">This section respects the existing server feature flags. Research remains separate from current dashboard analysis; this screen never requests a game-level research forecast or enables a model.</p>
      {catalog.error && <p className="coverage-note mt-3">{catalog.error}</p>}
      {authorized && <><label className="quiet-label block mt-4" htmlFor="research-run">Saved evaluation</label><select id="research-run" value={selected?.run_id || ''} onChange={event => setRun(event.target.value)}><option value="">Select a saved research run</option>{runs.map(item => <option key={item.run_id} value={item.run_id}>{item.run_id}</option>)}</select>{!runs.length && <p className="score-model-note">No eligible saved evaluations were returned by the research registry.</p>}</>}
      {selected && report.loading && <p className="score-model-note" role="status">Loading saved evaluation…</p>}
      {selected && report.error && <RequestError message={report.error} onRetry={report.retry} />}
      {result && <><p className="score-model-note">Historical evaluation · {result.evaluated_games ?? 'Unavailable'} games · Evidence class {result.evidence_class || 'Unavailable'}. No statistically significant advantage is claimed.</p><div className="table-scroll"><table className="evaluation-table"><thead><tr><th>Model</th><th>Log loss</th><th>Brier</th><th>Accuracy</th></tr></thead><tbody>{rows.map(([name, value]) => {
        const item = record(value) ? value as Record<string, number> : null;
        return item && <tr key={name}><th scope="row">{name.replace(':selected', '').replaceAll('_', ' ')}</th><td>{item.log_loss.toFixed(6)}</td><td>{item.brier.toFixed(6)}</td><td>{(item.accuracy * 100).toFixed(2)}%</td></tr>;
      })}</tbody></table></div></>}
      <button className="text-button mt-3" onClick={() => navigate('/insights')}>Explore the saved Phase 2.1 historical study →</button>
    </div>
  </section>;
}
