import type { Decision, SourceFreshness } from './components/DecisionCard';

export function pregameFailure(kickoff: string | undefined, status?: Decision['eventStatus'], source?: SourceFreshness | null, now = Date.now()) {
  const start = kickoff ? Date.parse(kickoff) : NaN;
  const state = typeof status === 'object' && status !== null ? status.state || status.short || status.name || status.long : status;
  const statuses = typeof status === 'object' && status !== null ? [status.state, status.short, status.name, status.long].filter(Boolean) : [state].filter(Boolean);
  if (!Number.isFinite(start) || start <= now || (typeof status === 'object' && status?.completed)
      || statuses.some(value => !['PRE', 'NS', 'SCHEDULED', 'STATUS_SCHEDULED', 'NOT STARTED', 'TIMED'].includes(String(value).trim().toUpperCase()))) {
    return 'Pregame recommendations are unavailable after kickoff or when the game is not scheduled.';
  }
  if (source && Object.keys(source).length) {
    const observed = Date.parse(source.observed_at || '');
    const expires = Date.parse(source.expires_at || '');
    const maximum = Date.parse(source.max_stale_at || source.expires_at || '');
    if (source.state !== 'fresh' || !Number.isFinite(observed) || observed - now > 1000 || !Number.isFinite(expires) || expires <= now || !Number.isFinite(maximum) || maximum <= now) {
      return 'Pregame recommendations are unavailable because their source data needs a fresh update.';
    }
  }
  return null;
}

export function decisionAt(decision: Decision, now = Date.now()): Decision {
  const reason = pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now);
  return reason ? { ...decision, researchPick: false, pickLabel: undefined, action: 'SKIP', side: 'Unavailable', reason, confidence: 0,
    winProbability: null, drawProbability: null, scenarioShare: null, tiedScenarioShare: null, market: null, data: 'Limited Data' } : { ...decision, market: marketAt(decision.market, now) };
}

export function marketAt(market: Decision['market'], now = Date.now()) {
  if (!market) return market;
  const updated = Date.parse(market.updated_at);
  if (!Number.isFinite(updated) || updated > now || now - updated >= 900000) return null;
  const source = market.data_freshness;
  if (source && (source.state !== 'fresh' || Date.parse(source.expires_at || '') <= now || !Number.isFinite(Date.parse(source.expires_at || '')))) return null;
  return market;
}
