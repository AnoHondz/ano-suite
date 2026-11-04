import { useEffect, useRef, type ReactNode } from 'react';
import type { Ladder } from '../propPricing';
import RequestError from './RequestError';
import { useNow } from '../hooks/useNow';
import { calendarFormat } from '../navigation';

export interface SportsbookQuote {
  bookmaker: string;
  bookmaker_key: string;
  side: 'over' | 'under';
  line: number;
  decimal_odds: number;
  implied_probability: number;
  fair_probability: number | null;
  model_chance: number | null;
  updated_at: string;
  expires_at: string;
  source: string;
}

export interface SportsbookStatus {
  status: string;
  reason: string;
  matched_picks: number;
  total_picks: number;
}

export interface PropPick {
  rationale?: string;
  line_source?: string;
  games?: number;
  name: string;
  team: string;
  position: string;
  label: string;
  side: 'over' | 'under' | 'yes';
  line: number | null;
  chance: number;
  record: number | null;
  probability: number;
  ladder?: Ladder | null;
  sportsbook_quotes?: SportsbookQuote[];
}

export interface RosterVerification {
  status?: string;
  observed_at?: string;
  expires_at: string;
}

const pct = (value: number) => `${Math.round(value * 100)}%`;
const SIDE = { over: 'Over', under: 'Under', yes: 'Yes' };

const validChance = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const quoteTime = (value: string) => calendarFormat(value, { hour: '2-digit', minute: '2-digit' }) + ' ET';
const freshQuote = (quote: SportsbookQuote, side: PropPick['side'], now: number) => Date.parse(quote.expires_at) > now
  && Date.parse(quote.updated_at) <= now && quote.side === side
  && Number.isFinite(quote.line) && quote.line >= 0 && Number.isFinite(quote.decimal_odds) && quote.decimal_odds > 1;

function PickRow({ pick, rank }: { pick: PropPick; rank: number }) {
  const now = useNow();
  const quotes = (pick.sportsbook_quotes || []).filter(quote => freshQuote(quote, pick.side, now));
  const quote = quotes[0];
  if (pick.line_source === 'sportsbook' && !quote) return null;
  const chance = quote ? quote.model_chance : pick.chance;
  const line = quote ? quote.line : pick.line;
  return <li className="pick-row">
    <span className="pick-rank">{rank}</span>
    <div className="pick-body">
      <b>{pick.name}</b> <span className="quiet-label">{pick.team} · {pick.position}</span>
      <p className="pick-call">{SIDE[pick.side]}{line != null ? ` ${line}` : ''} {pick.label}</p>
      {quote ? <>
        <p className="quiet-label pick-market-source">{quote.bookmaker} · {quote.decimal_odds.toFixed(2)} decimal odds</p>
        <p className="quiet-label">{validChance(quote.fair_probability) ? `Market fair ${pct(quote.fair_probability)} · ` : ''}Updated {quoteTime(quote.updated_at)}</p>
        <details className="prop-about pick-comparison"><summary>Compare sportsbooks</summary>
          {quotes.map(item => <p key={item.bookmaker_key}>{item.bookmaker}: {SIDE[item.side]} {item.line} · {item.decimal_odds.toFixed(2)} decimal
            {validChance(item.model_chance) ? ` · Model ${pct(item.model_chance)}` : ' · Model comparison unavailable'} · {quoteTime(item.updated_at)}</p>)}
          {pick.line_source !== 'sportsbook' && <p>Historical reference: {SIDE[pick.side]} {pick.line} {pick.label}. Model {pct(pick.chance)}; observed record {pick.record == null ? 'unavailable' : pct(pick.record)}.</p>}
          <p>Market fair removes the margin from the same book’s over/under pair. These comparisons are experimental and do not establish a betting edge.</p>
        </details>
      </> : pick.line_source === 'model_target' ? <>
        <p className="quiet-label">Model target · verify the offered line before betting</p>
        <p className="quiet-label">{pick.rationale}</p>
      </> : <>
        <p className="quiet-label">Historical target · no sportsbook quote</p>
        <p className="quiet-label">Historical hit rate at this target: {pick.record == null ? 'Unavailable' : pct(pick.record)}{pick.games != null ? ` · ${pick.games} games` : ''}</p>
        {pick.games != null && pick.games < 8 && <p className="quiet-label">Limited player sample</p>}
      </>}
    </div>
    <div className="pick-side">
      <strong className="pick-chance">{validChance(chance) ? pct(chance) : 'Unavailable'}</strong>
      <span className="quiet-label pick-estimate-label">Model estimate</span>
    </div>
  </li>;
}

export default function PropPicks({ title, picks, sportsbook, roster, received, onRetry, loading, unavailable, error, about }: {
  title: string; picks?: PropPick[]; received: boolean; onRetry: () => void; loading: boolean;
  sportsbook?: SportsbookStatus;
  roster?: RosterVerification;
  unavailable: string | null | false | undefined; error: ReactNode; about: ReactNode;
}) {
  const now = useNow();
  const rosterExpired = Boolean(roster && (!Number.isFinite(Date.parse(roster.expires_at)) || Date.parse(roster.expires_at) <= now));
  const renewed = useRef('');
  const expired = [...(picks || []).flatMap(pick => pick.sportsbook_quotes || []).map(quote => quote.expires_at)
    .filter(value => Date.parse(value) <= now), ...(rosterExpired ? [roster!.expires_at] : [])].sort().join('|');
  useEffect(() => {
    if (!unavailable && !loading && expired && renewed.current !== expired) {
      renewed.current = expired;
      onRetry();
    }
  }, [expired, loading, onRetry, unavailable]);
  const matched = (picks || []).filter(pick => pick.sportsbook_quotes?.some(quote => freshQuote(quote, pick.side, now))).length;
  const marketStatus = sportsbook?.status === 'available'
    ? matched ? `${matched} of ${picks?.length} props matched to fresh sportsbook lines.` : 'Sportsbook quotes expired. Historical targets shown until prices refresh.'
    : sportsbook?.reason || 'Sportsbook comparison unavailable. Targets below come from historical statistics.';
  // A response without a picks list (for example an outdated server) must not render as an empty panel.
  const incomplete = received && !Array.isArray(picks);
  return <section className="section" aria-label={title} id="player-props">
    <div className="section-heading-row"><h2 className="section-title">{title}</h2><span className="research-badge">Experimental</span></div>
    {unavailable && <p className="coverage-note" role="status">{unavailable}</p>}
    {!unavailable && loading && <div className="card"><p className="quiet-label" role="status">Finding the best picks…</p></div>}
    {!unavailable && error}
    {!unavailable && incomplete && <RequestError message="Player props returned no picks. Restart the app (Start-ScorPred.ps1 -Restart) if this persists." onRetry={onRetry} />}
    {!unavailable && rosterExpired && <p className="coverage-note" role="status">Refreshing current player availability…</p>}
    {!unavailable && !rosterExpired && picks && sportsbook && <p className="quiet-label prop-market-status" role="status">{marketStatus}</p>}
    {!unavailable && !rosterExpired && picks && (picks.length
      ? <ol className="card pick-list">{picks.map((pick, index) => <PickRow key={`${pick.name}-${pick.label}`} pick={pick} rank={index + 1} />)}</ol>
      : <p className="coverage-note">No strong picks for this game.</p>)}
    {!unavailable && picks && <details className="prop-about"><summary>How picks are chosen</summary>{about}</details>}
  </section>;
}
