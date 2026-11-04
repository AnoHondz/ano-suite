import { useState } from 'react';
import HistoricalPerformance from '../components/HistoricalPerformance';
import TrackRecord from '../components/TrackRecord';
import NFLPregameResearch from '../components/NFLPregameResearch';
import { DashboardCard } from '../components/DashboardTheme';
import { DecisionCard, EmptyState, PlanStrip, type Decision } from '../components/DecisionCard';
import { useFetch } from '../hooks/useFetch';
import RequestError from '../components/RequestError';
import { decisionAt } from '../decisionLifecycle';
import { useNow } from '../hooks/useNow';
import { slatePlan } from '../slate';
import DataStatus from '../components/DataStatus';
import { MatchupStage } from '../components/immersive/CommandCenter';

interface VolatilityRow {
  side: string;
  matchup: string;
  action: string;
  confidence: number;
  note: string;
}

interface InsightsData {
  radarCards: Decision[];
  slate?: Decision[];
  radarGroups?: Record<string, Decision[]>;
  volatilityRows: VolatilityRow[];
  confidenceGroups: Record<string, number>;
  plan: { bet: number; consider: number; skip: number };
  sportFilter: string;
  warnings?: string[];
}

const SPORT_FILTERS = ['All', 'Soccer', 'NBA', 'NFL'];

export default function InsightsPage({ onSelectMatch }: { onSelectMatch: (decision: Decision) => void }) {
  const now = useNow();
  const [sport, setSport] = useState('All');
  const [view, setView] = useState<'slate' | 'record' | 'history' | 'research'>('slate');
  const sportParam = sport === 'All' ? 'all' : sport.toLowerCase();
  function chooseSport(item: string) {
    setSport(item);
    const views = item === 'All' ? ['slate', 'record', 'history', 'research'] : item === 'NFL' ? ['slate', 'history', 'research'] : ['slate', 'record'];
    if (!views.includes(view)) setView('slate');
  }
  const { data, loading, error, retry } = useFetch<InsightsData>(`/api/dashboard/insights?sport=${sportParam}`);

  const groups = Object.entries(data?.radarGroups ?? { [sport.toLowerCase()]: data?.radarCards ?? [] }).map(([name, cards]) => ({ name, cards: cards.map(card => decisionAt(card, now)) }));
  const radarCards = groups.flatMap(group => group.cards);
  const slate = (data?.slate ?? radarCards).map(card => decisionAt(card, now));
  const ruleCards = slate.filter(card => card.sport !== 'nfl');
  const volatilityRows = (data?.volatilityRows ?? []).filter(row => radarCards.some(card => card.matchup === row.matchup && card.action !== 'SKIP'));
  const plan = slatePlan(slate);
  const confidenceGroups = {
    'Favourite at 66%+': ruleCards.filter(card => card.confidence >= 66).length,
    'Favourite at 55-65%': ruleCards.filter(card => card.confidence >= 55 && card.confidence < 66).length,
    'Favourite below 55%': ruleCards.filter(card => card.confidence < 55).length,
  };

  return (
    <div className="page-stack">
      <section className="hero-card">
        <p className="page-eyebrow">SPORTS INTELLIGENCE</p>
        <h1 className="page-title">Insights & model performance.</h1>
        <p className="mt-4 max-w-2xl text-slate-400">
          Review today’s evidence and explore historical evaluation. Each sport stays within its own measure.
        </p>
      </section>

      {view === 'slate' && <MatchupStage sport={slate[0]?.sport || 'nfl'} decision={slate[0]} onSelect={onSelectMatch} />}

      <div className="dashboard-controls">
        <div className="sport-filter" aria-label="Insights sport filter">
          {SPORT_FILTERS.map(item => <button key={item} type="button" onClick={() => chooseSport(item)} aria-pressed={item === sport} className={item === sport ? 'sport-filter-active' : ''}>{item === 'All' ? 'All sports' : item}</button>)}
        </div>
        <div className="sport-filter" aria-label="Insights view">
          <button onClick={() => setView('slate')} aria-pressed={view === 'slate'} className={view === 'slate' ? 'sport-filter-active' : ''}>Current slate</button>
          {sport !== 'NFL' && <button onClick={() => setView('record')} aria-pressed={view === 'record'} className={view === 'record' ? 'sport-filter-active' : ''}>Track record</button>}
          {(sport === 'All' || sport === 'NFL') && <button onClick={() => setView('history')} aria-pressed={view === 'history'} className={view === 'history' ? 'sport-filter-active' : ''}>Historical performance</button>}
          {(sport === 'All' || sport === 'NFL') && <button onClick={() => setView('research')} aria-pressed={view === 'research'} className={view === 'research' ? 'sport-filter-active' : ''}>Pregame research</button>}
        </div>
      </div>

      <div className="page-stack" hidden={view !== 'slate'}>
      <PlanStrip bet={plan.bet} consider={plan.consider} skip={plan.skip} loading={loading || Boolean(error)} />
      {(sport === 'All' || sport === 'NFL') && <p className="text-xs text-slate-400">NFL estimates use experimental scoring scenarios and have no validated accuracy.</p>}
      {error && <RequestError message={error} onRetry={retry} />}
      <DataStatus cards={slate} loading={loading} />
      {data?.warnings?.map(message => <RequestError key={message} message={message} onRetry={retry} />)}

      <section className="insight-stats">
        {Object.entries(confidenceGroups).map(([label, value]) => (
          <DashboardCard key={label} title={label}>
            <p>
              {loading || error ? '—' : value}
            </p>
          </DashboardCard>
        ))}
      </section>

      <section className="section">
        <div>
          <p className="section-label">Reads by sport</p>
          <h2 className="section-title">Compare each sport within its own measure.</h2>
          <p className="text-sm text-slate-400">NFL scenario share describes simulated scoring scenarios; it is not a calibrated win probability and cannot be compared with the soccer and NBA win probabilities.</p>
        </div>
        {loading ? (
          <div className="empty-state">
            <p className="font-oswald text-lg uppercase tracking-normal text-slate-400">Loading opportunities…</p>
          </div>
        ) : radarCards.length > 0 ? (
          <div className="page-stack">
            {groups.filter(group => group.cards.length).map(group => <section key={group.name}><h3 className="section-label">{group.name.toUpperCase()} · {group.name === 'nfl' ? 'Scoring scenario share' : 'Rule confidence'}</h3><div className="grid-2">{group.cards.map((decision) => (
              <div key={`${decision.matchup}-${decision.side}`} className="space-y-3">
                <DecisionCard decision={decision} featured onAnalyze={onSelectMatch} />
                <div className="grid grid-cols-2 gap-2 text-xs text-slate-400">
                  <span className="rounded-lg border border-white/[0.08] bg-white/[0.03] p-3">{decision.sport === 'nfl' ? 'Scenario share' : 'Rule confidence'}: {decision.side === 'Unavailable' ? 'Unavailable' : `${decision.confidence}%`}</span>
                  <span className="rounded-lg border border-white/[0.08] bg-white/[0.03] p-3">Trust: {decision.data}</span>
                </div>
              </div>
            ))}</div></section>)}
          </div>
        ) : (
          <EmptyState title="No reads available" body="No opportunities found for the selected sport filter." />
        )}
      </section>

      <section className="section">
        <div>
          <p className="section-label">Volatility Watch</p>
          <h2 className="section-title">Playable reads that need one more context check.</h2>
        </div>
        {loading ? (
          <div className="empty-state">
            <p className="font-oswald text-lg uppercase tracking-normal text-slate-400">Loading…</p>
          </div>
        ) : volatilityRows.length > 0 ? (
          <div className="grid-2">
            {volatilityRows.map((row) => (
              <article key={`${row.matchup}-${row.side}`} className="card">
                <p className="text-xs uppercase tracking-[0.14em] text-amber-600">{row.action}</p>
                <h3 className="mt-2 font-oswald text-2xl uppercase tracking-normal text-white">{row.side}</h3>
                <p className="mt-1 text-sm text-slate-500">{row.matchup}</p>
                  <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/[0.05]">
                    <div className="h-full rounded-full bg-amber-400" style={{ width: `${row.confidence}%` }} />
                  </div>
                  <p className="mt-4 text-sm text-slate-500">{row.note}</p>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState title={error ? 'Volatility data unavailable' : 'No volatility flags'} body={error ? 'Try again when the data service is available.' : 'No flags were returned for the current slate.'} />
        )}
      </section>
      </div>
      {view === 'record' && sport !== 'NFL' && <TrackRecord />}
      {view === 'history' && (sport === 'All' || sport === 'NFL') && <HistoricalPerformance />}
      {view === 'research' && (sport === 'All' || sport === 'NFL') && <NFLPregameResearch />}
    </div>
  );
}
