import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import RequestError from './RequestError';
import type { Decision } from './DecisionCard';
import { calendarDate } from '../navigation';
import { pregameFailure } from '../decisionLifecycle';
import PropPicks, { type PropPick, type SportsbookStatus, type RosterVerification } from './PropPicks';

interface PropsResult {
  picks: PropPick[];
  data_through_week: number;
  notice: string;
  sportsbook?: SportsbookStatus;
  roster_verification?: RosterVerification;
}

export default function NFLProps({ decision }: { decision: Decision }) {
  const now = useNow();
  const parentFailure = pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now);
  const params = new URLSearchParams();
  if (decision.slateDate || decision.matchDate) params.set('date', decision.slateDate || calendarDate(decision.matchDate!));
  const { data, loading, error, retry, status } = useFetch<{ result: PropsResult }>(decision.matchId && !parentFailure
    ? `/api/nfl/matches/${encodeURIComponent(decision.matchId)}/props?${params}` : null);
  const result = data?.result;
  return <PropPicks title="Player props" received={!parentFailure && Boolean(data)} onRetry={retry} loading={!parentFailure && loading}
    unavailable={parentFailure || (error && (status === 422 || status === 423) ? error : null)}
    error={!parentFailure && error && status !== 422 && status !== 423 ? <RequestError message={error} onRetry={retry} /> : null}
    picks={result?.picks} sportsbook={result?.sportsbook} roster={result?.roster_verification} about={result && <>
      <p>The five props where the model most disagrees with each player’s own season, from box scores through week {result.data_through_week}. Injured or questionable players are left out.</p>
      <p>Fresh sportsbook targets and prices are matched automatically when available. Otherwise, targets use the player's season median. Model estimates at sportsbook targets are experimental; the historical-target backtest does not validate sportsbook performance.</p>
    </>} />;
}
