import { useState, useEffect, useRef } from 'react';
import { Target } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { calendarFormat } from '../navigation';
import { useNow } from '../hooks/useNow';
import EmailProps from './EmailProps';

type Sport = 'soccer' | 'nba' | 'nfl';

interface BoardPick {
  roster_verification?: { expires_at: string };
  sport: Sport;
  validation: string;
  match_id: string;
  matchup: string;
  kickoff: string;
  league: string;
  name: string;
  team: string;
  label: string;
  side: 'over' | 'under';
  line: number;
  line_source: string;
  projection: number;
  chance: number;
  record: number | null;
}

interface Board {
  date: string;
  slates: Record<Sport, { date: string | null; games: number; with_props: number; unavailable: { matchup: string; reason: string }[] }>;
  overall: BoardPick[];
  by_sport: Record<Sport, BoardPick[]>;
  notes: string[];
}

const TABS: { key: 'overall' | Sport; label: string }[] = [
  { key: 'overall', label: 'Best overall' }, { key: 'nba', label: 'NBA' }, { key: 'soccer', label: 'Soccer' }, { key: 'nfl', label: 'NFL' },
];

function kickoff(value: string) {
  return calendarFormat(value, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

function PickRow({ pick }: { pick: BoardPick }) {
  const sportsbook = pick.line_source !== 'historical';
  return <tr>
    <th scope="row">{pick.name}<span className="quiet-label"> · {pick.team}</span></th>
    <td>{pick.side === 'over' ? 'Over' : 'Under'} {pick.line} {pick.label}{sportsbook ? '' : <span className="quiet-label"> (player median)</span>}</td>
    <td><strong>{Math.round(pick.chance * 100)}%</strong></td>
    <td>{pick.projection.toFixed(1)}</td>
    <td>{pick.record == null ? '—' : `${Math.round(pick.record * 100)}%`}</td>
    <td>{pick.matchup}<br /><span className="quiet-label">{kickoff(pick.kickoff)} · {pick.validation}</span></td>
  </tr>;
}

export default function BestProps({ date }: { date: string }) {
  const [tab, setTab] = useState<'overall' | Sport>('overall');
  const { data, loading, error, retry } = useFetch<{ result: Board }>(`/api/props/best?date=${encodeURIComponent(date)}`);
  const board = data?.result;
  const now = useNow();
  const renewed = useRef('');
  const allPicks = board ? [...board.overall, ...Object.values(board.by_sport).flat()] : [];
  const expired = allPicks.map(p => p.roster_verification?.expires_at).filter((value): value is string =>
    Boolean(value && (!Number.isFinite(Date.parse(value)) || Date.parse(value) <= now))).sort().join('|');
  useEffect(() => {
    if (expired && !loading && renewed.current !== expired) {
      renewed.current = expired;
      retry();
    }
  }, [expired, loading, retry]);
  const picks = (board ? (tab === 'overall' ? board.overall : board.by_sport[tab] ?? []) : []).filter(p =>
    !p.roster_verification || Date.parse(p.roster_verification.expires_at) > now);
  const slate = board && tab !== 'overall' ? board.slates[tab] : null;
  return <section className="section" aria-label="Best player props">
    <div className="section-heading-row"><div><h2 className="section-title"><Target size={18} />Best player props</h2>
      <p className="quiet-label">Ranked by model comparison · at least 60% estimated chance · one pick per player</p></div></div>
    <div className="prop-board-tabs" role="tablist" aria-label="Props by sport">
      {TABS.map(item => <button key={item.key} type="button" role="tab" aria-selected={tab === item.key} onClick={() => setTab(item.key)}
        className={tab === item.key ? 'prop-board-tab-active' : ''}>{item.label}</button>)}
    </div>
    <EmailProps key={`${date}-${tab}`} date={date} sport={tab} />
    {loading && <div className="card"><p className="quiet-label" role="status">Collecting props from every game on the slate…</p></div>}
    {error && <p className="coverage-note">Player props are unavailable right now. <button type="button" className="text-button" onClick={retry}>Reload props</button></p>}
    {board && <div className="card evaluation-table-card">
      {tab === 'overall' && <p className="quiet-label">Experimental soccer and NBA estimates. Historical model tests do not establish sportsbook returns. NFL props have a separate tab.</p>}
      {slate && slate.date && slate.date !== board.date && <p className="coverage-note">No {tab.toUpperCase()} games on this date; showing the next slate ({calendarFormat(`${slate.date}T12:00:00Z`, { month: 'short', day: 'numeric' })}).</p>}
      {picks.length === 0 ? <p className="quiet-label">No pregame props for this selection.</p>
        : <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Best player props</caption>
          <thead><tr><th scope="col">Player</th><th scope="col">Pick</th><th scope="col">Chance</th><th scope="col">Projection</th><th scope="col">Recent hit rate</th><th scope="col">Game</th></tr></thead>
          <tbody>{picks.map(pick => <PickRow key={`${pick.sport}-${pick.match_id}-${pick.name}-${pick.label}`} pick={pick} />)}</tbody>
        </table></div>}
      {slate && slate.unavailable.length > 0 && <p className="quiet-label">No props for {slate.unavailable.map(item => item.matchup).join(', ')}.</p>}
      <details className="prop-about"><summary>How to read this</summary>{board.notes.map(note => <p key={note}>{note}</p>)}</details>
    </div>}
  </section>;
}
