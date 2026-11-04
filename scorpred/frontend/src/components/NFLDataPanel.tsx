import { ArrowUpRight, Database } from 'lucide-react';
import { useNow } from '../hooks/useNow';

export interface NFLResearchDataset {
  name: string; status: string; rows: number; season: number; latestWeek?: number | null;
  observedAt?: string | null; expiresAt?: string | null;
}
export interface NFLDataSources { form: string; odds: string; injuries: string; weather: string; provider?: string; warning?: string; teamStats?: string; depthCharts?: string; researchDatasets?: NFLResearchDataset[] }

const researchNames: Record<string, string> = {
  snap_counts: 'Player snap counts', ngs_passing: 'Next Gen passing', ngs_rushing: 'Next Gen rushing',
  ngs_receiving: 'Next Gen receiving', weekly_rosters: 'Weekly roster history',
  players: 'Player ID directory', injuries: 'Injury research snapshots',
};

export default function NFLDataPanel({ sources }: { sources?: NFLDataSources }) {
  const now = useNow();
  const items = [
    { title: 'Schedules & results', status: sources?.form, ready: `${sources?.provider === 'nflverse' ? 'nflverse' : 'ESPN fallback'} · completed scores` },
    { title: 'Weekly team stats', status: sources?.teamStats || 'not_synced', ready: 'nflverse · research features' },
    { title: 'Rosters & depth charts', status: sources?.depthCharts || 'not_synced', ready: 'nflverse · stored snapshots' },
    { title: 'Market odds', status: sources?.odds, ready: 'The Odds API · quote context' },
    { title: 'Pregame injuries', status: sources?.injuries, ready: 'Availability context' },
    { title: 'Game-day weather', status: sources?.weather, ready: 'Venue conditions' },
  ];
  return <section className="data-panel" aria-label="NFL data coverage">
    <div className="data-panel-header"><div><p className="page-eyebrow"><Database size={12} />BEHIND THE ESTIMATE</p>
      <h2>Better context starts with better data.</h2>
      <p className="data-panel-description">nflverse is the main historical and modeling data source. Current recommendations use experimental recent-scoring scenarios; imported stats and roster snapshots support research. No BET recommendations.</p>
    </div><a className="provider-link" href="https://nflreadpy.nflverse.com/" target="_blank" rel="noopener noreferrer">Explore nflverse <ArrowUpRight size={13} /></a></div>
    {sources?.warning && <p className="coverage-note mt-3"><span className="note-dot" />{sources.warning}</p>}
    <div className="source-grid">{items.map(item => {
      const label = item.status === 'available' ? item.ready : !item.status ? 'Checking coverage…' : item.status === 'not_synced' ? 'Not synchronized' : item.status === 'needs_refresh' ? 'Snapshot needs refresh' : ['not_configured', 'not_connected'].includes(item.status) ? 'Not connected' : 'Feed unavailable';
      return <div className="source-item" key={item.title}><span>{item.title}</span><p className={`source-status ${item.status || ''}`}><span className="source-dot" />{label}</p></div>;
    })}</div>
    {!!sources?.researchDatasets?.length && <>
      <p className="coverage-note mt-4">Free research data · imported snapshots, not live feeds. Row counts and the latest included week describe coverage, not completeness. Injury rows lack report timestamps and do not confirm game-day availability.</p>
      <div className="source-grid" aria-label="Free NFL research datasets">{sources.researchDatasets.filter(dataset => researchNames[dataset.name]).map(dataset => {
        const expired = !dataset.expiresAt || !Number.isFinite(Date.parse(dataset.expiresAt)) || Date.parse(dataset.expiresAt) <= now;
        const status = dataset.status === 'available' && expired ? 'needs_refresh' : dataset.status;
        const observed = dataset.observedAt ? new Date(dataset.observedAt) : null;
        const stamp = observed && Number.isFinite(observed.getTime()) ? observed.toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : null;
        return <div className="source-item" key={dataset.name}>
          <span>{researchNames[dataset.name]}</span>
          <p className={`source-status ${status}`}><span className="source-dot" />{status === 'available' ? 'Imported snapshot' : status === 'needs_refresh' ? 'Snapshot needs refresh' : 'Not synchronized'}</p>
          {dataset.rows > 0 && <p className="source-detail">{dataset.rows.toLocaleString('en-US')} rows · {dataset.season || 'All seasons'}{dataset.latestWeek ? ` · through week ${dataset.latestWeek}` : ''}</p>}
          {stamp && <p className="source-detail">Imported {stamp}</p>}
        </div>;
      })}</div>
    </>}
    <div className="provider-options"><span>Recommended data providers:</span>
      <a href="https://the-odds-api.com/liveapi/guides/v4/" target="_blank" rel="noopener noreferrer">The Odds API · prices</a>
      <a href="https://sportsdata.io/developers/coverages/nfl" target="_blank" rel="noopener noreferrer">SportsDataIO · stats & injuries</a>
      <a href="https://developer.sportradar.com/football/docs/nfl-ig-rosters" target="_blank" rel="noopener noreferrer">Sportradar · depth charts</a>
      <a href="https://nflreadpy.nflverse.com/" target="_blank" rel="noopener noreferrer">nflverse · historical data</a>
      <a href="https://www.weather.gov/documentation/services-web-api" target="_blank" rel="noopener noreferrer">NWS · free US weather API</a>
    </div>
  </section>;
}
