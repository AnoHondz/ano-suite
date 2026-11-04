import { useEffect, useState } from 'react';
import { useFetch } from '../hooks/useFetch';
import type { Decision, SourceFreshness } from './DecisionCard';
import { calendarDate, calendarFormat } from '../navigation';
import NBAEarlySeasonResearch from './NBAEarlySeasonResearch';
import { useNBARefresh } from '../hooks/useNBARefresh';

interface Summary {
  games: number; wins: number; losses: number; points_for: number | null; points_against: number | null;
  scope: string; recent_results: { game_id: string; date: string; opponent: string; result: string;
    points_for: number; points_against: number; home: boolean }[];
}
interface TeamContext {
  team: { id: string; name: string }; current: Summary; previous: Summary;
  current_season: number; previous_season: number; current_status: string; current_source: SourceFreshness;
  historical_source: { files: string[]; historical_pregame_availability: string };
}
interface Context {
  version: string; role: string; refresh_pending: boolean; statistics_cutoff: string; notice: string;
  teams: { home: TeamContext; away: TeamContext };
  game: { schedule_source?: string; result?: { home: { points: number }; visitors: { points: number } } | null };
}

export default function NBAContext({ decision }: { decision: Decision }) {
  const day = decision.slateDate || (decision.matchDate ? calendarDate(decision.matchDate) : '');
  const { data, loading, error, retry } = useFetch<{ result: Context }>(decision.matchId
    ? `/api/nba/matches/${encodeURIComponent(decision.matchId)}/context?date=${day}` : null);
  const [chosen, setPeriod] = useState<'current' | 'previous' | null>(null);
  const result = data?.result;
  const deadlines = result ? [result.teams.home, result.teams.away]
    .map(team => Date.parse(team.current_source?.expires_at || '')).filter(Number.isFinite) : [];
  useNBARefresh(deadlines.length ? Math.min(...deadlines) : null, retry,
    decision.matchDate ? Date.parse(decision.matchDate) : undefined);
  const period = chosen ?? (result?.teams.home.current.games && result.teams.away.current.games ? 'current' : 'previous');
  useEffect(() => {
    if (!result?.refresh_pending) return;
    const timeout = setTimeout(retry, 2000);
    return () => clearTimeout(timeout);
  }, [result, retry]);
  if (loading) return <section className="card" aria-label="NBA team comparisons"><p role="status">Loading available team comparisons…</p></section>;
  if (error) return <section className="card" aria-label="NBA team comparisons"><p className="coverage-note" role="status">Team comparisons unavailable: {error}</p><button className="text-button" onClick={retry}>Retry comparisons</button></section>;
  if (!result) return null;
  const teams = [result.teams.home, result.teams.away];
  const season = period === 'current' ? teams[0].current_season : teams[0].previous_season;
  const summaries = teams.map(team => team[period]);
  return <section className="section nba-context" aria-label="NBA team comparisons">
    <div className="section-heading-row"><h2 className="section-title">Team comparison</h2><span className="quiet-label">Descriptive</span></div>
    {result.game.schedule_source === 'cached_schedule' && <p className="coverage-note" role="status">Showing last observed matchup details while the schedule source is unavailable. Team comparisons remain descriptive.</p>}
    <div className="sport-filter" aria-label="NBA comparison period">
      <button aria-pressed={period === 'current'} onClick={() => setPeriod('current')}>Current season</button>
      <button aria-pressed={period === 'previous'} onClick={() => setPeriod('previous')}>Previous season</button>
    </div>
    <div className="card compact-comparison-card"><div className="table-scroll"><table className="evaluation-table compact-comparison">
      <caption>{season - 1}–{String(season).slice(-2)} · {period === 'current' ? 'Current observations' : 'Existing local archive'}</caption>
      <thead><tr><th scope="col">Statistic</th>{teams.map(team => <th key={team.team.id} scope="col">{team.team.name}</th>)}</tr></thead>
      <tbody>
        <tr><th scope="row">Observed record</th>{summaries.map((summary, i) => <td key={i}>{summary.games ? `${summary.wins}–${summary.losses}` : 'Unavailable'}</td>)}</tr>
        <tr><th scope="row">Games in sample</th>{summaries.map((summary, i) => <td key={i}>{summary.games}</td>)}</tr>
        <tr><th scope="row">Points / game</th>{summaries.map((summary, i) => <td key={i}>{summary.points_for?.toFixed(1) ?? 'Unavailable'}</td>)}</tr>
        <tr><th scope="row">Conceded / game</th>{summaries.map((summary, i) => <td key={i}>{summary.points_against?.toFixed(1) ?? 'Unavailable'}</td>)}</tr>
      </tbody>
    </table></div></div>
    {period === 'current' && teams.filter(team => ['unavailable', 'refreshing'].includes(team.current_status)).map(team => <p className="coverage-note" role="status" key={team.team.id}>{team.team.name}: {team.current_status === 'refreshing' ? 'Source refresh in progress; historical context remains available.' : 'Current source unavailable. Zero displayed observations do not establish a 0–0 season record.'}</p>)}
    <details className="prop-about"><summary>Recent results</summary><div className="grid-2">{teams.map(team => <article key={team.team.id}>
      <h3 className="panel-title">{team.team.name}</h3>
      {team[period].recent_results.length ? <div className="table-scroll"><table className="evaluation-table"><caption>Recent results in this sample</caption>
          <thead><tr><th>Date</th><th>Opponent</th><th>Result</th><th>Score</th></tr></thead><tbody>{team[period].recent_results.map(row => <tr key={row.game_id}>
            <td>{calendarFormat(row.date, { month: 'short', day: 'numeric' })}</td><td>{row.opponent} · {row.home ? 'H' : 'A'}</td><td>{row.result}</td><td>{row.points_for}–{row.points_against}</td>
        </tr>)}</tbody></table></div> : <p className="coverage-note">No eligible completed games in the available sample.</p>}
    </article>)}</div></details>
    {result.game.result && <p className="coverage-note">Recorded final score: {decision.teamA} {result.game.result.home.points} – {result.game.result.visitors.points} {decision.teamB}. This is an observed result, not a prediction.</p>}
    <NBAEarlySeasonResearch key={decision.matchId} decision={decision} />
    <details className="prop-about"><summary>Availability, sources & prediction policy</summary><p className="quiet-label">{result.notice}</p>
      {teams.map(team => <div key={team.team.id}><p className="quiet-label">{team.team.name}: {team[period].scope}. Coverage may be incomplete; no possession-based efficiency is implied.</p>
        {period === 'current' ? <p className="quiet-label">Source observed: {team.current_source?.observed_at ? `${calendarFormat(team.current_source.observed_at, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET` : 'Unverified'}. State: {team.current_source?.state || 'Unavailable'}.</p>
          : <p className="quiet-label">{team.historical_source.files.length} existing archive files · original receipt time unverified.</p>}
      </div>)}
      <p className="quiet-label">Statistics cutoff: {result.statistics_cutoff}. Version: {result.version}.</p>
      <p className="quiet-label">Prediction availability follows the installed method's sample requirements, source freshness and pregame checks. These team comparisons remain descriptive and do not provide a probability fallback.</p>
    </details>
  </section>;
}
