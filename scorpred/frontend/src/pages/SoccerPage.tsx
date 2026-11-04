import { navigate, useLocation, localToday } from '../navigation';
import RequestError from '../components/RequestError';
import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import { useSourceRefresh } from '../hooks/useSourceRefresh';
import { decisionAt } from '../decisionLifecycle';
import { slatePlan } from '../slate';
import { CardSkeleton, DateTabs, DecisionCard, EmptyState, PlanStrip, type Decision } from '../components/DecisionCard';
import DataStatus from '../components/DataStatus';
import RecentResults from '../components/RecentResults';

interface SoccerData {
  slate: Decision[];
  topOpportunities: Decision[];
  plan: { bet: number; consider: number; skip: number };
  availableDates?: string[];
  selectedDate?: string;
  error?: string | null;
  last_updated?: string | null;
  refresh_pending?: boolean;
}

interface League {
  id: number;
  name: string;
  flag: string;
  country: string;
}

function SoccerWatermark() {
  return (
    <svg viewBox="0 0 80 80" className="hero-watermark" width="120" height="120" aria-hidden="true">
      <circle cx="40" cy="40" r="36" fill="none" stroke="currentColor" strokeWidth="3" />
      <polygon points="40,12 48,30 40,36 32,30" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
      <polygon points="40,36 52,44 48,58 32,58 28,44" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
      <line x1="32" y1="30" x2="28" y2="44" stroke="currentColor" strokeWidth="2" />
      <line x1="48" y1="30" x2="52" y2="44" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

const todayISO = localToday;

export default function SoccerPage({ onSelectMatch }: { onSelectMatch: (d: Decision) => void }) {
  const now = useNow();
  const location = useLocation();
  const params = new URLSearchParams(location.split('?')[1]);
  const selectedLeagueId = Number(params.get('league_id') || 39);
  const requestedDate = params.get('date') || todayISO();
  const results = params.get('view') === 'results';
  const leagueData = useFetch<{ leagues: League[] }>('/api/leagues');
  const leagues = leagueData.data?.leagues ?? [];
  const url = results ? null : `/api/dashboard/soccer?league_id=${selectedLeagueId}&date=${requestedDate}`;
  const { data, loading, error, retry, status } = useFetch<SoccerData>(url);
  const deadlines = (data?.slate ?? []).filter(game => Date.parse(game.matchDate || '') > now)
    .map(game => Date.parse(game.dataFreshness?.expires_at || '')).filter(Number.isFinite);
  const refreshing = data?.refresh_pending || (status === 503 && error?.includes('still refreshing'));
  useSourceRefresh(refreshing ? 0 : deadlines.length ? Math.min(...deadlines) : null, retry);
  const availableDates = data?.availableDates ?? [];
  const selectedDate = requestedDate || data?.selectedDate || todayISO();
  const setSelectedDate = (date: string) => navigate(`/soccer?league_id=${selectedLeagueId}&date=${date}`);

  const isTBD = (d: Decision) =>
    !d.teamA || !d.teamB || d.teamA === 'TBD' || d.teamB === 'TBD' ||
    (d.matchup || '').includes('TBD');

  const slate = (data?.slate ?? []).map(card => decisionAt(card, now)).filter(d => !isTBD(d));
  const top = (data?.topOpportunities ?? []).map(card => decisionAt(card, now)).filter(d => !isTBD(d) && (d.action !== 'SKIP' || d.researchPick));
  const plan = slatePlan(slate);
  const selectedLeague = leagues.find((l) => l.id === selectedLeagueId);

  return (
    <div className="page-stack">
      <section className="hero-card">
        <SoccerWatermark />
        <p className="page-eyebrow">
          {selectedLeague ? `${selectedLeague.flag} ${selectedLeague.name}` : 'Soccer'}
        </p>
        <h1 className="page-title">Soccer Predictions</h1>
        <p className="mt-3 max-w-xl text-slate-400 text-sm leading-relaxed">
          Fixtures, win chances and the matches worth a closer look.
        </p>
      </section>

      <div className="dashboard-controls"><div className="sport-filter" aria-label="Soccer view">
        <button aria-pressed={!results} onClick={() => navigate(`/soccer?league_id=${selectedLeagueId}`)}>Upcoming</button>
        <button aria-pressed={results} onClick={() => navigate(`/soccer?league_id=${selectedLeagueId}&view=results`)}>Recent results</button>
      </div></div>

      {results ? <RecentResults sport="soccer" /> : <>
      {/* League switcher */}
      {leagueData.error && <RequestError message={leagueData.error} onRetry={leagueData.retry} />}
      {error && <RequestError message={error} onRetry={retry} />}
      {data?.error && <RequestError message={data.error} onRetry={retry} />}
      {data?.refresh_pending && <p className="coverage-note" role="status">Match forecasts are refreshing. This view will update automatically.</p>}
      {leagues.length > 0 && (
        <div className="league-scroll">
          {leagues.map((lg) => (
            <button
              key={lg.id}
              type="button"
              onClick={() => navigate(`/soccer?league_id=${lg.id}`)}
              className={`league-pill ${selectedLeagueId === lg.id ? 'league-pill-active' : ''}`}
            >
              <span>{lg.flag}</span>
              <span>{lg.name}</span>
            </button>
          ))}
        </div>
      )}

      <DateTabs
        dates={[...new Set([...(availableDates.length ? availableDates : [todayISO()]), selectedDate])].sort()}
        selected={selectedDate}
        onSelect={setSelectedDate}
      />

      <PlanStrip bet={plan.bet} consider={plan.consider} skip={plan.skip} loading={loading} />
      <DataStatus cards={slate} updated={data?.last_updated} loading={loading} />

      <section className="section">
        <div className="section-header">
          <p className="section-label">Full Slate</p>
          <div className="section-divider" />
        </div>
        {loading ? (
          <div className="grid-2">
            {[0, 1, 2, 3].map((i) => <CardSkeleton key={i} />)}
          </div>
        ) : slate.length > 0 ? (
          <div className="grid-2">
            {slate.map((decision) => (
              <DecisionCard
                key={`${decision.action}-${decision.side}-${decision.matchup}`}
                decision={decision}
                onAnalyze={onSelectMatch}
              />
            ))}
          </div>
        ) : !loading && !error && !data?.error ? (
          <EmptyState title="No fixtures" body="No matches found for this date and league." />
        ) : null}
      </section>
      {top.length > 0 && (
      <section className="section">
        <div className="section-header">
          <p className="section-label">Top Opportunities</p>
          <div className="section-divider" />
        </div>
        {loading ? (
          <div className="grid-2">
            {[0, 1].map((i) => <CardSkeleton key={i} />)}
          </div>
        ) : error || data?.error ? (
          <EmptyState
            title="Data unavailable"
            body={data?.error ?? "Could not load soccer fixtures. Check back shortly."}
          />
        ) : top.length > 0 ? (
          <div className="grid-2">
            {top.map((decision) => (
              <DecisionCard
                key={`${decision.action}-${decision.side}-${decision.matchup}`}
                decision={decision}
                featured
                onAnalyze={onSelectMatch}
              />
            ))}
          </div>
        ) : (
          <EmptyState title="No top picks for this day" body="Try another date or check back when fixtures are confirmed." />
        )}
      </section>
      )}
      </>}
    </div>
  );
}
