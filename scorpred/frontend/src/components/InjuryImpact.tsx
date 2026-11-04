import { useFetch } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import RequestError from './RequestError';
import type { Decision, SourceFreshness } from './DecisionCard';
import { calendarDate, calendarFormat } from '../navigation';
import { pregameFailure } from '../decisionLifecycle';

interface InjuredPlayer {
  name: string;
  position: string;
  status: string;
  injury: string;
  chance_of_playing: number;
  impact: number | null;
  sources: string[];
}

interface TeamImpact {
  side: 'home' | 'away';
  team: string;
  win_now: number;
  win_if_fit: number;
  players: InjuredPlayer[];
}

interface ImpactResult {
  teams: TeamImpact[];
  draw_now?: number;
  sources: SourceFreshness[];
  warnings: string[];
  notice: string;
}

// Below this a listed player barely moves the win chance; group them in one line.
const NOTABLE = 0.5;

function TeamCard({ team }: { team: TeamImpact }) {
  const notable = team.players.filter(p => p.impact != null && p.impact >= NOTABLE);
  const others = team.players.filter(p => !notable.includes(p));
  const gap = team.win_if_fit - team.win_now;
  return <section className="card injury-team" aria-label={`${team.team} injuries`}>
    <h3 className="panel-title">{team.team}</h3>
    <p className="injury-win"><strong>{Math.round(team.win_now)}%</strong> win chance</p>
    <p className="quiet-label">{gap >= NOTABLE ? `${Math.round(team.win_if_fit)}% if everyone listed were fit` : 'Injuries barely change this team’s chances'}</p>
    {notable.length > 0 && <ul className="injury-list">{notable.map(p => <li key={p.name}>
      <span><b>{p.name}</b> <span className="quiet-label">{p.position}</span><br /><span className="injury-status">{p.status}{p.injury ? ` · ${p.injury}` : ''}</span></span>
      <span className="injury-cost" aria-label={`${p.impact!.toFixed(1)} points of win chance`}>−{p.impact!.toFixed(1)}%</span>
    </li>)}</ul>}
    {others.length > 0 && <p className="quiet-label injury-others">Also listed: {others.map(p => `${p.name} (${p.status})`).join(', ')}</p>}
    {team.players.length === 0 && <p className="quiet-label">No injuries reported.</p>}
  </section>;
}

export default function InjuryImpact({ decision }: { decision: Decision }) {
  const now = useNow();
  const sport = decision.sport ?? 'soccer';
  const parentFailure = pregameFailure(decision.matchDate, decision.eventStatus, decision.dataFreshness, now);
  const params = new URLSearchParams();
  if (sport === 'soccer' && decision.leagueId) params.set('league_id', String(decision.leagueId));
  if (decision.slateDate || decision.matchDate) params.set('date', decision.slateDate || calendarDate(decision.matchDate!));
  const { data, loading, error, retry, status } = useFetch<{ result: ImpactResult }>(decision.matchId && !parentFailure
    ? `/api/${sport}/matches/${encodeURIComponent(decision.matchId)}/injuries?${params}` : null);
  const result = data?.result;
  const valid = Array.isArray(result?.teams) && result.teams.length === 2;
  const updated = result?.sources?.map(source => source.observed_at).filter(Boolean).sort()[0];
  return <section className="section" aria-label="Injury impact" id="injury-impact">
    <div className="section-heading-row"><h2 className="section-title">Injuries</h2><span className="research-badge">Experimental</span></div>
    {parentFailure && <p className="coverage-note" role="status">{parentFailure}</p>}
    {!parentFailure && loading && <div className="card"><p className="quiet-label" role="status">Checking injury reports…</p></div>}
    {!parentFailure && error && (status === 422 || status === 423 ? <p className="coverage-note" role="status">{error}</p> : <RequestError message={error} onRetry={retry} />)}
    {!parentFailure && data && !valid && <RequestError message="The injury service returned incomplete data. Please try again." onRetry={retry} />}
    {!parentFailure && valid && <>
      <div className="grid-2">{result!.teams.map(team => <TeamCard key={team.side} team={team} />)}</div>
      {result!.draw_now != null && <p className="quiet-label">Draw: {Math.round(result!.draw_now)}%</p>}
      {result!.warnings.map(warning => <p key={warning} className="coverage-note">{warning}</p>)}
      <details className="prop-about"><summary>How this works</summary>
        <p>{result!.notice}</p>
        <p>Each listed player shows how much win chance the team would gain if they were fit. Players who missed most recent games are already reflected in the team’s form.
          {updated && ` Reports checked ${calendarFormat(updated, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET.`}</p>
      </details>
    </>}
  </section>;
}
