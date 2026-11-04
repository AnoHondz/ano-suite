import { useState } from 'react';
import { FlaskConical, ArrowDownRight, Database } from 'lucide-react';
import evaluation from '../data/nfl-phase21-evaluation.json';
import { calendarFormat } from '../navigation';

type Metric = 'brier' | 'log_loss' | 'accuracy';
const METRICS: { key: Metric; label: string; hint: string; maximum: number }[] = [
  { key: 'brier', label: 'Brier score', hint: 'Lower is better · binary outcomes', maximum: 1 },
  { key: 'log_loss', label: 'Log loss', hint: 'Lower is better · displayed scale 0–1', maximum: 1 },
  { key: 'accuracy', label: 'Accuracy', hint: 'Higher is better · scale 0–100%', maximum: 1 },
];

export default function HistoricalPerformance() {
  const [modelId, setModelId] = useState(evaluation.models[0].id);
  const model = evaluation.models.find(item => item.id === modelId)!;
  const bins = model.reliability.filter(bin => bin.count > 0 && bin.mean_probability != null && bin.home_win_rate != null);
  return <section className="section historical-performance" aria-label="NFL historical model performance">
    <div className="section-heading-row"><div><p className="page-eyebrow"><FlaskConical size={14} />HISTORICAL RESEARCH</p>
      <h2 className="section-title">Performance, with perspective.</h2></div><span className="research-badge">Phase 2.1 · Experimental</span></div>
    <p className="page-description">NFL winner models evaluated chronologically on Class B reconstructed evidence. These are saved historical results, not live production performance.</p>
    <div className="research-summary">
      <div><span className="quiet-label">Evaluated games</span><strong>{evaluation.evaluated_games}</strong><small>Out of {evaluation.eligible_games} eligible games</small></div>
      <div><span className="quiet-label">Evidence class</span><strong>Class {evaluation.evidence_class}</strong><small>Historically reconstructed</small></div>
      <div><span className="quiet-label">Production use</span><strong className="research-status">Disabled</strong><small>No change to dashboard decisions</small></div>
    </div>
    <div className="card evaluation-table-card"><div className="section-heading-row"><h3 className="panel-title">Model comparison</h3><span className="quiet-label">Same {evaluation.evaluated_games} evaluation games</span></div>
      <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Phase 2.1 saved NFL historical model evaluation</caption>
        <thead><tr><th scope="col">Model</th><th scope="col">Log loss ↓</th><th scope="col">Brier ↓</th><th scope="col">Accuracy ↑</th></tr></thead>
        <tbody>{evaluation.models.map(item => <tr key={item.id}><th scope="row">{item.label}</th><td>{item.log_loss.toFixed(6)}</td><td>{item.brier.toFixed(6)}</td><td>{(item.accuracy * 100).toFixed(2)}%</td></tr>)}</tbody>
      </table></div><p className="quiet-label">Calibration selection used earlier data. Point estimates do not establish a statistically significant advantage.</p></div>
    <div className="metric-charts">{METRICS.map(metric => <div className="card" key={metric.key}><h3 className="panel-title">{metric.label}{metric.key !== 'accuracy' && <ArrowDownRight size={15} />}</h3><p className="quiet-label chart-hint">{metric.hint}</p>
      <div className="comparison-chart" role="img" aria-label={`${metric.label} comparison; exact values are provided in the model comparison table`}>{evaluation.models.map(item => <div className="comparison-chart-row" key={item.id}>
        <div><span>{item.label}</span><strong>{metric.key === 'accuracy' ? `${(item[metric.key] * 100).toFixed(2)}%` : item[metric.key].toFixed(6)}</strong></div>
        <div className="chart-track"><span style={{ width: `${item[metric.key] / metric.maximum * 100}%` }} /></div>
      </div>)}</div><div className="chart-scale"><span>0</span><span>{metric.key === 'accuracy' ? '100%' : '1'}</span></div></div>)}</div>
    <div className="grid-2">
      <section className="card"><div className="section-heading-row"><h3 className="panel-title">Calibration</h3><label className="sr-only" htmlFor="calibration-model">Calibration model</label>
        <select id="calibration-model" value={modelId} onChange={event => setModelId(event.target.value)}>{evaluation.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></div>
        <p className="quiet-label chart-hint">Saved test bins · home-win outcomes conditional on no tie</p>
        <svg className="calibration-chart" viewBox="0 0 360 285" role="img" aria-label={`Calibration for ${model.label}. Horizontal axis: predicted home-win probability. Vertical axis: observed home-win rate. Bin values follow in the table.`}>
          {[0, .25, .5, .75, 1].map(value => <g key={value}><line x1="44" x2="328" y1={242 - value * 212} y2={242 - value * 212} className="chart-grid" /><text x="34" y={246 - value * 212} textAnchor="end">{value * 100}</text><text x={44 + value * 284} y="261" textAnchor="middle">{value * 100}%</text></g>)}
          <line x1="44" y1="242" x2="328" y2="30" className="ideal-line" />
          {bins.map(bin => <circle key={bin.low} cx={44 + bin.mean_probability! * 284} cy={242 - bin.home_win_rate! * 212} r="5" className="calibration-dot"><title>{`${bin.count} games · predicted ${(bin.mean_probability! * 100).toFixed(1)}% · observed ${(bin.home_win_rate! * 100).toFixed(1)}%`}</title></circle>)}
          <text x="186" y="281" textAnchor="middle">Predicted home-win probability</text>
          <text transform="translate(12,136) rotate(-90)" textAnchor="middle">Observed home-win rate (%)</text>
        </svg>
        <p className="quiet-label">Dashed line: ideal calibration. Dots: non-empty saved bins. Small bin samples make these estimates uncertain.</p>
        <details className="research-details"><summary>View calibration bin data</summary><div className="table-scroll"><table className="evaluation-table"><thead><tr><th>Games</th><th>Predicted</th><th>Observed</th></tr></thead><tbody>{bins.map(bin => <tr key={bin.low}><td>{bin.count}</td><td>{(bin.mean_probability! * 100).toFixed(1)}%</td><td>{(bin.home_win_rate! * 100).toFixed(1)}%</td></tr>)}</tbody></table></div></details>
      </section>
      <section className="card research-limitations"><p className="page-eyebrow"><Database size={14} />EVIDENCE & LIMITATIONS</p><h3 className="panel-title">Read the result in context.</h3>
        <p>Class B reconstructs earlier evidence under explicit availability assumptions. It does not prove that the stored source values were observed before each historical game.</p>
        <ul><li>Chronological test cutoffs: {calendarFormat(evaluation.test_start, { month: 'short', day: 'numeric', year: 'numeric' })}–{calendarFormat(evaluation.test_end, { month: 'short', day: 'numeric', year: 'numeric' })}.</li>
          <li>Limited coverage: {evaluation.evaluated_games} evaluated games across two qualifying folds. Other eligible games were used for development or not evaluated.</li>
          <li>EPA and quarterback features lack verified historical support. Injuries, weather, and betting lines are excluded.</li>
          <li>No statistically significant advantage established. Random forest versus home-prior 95% Brier difference interval: [{evaluation.forest_vs_prior.brier_delta_95.map(value => value.toFixed(6)).join(', ')}], which includes zero.</li>
          <li>Binary evaluation excludes tied outcomes. Prospective validation and reconstruction uncertainty remain unresolved.</li></ul>
      </section>
    </div>
    <details className="research-details provenance"><summary>Evaluation provenance</summary><dl><dt>Run</dt><dd>{evaluation.run_id}</dd><dt>Source artifact</dt><dd>{evaluation.source}</dd><dt>Source SHA-256</dt><dd>{evaluation.source_sha256}</dd><dt>Generated</dt><dd>{calendarFormat(evaluation.generated_at, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })} ET</dd></dl><p>Read-only metric snapshot exported from the saved report. This view does not call prediction endpoints or activate a model.</p></details>
  </section>;
}
