import { ArrowRight } from 'lucide-react';
import type { CSSProperties, ReactNode, PointerEvent } from 'react';
import { navigate } from '../../navigation';
import { decisionAt } from '../../decisionLifecycle';
import { useNow } from '../../hooks/useNow';
import type { Decision } from '../DecisionCard';
import type { ArenaSport } from './SportsArena';
import PredictionField from './PredictionField';

const SPORT_LABELS = { nfl: 'NFL', nba: 'NBA', soccer: 'Soccer' };

export function DepthSurface({ children, className = '' }: { children: ReactNode; className?: string }) {
  function move(event: PointerEvent<HTMLElement>) {
    if (!matchMedia('(pointer:fine)').matches || matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    const rect = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty('--tilt-x', `${((event.clientY - rect.top) / rect.height - .5) * -3}deg`);
    event.currentTarget.style.setProperty('--tilt-y', `${((event.clientX - rect.left) / rect.width - .5) * 3}deg`);
  }
  return <div className={`depth-surface ${className}`} onPointerMove={move} onPointerLeave={event => {
    event.currentTarget.style.setProperty('--tilt-x', '0deg'); event.currentTarget.style.setProperty('--tilt-y', '0deg');
  }}>{children}</div>;
}

export function SportPortals() {
  return <nav className="sport-portals" aria-label="Explore sports">{(['nfl', 'nba', 'soccer'] as const).map(sport =>
    <DepthSurface key={sport}><button className={`sport-portal portal-${sport}`} onClick={() => navigate(`/${sport}`)}>
      <span className={`portal-orb orb-${sport}`} aria-hidden="true" /><span><strong>{SPORT_LABELS[sport]}</strong><small>{sport === 'nba' ? 'Courtside context & matchups' : sport === 'nfl' ? 'The week ahead, in focus' : 'Fixtures, form & perspective'}</small></span><ArrowRight size={18} />
    </button></DepthSurface>)}</nav>;
}

export function AnimatedIndicator({ label, value, share, tone = 'emerald' }: { label: string; value: string | number; share?: number; tone?: string }) {
  return <div className={`animated-indicator indicator-${tone}`} data-unavailable={value === 'Unavailable'}><span>{label}</span><strong>{value}</strong>
    {share != null && Number.isFinite(share) && <div className="indicator-track"><span style={{ '--share': Math.max(0, Math.min(1, share)) } as CSSProperties} /></div>}
  </div>;
}

export function MatchupStage({ sport, decision: raw, onSelect, primary = false }: {
  sport: ArenaSport; decision?: Decision | null; onSelect?: (decision: Decision) => void; primary?: boolean;
}) {
  const now = useNow();
  const decision = raw ? decisionAt(raw, now) : null;
  if (!decision) return null;
  return <PredictionField sport={sport} decision={decision} primary={primary} onSelect={onSelect} />;
}

export function CoverageVisual({ groups }: { groups: { name: string; count: number; loading: boolean }[] }) {
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  const complete = groups.every(group => !group.loading);
  return <section className="coverage-visual card" aria-label="Schedule coverage visualization"><div><p className="page-eyebrow">SIGNAL OVERVIEW</p><h2 className="section-title">The available slate.</h2><p className="quiet-label">Observed matchups by sport. Counts are not confidence ratings.</p></div>
    <div className="coverage-columns">{groups.map(group => <AnimatedIndicator key={group.name} label={group.name} value={group.loading ? '…' : group.count} share={complete && total ? group.count / total : undefined} tone={group.name === 'NFL' ? 'blue' : group.name === 'NBA' ? 'purple' : 'emerald'} />)}</div>
  </section>;
}
