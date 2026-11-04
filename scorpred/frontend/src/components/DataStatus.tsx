import type { Decision } from './DecisionCard';
import { calendarFormat } from '../navigation';
import { useNow } from '../hooks/useNow';

/** Source observation time is never replaced by the time a page was rendered. */
export default function DataStatus({ cards, updated, loading, warnings = [] }: {
  cards: Decision[]; updated?: string | null; loading?: boolean; warnings?: string[];
}) {
  const now = useNow();
  const sources = cards.map(card => card.dataFreshness);
  const dates = sources.map(source => source?.observed_at).filter((value): value is string => Boolean(value));
  if (updated) dates.push(updated);
  const observed = dates.filter(value => Number.isFinite(Date.parse(value))).sort((a, b) => Date.parse(a) - Date.parse(b))[0];
  const delayed = sources.some(source => !source || source.state !== 'fresh'
    || !Number.isFinite(Date.parse(source.expires_at || '')) || Date.parse(source.expires_at || '') <= now);
  // Quiet when everything is current: only a delay or a provider warning earns a line.
  if (loading || (!delayed && observed && !warnings.length) || !cards.length && !warnings.length) return null;
  return <div className="data-status" aria-label="Data availability">
    {(delayed || !observed) && <p className="quiet-label">{observed
      ? `Data from ${calendarFormat(observed, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET · update needed; some estimates may be unavailable`
      : 'Data update time unavailable.'}</p>}
    {warnings.map(warning => <p key={warning} className="coverage-note">{warning}</p>)}
  </div>;
}
