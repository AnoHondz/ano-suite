import type { ResearchForecast } from "../scoringContracts";

export function ScoreProjection({ home, away, teamA, teamB, sport }: {
  home: number; away: number; teamA: string; teamB: string; sport: 'soccer' | 'nba' | 'nfl';
}) {
  return <div className="score-projection" aria-label="Projected score">
    <div><span className="score-team">{teamA}</span><strong>{home.toFixed(1)}</strong><span className="score-unit">{sport === 'soccer' ? 'expected goals' : 'expected points'}</span></div>
    <span className="score-separator" aria-hidden="true">:</span>
    <div><span className="score-team">{teamB}</span><strong>{away.toFixed(1)}</strong><span className="score-unit">{sport === 'soccer' ? 'expected goals' : 'expected points'}</span></div>
    <p>Expected total {(home + away).toFixed(1)}. Averages can be fractional; actual scores are whole numbers.</p>
  </div>;
}

export default function ScoringResearch({ forecast, teamA, teamB, sport }: {
  forecast?: ResearchForecast | null; teamA: string; teamB: string; sport: 'soccer' | 'nba' | 'nfl';
}) {
  if (!forecast?.available) return <p className="score-model-note">{forecast?.reason || 'This is a descriptive estimate. A trained scoring model is not available for this matchup.'}</p>;
  const evaluation = forecast.validation;
  return <div className="scoring-research">
    <p className="section-label">Experimental forecast · historical evaluation</p>
    {forecast.intervals && <div className="score-intervals" aria-label="Score prediction ranges">
      {(['home', 'away'] as const).map((side, index) => <div key={side}><span>{index === 0 ? teamA : teamB}</span>
        <strong>{forecast.intervals![side].low.toFixed(0)}–{forecast.intervals![side].high.toFixed(0)}</strong><small>80% target range · {sport === 'soccer' ? 'goals' : 'points'}</small></div>)}
    </div>}
    {evaluation && <p className="score-model-note">Later-game test: {evaluation.test_games} games through {evaluation.test_end}. Average error per team: {evaluation.points_mae.toFixed(2)} {sport === 'soccer' ? 'goals' : 'points'}; training-average baseline: {evaluation.baseline_mae.toFixed(2)}.
      {' '}Observed range coverage: {(evaluation.interval_coverage.home * 100).toFixed(1)}% / {(evaluation.interval_coverage.away * 100).toFixed(1)}%.</p>}
    <p className="score-model-note">{forecast.notice}</p>
    {forecast.imputed_features && forecast.imputed_features.length > 0 && <p className="score-model-note">Missing inputs filled from training data: {forecast.imputed_features.join(', ')}.</p>}
    <div className="score-references">{forecast.references?.map(reference => <a key={reference.url} href={reference.url} target="_blank" rel="noreferrer">{reference.title} ↗</a>)}</div>
  </div>;
}
