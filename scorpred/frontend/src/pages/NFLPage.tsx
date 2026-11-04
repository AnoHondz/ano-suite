import { navigate, useLocation } from '../navigation';
import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import { decisionAt } from '../decisionLifecycle';
import { slatePlan } from '../slate';
import RequestError from '../components/RequestError';
import { CardSkeleton, DateTabs, DecisionCard, EmptyState, PlanStrip, type Decision } from '../components/DecisionCard';
import NFLDataPanel, { type NFLDataSources } from '../components/NFLDataPanel';
import DataStatus from '../components/DataStatus';

interface NFLData {
  slate: Decision[];
  topOpportunities: Decision[];
  plan: { bet: number; consider: number; skip: number };
  availableDates: string[];
  selectedDate: string;
  notice: string;
  last_updated?: string | null;
  warnings?: string[];
  dataSources?: NFLDataSources;
}

export default function NFLPage({ onSelectMatch }: { onSelectMatch: (decision: Decision) => void }) {
  const now = useNow();
  const location = useLocation();
  const requestedDate = new URLSearchParams(location.split('?')[1]).get('date') || '';
  const { data, loading, error, retry } = useFetch<NFLData>(`/api/dashboard/nfl?date=${encodeURIComponent(requestedDate)}`);
  const selected = requestedDate || data?.selectedDate || '';
  const dates = [...new Set([...(data?.availableDates ?? []), ...(selected ? [selected] : [])])].sort();
  const slate = (data?.slate ?? []).map(card => decisionAt(card, now));
  return <div className="page-stack">
    <section className="hero-card nfl-header">
      <div><p className="page-eyebrow"><span className="eyebrow-rule" />NFL · THE WEEK AHEAD</p>
      <h1 className="page-title">NFL Game Plan</h1>
      <p className="hero-description">This week’s games, recent form and the matchups worth a closer look.</p></div>
      <svg viewBox="0 0 150 110" className="nfl-symbol" fill="none" stroke="currentColor" aria-hidden="true"><path d="M10 92C-3 29 47-2 137 18c14 61-41 97-127 74Z" strokeWidth="2" /><path d="m44 71 59-35m-49 7 19 30m-1-40 19 30m-1-40 19 30M10 92l20-27m107-47-24 26" strokeWidth="3" /></svg>
    </section>
    {error ? <RequestError message={error} onRetry={retry} /> : <>
      {dates.length > 0 && <DateTabs dates={dates} selected={selected} onSelect={date => navigate(`/nfl?date=${date}`)} />}
      <PlanStrip {...slatePlan(slate)} loading={loading} />
      <DataStatus cards={slate} updated={data?.last_updated} loading={loading} warnings={data?.warnings} />
      <section className="section">
        <div className="section-heading-row"><h2 className="section-title">This week's matchups<span className="count-tag">{data?.slate.length ?? 0}</span></h2></div>
        {loading ? <div className="grid-2">{[0, 1, 2, 3].map(i => <CardSkeleton key={i} />)}</div>
          : slate.length ? <div className="grid-2">{slate.map(decision => <DecisionCard key={decision.matchId} decision={decision} onAnalyze={onSelectMatch} />)}</div>
          : <EmptyState title="No NFL games scheduled" body="No regular-season or postseason games were found for this date. Upcoming game dates appear here when available." />}
      </section>
      <details className="prop-about"><summary>Data sources</summary><NFLDataPanel sources={data?.dataSources} /></details>
    </>}
  </div>;
}
