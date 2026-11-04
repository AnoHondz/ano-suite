import { Layers } from 'lucide-react';
import { navigate, useLocation, calendarFormat } from '../navigation';
import { useFetch } from '../hooks/useFetch';
import RequestError from '../components/RequestError';
import { CardSkeleton, EmptyState } from '../components/DecisionCard';

type Sport = 'nba' | 'nfl' | 'soccer';

interface Pick {
  rank: number;
  selection: string;
  matchup: string;
  competition: string;
  commence_time: string;
  bet_type: string;
  american: string;
  book: string;
  prob: number;
  ev: number;
  tier: 'value' | 'best available' | 'lean';
  source: 'model+market' | 'market';
}

interface Parlay {
  legs: number[];
  selections: string[];
  american: string;
  decimal_odds: number;
  hit_prob: number;
  payout_per_10: number;
}

interface Slate {
  status: string;
  title?: string;
  slate_date?: string;
  generated_at?: string;
  games?: number;
  model_games?: number;
  competitions?: string[];
  picks?: Pick[];
  parlay?: Parlay | null;
  notes?: string[];
  is_previous?: boolean;
  today_status?: string;
  today_note?: string;
}

const SPORTS: { key: Sport; label: string; schedule: string }[] = [
  { key: 'nba', label: 'NBA', schedule: 'Every game day · 6 AM ET' },
  { key: 'nfl', label: 'NFL', schedule: 'Every game day (Thu / Sun / Mon) · 6 AM ET' },
  { key: 'soccer', label: 'Soccer', schedule: 'Every day with top-league or European games · 6 AM ET' },
];

const pct = (value: number) => `${Math.round(value * 100)}%`;
const kickoff = (iso: string) => calendarFormat(iso, { weekday: 'short', hour: 'numeric', minute: '2-digit' });

function Waiting({ sport, slate }: { sport: Sport; slate: Slate | null }) {
  const label = SPORTS.find(item => item.key === sport)!;
  if (slate?.today_note) return <EmptyState title="Today's slate isn't available" body={`${slate.today_note}. ${label.label} picks post ${label.schedule.toLowerCase()}.`} />;
  if (slate?.status === 'off_day') return <EmptyState title={`No ${label.label} slate today`} body={`${label.label} picks post ${label.schedule.toLowerCase()}.`} />;
  if (slate?.status === 'no_games') return <EmptyState title="No games in this window" body="No games were found for today's slate window." />;
  return <EmptyState title="No picks yet" body={`${label.label} picks post ${label.schedule.toLowerCase()}.`} />;
}

export default function ParlayPage() {
  const location = useLocation();
  const requested = new URLSearchParams(location.split('?')[1]).get('sport') as Sport | null;
  const sport: Sport = SPORTS.some(item => item.key === requested) ? requested! : 'nba';
  const { data, loading, error, retry } = useFetch<Slate>(`/api/parlays/${sport}`);
  const picks = data?.picks ?? [];
  const inParlay = new Set(data?.parlay?.legs ?? []);

  return <div className="page-stack">
    <section className="hero-card">
      <div><p className="page-eyebrow"><span className="eyebrow-rule" />PARLAY MODE</p>
      <h1 className="page-title">5 picks and one parlay</h1>
      <p className="hero-description">Priced off real sportsbook lines (DraftKings via ESPN, free). Your model is blended in where it covers the game.</p></div>
    </section>

    <div className="prop-board-tabs" role="tablist" aria-label="Sport">
      {SPORTS.map(item => <button key={item.key} type="button" role="tab" aria-selected={sport === item.key}
        className={sport === item.key ? 'prop-board-tab-active' : ''} onClick={() => navigate(`/parlays?sport=${item.key}`)}>{item.label}</button>)}
    </div>

    {error ? <RequestError message={error} onRetry={retry} />
      : loading ? <CardSkeleton />
      : !picks.length ? <Waiting sport={sport} slate={data} />
      : <>
        {data?.is_previous && <p className="coverage-note" role="status">
          {data.today_note ? `Today's slate isn't available (${data.today_note}).` : data.today_status === 'off_day' ? 'No slate today.' : "Today's slate isn't ready yet."} Showing the most recent one{data.slate_date ? ` (${calendarFormat(`${data.slate_date}T12:00:00Z`, { month: 'short', day: 'numeric' })})` : ''}.
        </p>}

        <section className="section">
          <div className="section-heading-row"><div>
            <h2 className="section-title"><Layers size={18} />{data?.title}<span className="count-tag">{picks.length}</span></h2>
            <p className="quiet-label">{[data?.competitions?.join(' · '), `${data?.games ?? 0} games scanned`, data?.model_games ? `${data.model_games} with ScorPred model` : '',
              data?.generated_at ? `updated ${calendarFormat(data.generated_at, { hour: 'numeric', minute: '2-digit' })} ET` : ''].filter(Boolean).join(' · ')}</p>
          </div></div>
          <div className="card evaluation-table-card"><div className="table-scroll"><table className="evaluation-table">
            <caption className="sr-only">Today's picks</caption>
            <thead><tr><th scope="col">#</th><th scope="col">Pick</th><th scope="col">Price</th><th scope="col">Chance</th><th scope="col">Value</th><th scope="col">Game</th></tr></thead>
            <tbody>{picks.map(pick => <tr key={pick.rank}>
              <td>{pick.rank}{inParlay.has(pick.rank) ? ' ★' : ''}</td>
              <th scope="row">{pick.selection}<br /><span className="quiet-label">{pick.bet_type}{pick.source === 'model+market' ? ' · ScorPred model + market' : ' · market consensus'}</span></th>
              <td>{pick.american}<br /><span className="quiet-label">{pick.book}</span></td>
              <td><strong>{pct(pick.prob)}</strong></td>
              <td>{pick.tier === 'value' ? `+${(pick.ev * 100).toFixed(1)}% EV` : 'Best available, no edge'}</td>
              <td>{pick.matchup}<br /><span className="quiet-label">{pick.competition} · {kickoff(pick.commence_time)}</span></td>
            </tr>)}</tbody>
          </table></div></div>
        </section>

        {data?.parlay && <section className="section">
          <div className="section-heading-row"><h2 className="section-title">Parlay of the day<span className="count-tag">{data.parlay.legs.length} legs</span></h2></div>
          <div className="card">
            <ul className="quiet-label">{data.parlay.selections.map(selection => <li key={selection}>★ {selection}</li>)}</ul>
            <div className="table-scroll"><table className="evaluation-table"><caption className="sr-only">Parlay summary</caption>
              <thead><tr><th scope="col">Odds</th><th scope="col">Payout</th><th scope="col">$10 returns</th><th scope="col">Chance to hit</th></tr></thead>
              <tbody><tr><td><strong>{data.parlay.american}</strong></td><td>{data.parlay.decimal_odds.toFixed(2)}×</td>
                <td>${data.parlay.payout_per_10.toFixed(2)}</td><td>{pct(data.parlay.hit_prob)}</td></tr></tbody>
            </table></div>
            <p className="quiet-label">The most likely combination of today's picks that pays at least 2× the stake. ★ marks its legs above.</p>
          </div>
        </section>}

        {data?.notes?.map(note => <p className="coverage-note" key={note}>{note}</p>)}
      </>}

    <p className="quiet-label">Odds move after picks post; check the price at your book first. Chances are estimates, not guarantees, and "Lean" picks rely on the
      market alone. Bet only what you can afford to lose. If gambling stops being fun, call 1-800-GAMBLER.</p>
  </div>;
}
