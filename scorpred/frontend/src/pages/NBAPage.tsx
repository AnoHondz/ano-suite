import { navigate, useLocation, localToday } from '../navigation';
import RequestError from '../components/RequestError';
import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import { useNBARefresh } from '../hooks/useNBARefresh';
import { decisionAt } from '../decisionLifecycle';
import { slatePlan } from '../slate';
import { CardSkeleton, DateTabs, DecisionCard, EmptyState, PlanStrip, type Decision } from '../components/DecisionCard';
import DataStatus from '../components/DataStatus';
import RecentResults from '../components/RecentResults';

interface NBAData {
  slate: Decision[];
  topOpportunities: Decision[];
  plan: { bet: number; consider: number; skip: number };
  availableDates?: string[];
  selectedDate?: string;
  error?: string | null;
  warnings?: string[];
  last_updated?: string | null;
  notice?: string;
}

function BasketballWatermark() {
  return (
    <svg viewBox="0 0 80 80" className="hero-watermark" width="120" height="120" aria-hidden="true">
      <circle cx="40" cy="40" r="36" fill="none" stroke="currentColor" strokeWidth="3" />
      <line x1="4" y1="40" x2="76" y2="40" stroke="currentColor" strokeWidth="2.5" />
      <line x1="40" y1="4" x2="40" y2="76" stroke="currentColor" strokeWidth="2.5" />
      <path d="M40 4 C52 16, 52 64, 40 76" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M40 4 C28 16, 28 64, 40 76" fill="none" stroke="currentColor" strokeWidth="2.5" />
    </svg>
  );
}

const todayISO = localToday;
const tipoff = (d: Decision) => (d.matchDate ? new Date(d.matchDate).getTime() : Number.MAX_SAFE_INTEGER) || Number.MAX_SAFE_INTEGER;
const ACTION_RANK = { BET: 0, CONSIDER: 1, SKIP: 2 } as const;

export default function NBAPage({ onSelectMatch }: { onSelectMatch: (d: Decision) => void }) {
  const now = useNow();
  const location = useLocation();
  const params = new URLSearchParams(location.split('?')[1]);
  const view = params.get('view') || 'upcoming';
  const upcoming = view === 'upcoming';
  const requestedDate = params.get('date') || '';
  const url = upcoming ? `/api/dashboard/nba?date=${requestedDate}` : view === 'results' ? null : `/api/nba/games?view=${view}&date=${requestedDate}`;
  const { data, loading, error, retry } = useFetch<NBAData>(url);
  const deadlines = (data?.slate ?? []).filter(game => Date.parse(game.matchDate || '') > now)
    .map(game => Date.parse(game.dataFreshness?.expires_at || '')).filter(Number.isFinite);
  useNBARefresh(upcoming && deadlines.length ? Math.min(...deadlines) : null, retry);
  const availableDates = data?.availableDates ?? [];
  const selectedDate = requestedDate || data?.selectedDate || availableDates[0] || todayISO();
  const setSelectedDate = (date: string) => navigate(`/nba?view=${view}&date=${date}`);

  const isTBD = (d: Decision) =>
    !d.teamA || !d.teamB || d.teamA === 'TBD' || d.teamB === 'TBD' ||
    (d.matchup || '').includes('TBD');

  const slate = (data?.slate ?? []).map(card => decisionAt(card, now)).filter(d => !isTBD(d));
  const plays = slate.filter(d => d.action !== 'SKIP' || d.researchPick)
    .sort((a, b) => ACTION_RANK[a.action] - ACTION_RANK[b.action] || b.confidence - a.confidence);
  const rest = slate.filter(d => d.action === 'SKIP' && !d.researchPick).sort((a, b) => tipoff(a) - tipoff(b));
  const plan = slatePlan(slate);
  const failed = Boolean(error || data?.error);

  return (
    <div className="page-stack">
      <section className="hero-card">
        <BasketballWatermark />
        <p className="page-eyebrow">NBA · Predictions</p>
        <h1 className="page-title">NBA Game Plan</h1>
        <p><a href="/nba/props/research">Player prop research · evidence, prices and paper results</a></p>
        <p className="mt-3 max-w-xl text-slate-400 text-sm leading-relaxed">
          Win chances for every game, compared with the sportsbook line.
        </p>
      </section>

      {error && <RequestError message={error} onRetry={retry} />}
      {data?.error && <RequestError message={data.error} onRetry={retry} />}
      <div className="dashboard-controls"><div className="sport-filter" aria-label="NBA schedule view">
        {(['upcoming', 'results', 'archive'] as const).map(item => <button key={item} aria-pressed={view === item}
          onClick={() => navigate(item === 'upcoming' ? '/nba' : `/nba?view=${item}`)}>{item === 'upcoming' ? 'Upcoming' : item === 'results' ? 'Recent results' : 'Local history'}</button>)}
      </div>{view === 'archive' && <label className="quiet-label">Observation date <input type="date" aria-label="NBA observation date" value={selectedDate} onChange={event => { if (event.target.value) setSelectedDate(event.target.value); }} /></label>}</div>
      {view === 'results' ? <RecentResults sport="nba" /> : <>
      <DateTabs
        dates={[...new Set([...(availableDates.length ? availableDates : [todayISO()]), selectedDate])].sort()}
        selected={selectedDate}
        onSelect={setSelectedDate}
      />

      {upcoming && <PlanStrip bet={plan.bet} consider={plan.consider} skip={plan.skip} loading={loading} />}
      <DataStatus cards={slate} updated={data?.last_updated} loading={loading} warnings={data?.warnings} />

      {plays.length > 0 && (
        <section className="section" aria-label="NBA plays">
          <div className="section-header">
            <p className="section-label">Plays</p>
            <div className="section-divider" />
          </div>
          <div className="grid-2">
            {plays.map((decision) => (
              <DecisionCard key={decision.matchId || decision.matchup} decision={decision} featured={decision.action === 'BET'} onAnalyze={onSelectMatch} />
            ))}
          </div>
        </section>
      )}

      {(loading || rest.length > 0 || !plays.length) && <section className="section" aria-label="NBA slate">
        <div className="section-header">
          <p className="section-label">{upcoming ? (plays.length ? 'Rest of the slate' : 'Slate') : 'Results'}</p>
          <div className="section-divider" />
        </div>
        {loading ? (
          <div className="grid-2">
            {[0, 1, 2, 3].map((i) => <CardSkeleton key={i} />)}
          </div>
        ) : rest.length > 0 ? (
          <div className="grid-2">
            {rest.map((decision) => (
              <DecisionCard key={decision.matchId || decision.matchup} decision={decision} onAnalyze={onSelectMatch} />
            ))}
          </div>
        ) : !failed && !plays.length ? (
          <EmptyState title="No games found" body="No NBA games scheduled for this date." />
        ) : null}
      </section>}
      </>}
    </div>
  );
}
