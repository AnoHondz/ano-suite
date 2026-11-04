import { useEffect } from 'react';
import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import { calendarDate } from '../navigation';
import RequestError from './RequestError';
import type { Decision } from './DecisionCard';
import { pregameFailure } from '../decisionLifecycle';
import PropPicks, { type PropPick, type SportsbookStatus, type RosterVerification } from './PropPicks';


export default function SoccerProps({ decision }: { decision: Decision }) {
  const now = useNow();
  const parentFailure = pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now);
  const params = new URLSearchParams();
  if (decision.leagueId) params.set('league_id', String(decision.leagueId));
  if (decision.slateDate || decision.matchDate) params.set('date', decision.slateDate || calendarDate(decision.matchDate!));
  const { data, loading, error, retry, status } = useFetch<{ refresh_pending?: boolean; result?: { picks: PropPick[]; sportsbook?: SportsbookStatus; roster_verification?: RosterVerification } }>(decision.matchId && !parentFailure
    ? `/api/soccer/matches/${encodeURIComponent(decision.matchId)}/props?${params}` : null);
  const result = data?.result;
  const pending = Boolean(data?.refresh_pending);
  useEffect(() => {
    if (!pending || parentFailure || error) return;
    const timer = window.setTimeout(retry, 4000);
    return () => window.clearTimeout(timer);
  }, [pending, parentFailure, error, retry, data]);
  return <><PropPicks title="Player props" received={!parentFailure && Boolean(result)} onRetry={retry} loading={!parentFailure && (loading || pending)}
    unavailable={parentFailure || (error && status === 422 ? error : null)}
    error={!parentFailure && error && status !== 422 ? <RequestError message={error} onRetry={retry} /> : null}
    picks={data?.result?.picks} sportsbook={data?.result?.sportsbook} roster={data?.result?.roster_verification} about={<>
      <p>Picks combine recent player output, expected minutes, team production and the opponent’s allowed statistics. Model targets use fixed thresholds and require agreement between the trained count model and the matchup baseline, at least eight comparable player games and three team games.</p>
      <p>Verified sportsbook opportunities use actual offered lines and prices. Other picks are clearly labeled model targets; their probabilities do not establish a betting edge. Goals and assists are evaluated for the over only.</p>
      <p>Players must appear on a fresh current-season roster. ESPN, FPL, StatsBomb and Wyscout supply the underlying research data. No side quota forces overs or unders.</p>
    </>} />
    </>;
}
