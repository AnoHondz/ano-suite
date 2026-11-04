import { useState } from 'react';
import BestProps from '../components/BestProps';
import { ArrowLeft, ArrowRight, CalendarDays, Layers3 } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { CardSkeleton, DecisionCard, EmptyState, PlanStrip, type Decision } from '../components/DecisionCard';
import RequestError from '../components/RequestError';
import { calendarDate, calendarFormat, navigate, useLocation } from '../navigation';
import { decisionAt, pregameFailure } from '../decisionLifecycle';
import { useNow } from '../hooks/useNow';
import { slatePlan } from '../slate';
import DataStatus from '../components/DataStatus';
import { MatchupStage, SportPortals, CoverageVisual } from '../components/immersive/CommandCenter';

interface SportData {
  topOpportunities: Decision[];
  slate: Decision[];
  selectedDate?: string;
  notice?: string;
  warnings?: string[];
  last_updated?: string | null;
}
type SportFilter = 'All' | 'Soccer' | 'NBA' | 'NFL';

export default function HomePage({ onSelectMatch }: { onSelectMatch: (d: Decision) => void }) {
  const now = useNow();
  const [sport, setSport] = useState<SportFilter>('All');
  const location = useLocation();
  const requestedDate = new URLSearchParams(location.split('?')[1]).get('date') || '';
  const date = requestedDate || calendarDate(now);
  const suffix = requestedDate ? `?date=${encodeURIComponent(requestedDate)}` : '';
  const soccer = useFetch<SportData>(`/api/dashboard/soccer${suffix}`);
  const nba = useFetch<SportData>(`/api/dashboard/nba${suffix}`);
  const nfl = useFetch<SportData>(`/api/dashboard/nfl${suffix}`);
  const groups = [
    { name: 'Soccer', result: soccer, measure: 'Model win probability', path: '/soccer' },
    { name: 'NBA', result: nba, measure: 'Model win probability', path: '/nba' },
    { name: 'NFL', result: nfl, measure: 'Scoring scenario share', path: '/nfl' },
  ].filter(group => sport === 'All' || sport === group.name).map(group => ({
    ...group,
    cards: (group.result.data?.slate ?? []).map(card => decisionAt(card, now)).filter(card =>
      card.teamA && card.teamB && card.teamA !== 'TBD' && card.teamB !== 'TBD'),
  }));
  const loading = groups.some(group => group.result.loading);
  const plan = slatePlan(groups.flatMap(group => group.cards));
  const featured = groups.find(group => group.name === 'NFL')?.cards[0] || groups.flatMap(group => group.cards)[0];
  function moveDate(offset: number) {
    const next = new Date(`${date}T12:00:00Z`);
    next.setUTCDate(next.getUTCDate() + offset);
    navigate(`/?date=${next.toISOString().slice(0, 10)}`);
  }

  return <div className="page-stack">
    <section className="page-intro">
      <div><p className="page-eyebrow"><span className="eyebrow-rule" />THE MATCHDAY PERSPECTIVE</p>
        <h1 className="page-title">Today’s slate<span className="title-period">.</span></h1>
        <p className="page-description">Your sports. The evidence. A clearer game plan.</p></div>
      <div className="date-navigation" aria-label="Slate date navigation">
        <button className="icon-button" onClick={() => moveDate(-1)} aria-label="Previous day"><ArrowLeft size={16} /></button>
        <label className="date-input-label"><CalendarDays size={16} /><span className="sr-only">Slate date</span>
          <input type="date" value={date} onChange={event => { if (event.target.value) navigate(`/?date=${event.target.value}`); }} /></label>
        <button className="icon-button" onClick={() => moveDate(1)} aria-label="Next day"><ArrowRight size={16} /></button>
      </div>
    </section>
    <SportPortals />
    <div className="dashboard-controls">
      <div className="sport-filter" aria-label="Filter by sport">
        {(['All', 'Soccer', 'NBA', 'NFL'] as SportFilter[]).map(item => <button key={item} type="button" onClick={() => setSport(item)} aria-pressed={sport === item} className={sport === item ? 'sport-filter-active' : ''}>
          {item === 'All' && <Layers3 size={14} />}{item === 'All' ? 'All sports' : item}</button>)}
      </div>
      <span className="quiet-label">Dates & kickoff times in US Eastern</span>
    </div>
    <div className="command-overview">
      <MatchupStage sport={featured?.sport || 'nfl'} decision={featured} onSelect={onSelectMatch} />
      <aside className="command-aside" aria-label="Command center overview">
        <PlanStrip {...plan} loading={loading || groups.some(group => Boolean(group.result.error))} />
        <CoverageVisual groups={groups.map(group => ({ name: group.name, count: group.cards.length, loading: group.result.loading || Boolean(group.result.error) }))} />
      </aside>
    </div>
    <BestProps date={date} />
    {(sport === 'All' || sport === 'NFL') && <p className="coverage-note"><span className="note-dot" />NFL scenarios are experimental. Scenario shares are not calibrated win probabilities and cannot be compared with the soccer and NBA numbers.</p>}
    {groups.map(group => <section key={group.name} className="section sport-section" aria-label={`${group.name} shortlist`}>
      <div className="section-heading-row"><div className="sport-section-heading"><span className={`sport-monogram sport-${group.name.toLowerCase()}`}>{group.name === 'Soccer' ? 'SC' : group.name}</span>
        <div><h2 className="section-title">{group.name}<span className="count-tag">{group.result.loading ? '—' : group.cards.length}</span></h2><p className="quiet-label">{group.measure}{group.name === 'NFL' ? ' · Experimental' : ''}{group.result.data?.selectedDate && group.result.data.selectedDate !== date ? ` · ${calendarFormat(`${group.result.data.selectedDate}T12:00:00Z`, { month: 'short', day: 'numeric' })}` : ''}</p></div></div>
        <button className="text-button" onClick={() => navigate(`${group.path}${requestedDate ? `?date=${requestedDate}` : ''}`)}>Explore {group.name}<ArrowRight size={14} /></button></div>
      {group.result.error && <RequestError message={group.result.error} onRetry={group.result.retry} />}
      {group.result.data?.warnings?.map(warning => <p key={warning} className="coverage-note">{warning}</p>)}
      {group.result.data?.notice && <p className="coverage-note">{group.result.data.notice}</p>}
      {group.result.loading ? <div className="grid-2">{[0, 1].map(item => <CardSkeleton key={item} />)}</div>
        : group.cards.length ? <div className="grid-2">{group.cards.map((decision, index) => <DecisionCard key={decision.matchId || `${decision.matchup}-${index}`} decision={decision} onAnalyze={pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now) ? undefined : onSelectMatch} />)}</div>
        : !group.result.error && <EmptyState title={`No ${group.name} games scheduled`} body="No confirmed fixtures were returned. Select another date or open the sport dashboard." />}
      <DataStatus cards={group.cards} updated={group.result.data?.last_updated} loading={group.result.loading} />
    </section>)}
  </div>;
}
