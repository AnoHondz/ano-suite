import { useFetch } from '../hooks/useFetch';
import RequestError from './RequestError';
import { CardSkeleton, EmptyState } from './DecisionCard';

interface Record {
  graded: number;
  correct: number;
  wrong: number;
  accuracy: number | null;
  accuracy_95ci: [number, number] | null;
}

interface GradedGame {
  id: string;
  kickoff: string;
  league: string | null;
  home: string;
  away: string;
  pick: string;
  pick_probability: number | null;
  final_score: { a: number; b: number } | null;
  correct: boolean;
  totals: { pick: string; line: number; scored: number; correct: boolean } | null;
}

interface ResultsData {
  minimum_sample: number;
  winner: Record;
  winner_last_7_days: Record;
  totals: Record;
  pending: number;
  games: GradedGame[];
}

const pct = (value: number) => `${Math.round(value * 100)}%`;

function Tally({ label, record }: { label: string; record: Record }) {
  return <span><span className="plan-stat-num">{record.graded ? `${record.correct}–${record.wrong}` : '—'}</span>
    {label}{record.accuracy != null ? ` · ${pct(record.accuracy)}` : ''}</span>;
}

const Outcome = ({ correct }: { correct: boolean }) =>
  <span className={correct ? 'form-dot-W' : 'form-dot-L'}>{correct ? '✓ Hit' : '✗ Miss'}</span>;

export default function RecentResults({ sport }: { sport: 'soccer' | 'nba' }) {
  const { data, loading, error, retry } = useFetch<ResultsData>(`/api/performance/results?sport=${sport}`);
  if (loading) return <CardSkeleton />;
  if (error || !data) return <RequestError message={error ?? 'Recent results are unavailable.'} onRetry={retry} />;
  const { winner, minimum_sample: minimum } = data;
  return <section className="section" aria-label="Track record">
    <div className="section-header"><p className="section-label">Track record</p><div className="section-divider" /></div>
    <p className="plan-line" aria-label="Track record summary">
      <Tally label="winners" record={winner} />
      <Tally label="last 7 days" record={data.winner_last_7_days} />
      <Tally label="totals" record={data.totals} />
      <span><span className="plan-stat-num">{data.pending}</span>pending</span>
    </p>
    <p className="quiet-label">
      Only picks recorded before kickoff count{sport === 'nba' ? '; preseason games are left out' : ''}.
      {winner.accuracy_95ci && ` 95% range for winner accuracy: ${pct(winner.accuracy_95ci[0])} to ${pct(winner.accuracy_95ci[1])}.`}
      {winner.graded < minimum && ` ${minimum - winner.graded} more graded picks are needed before accuracy means much.`}
    </p>
    {data.games.length === 0 ? <EmptyState title="No graded picks yet" body="Picks are graded automatically once games finish." />
      : <div className="card evaluation-table-card"><div className="table-scroll"><table className="evaluation-table">
        <caption className="sr-only">Pregame picks graded against final scores</caption>
        <thead><tr><th scope="col">Date</th><th scope="col">Match</th><th scope="col">Pick</th><th scope="col">Final</th><th scope="col">Result</th><th scope="col">Total</th></tr></thead>
        <tbody>{data.games.map(game => <tr key={game.id}>
          <td>{new Date(game.kickoff).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</td>
          <th scope="row">{game.home} vs {game.away}{game.league && <span className="quiet-label"> · {game.league}</span>}</th>
          <td>{game.pick}{game.pick_probability != null ? ` (${pct(game.pick_probability)})` : ''}</td>
          <td>{game.final_score ? `${game.final_score.a}–${game.final_score.b}` : '—'}</td>
          <td><Outcome correct={game.correct} /></td>
          <td>{game.totals ? <>{game.totals.pick} {game.totals.line} ({game.totals.scored}) <Outcome correct={game.totals.correct} /></> : '—'}</td>
        </tr>)}</tbody>
      </table></div></div>}
  </section>;
}
