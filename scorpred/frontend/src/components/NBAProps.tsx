import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import RequestError from './RequestError';
import type { Decision } from './DecisionCard';
import { pregameFailure } from '../decisionLifecycle';
import { calendarDate } from '../navigation';
import PropPicks, { type PropPick, type SportsbookStatus, type RosterVerification } from './PropPicks';

export default function NBAProps({ decision }: { decision: Decision }) {
  const now = useNow();
  const parentFailure = pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now);
  const params = new URLSearchParams();
  if (decision.slateDate || decision.matchDate) params.set('date', decision.slateDate || calendarDate(decision.matchDate!));
  const { data, loading, error, retry, status } = useFetch<{ result: { picks: PropPick[]; sportsbook?: SportsbookStatus; roster_verification?: RosterVerification } }>(decision.matchId && !parentFailure
    ? `/api/nba/matches/${encodeURIComponent(decision.matchId)}/props?${params}` : null);
  return <PropPicks title="Player props" received={!parentFailure && Boolean(data)} onRetry={retry} loading={!parentFailure && loading}
    unavailable={parentFailure || (error && status === 422 ? error : null)}
    error={!parentFailure && error && status !== 422 ? <RequestError message={error} onRetry={retry} /> : null}
    picks={data?.result?.picks} sportsbook={data?.result?.sportsbook} roster={data?.result?.roster_verification} about={<>
      <p>The five props where the model most disagrees with each regular’s recent games: points, rebounds, assists, threes and points + rebounds + assists, from ESPN box scores this season and last. Players on the injury report are left out.</p>
      <p>Fresh sportsbook targets and prices are matched automatically when available. Otherwise, targets use recent-game medians at current minutes. Model estimates at sportsbook targets are experimental; historical-target backtests do not establish accuracy against sportsbooks.</p>
    </>} />;
}
