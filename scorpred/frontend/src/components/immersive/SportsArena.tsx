import { Component, lazy, Suspense, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { ballPose, PLAYERS, playerPose, PLAY_LABELS, TEAM_COLORS } from './arenaPlay';

const ArenaScene = lazy(() => import('./ArenaScene'));
export type ArenaSport = 'nfl' | 'nba' | 'soccer';

function useMedia(query: string) {
  return useSyncExternalStore(callback => {
    const media = matchMedia(query); media.addEventListener('change', callback);
    return () => media.removeEventListener('change', callback);
  }, () => matchMedia(query).matches);
}

function FlatArena({ sport }: { sport: ArenaSport }) {
  const ball = ballPose(sport, 0);
  return <div className={`arena-flat arena-flat-${sport}`} data-renderer="lite" aria-hidden="true"><div className="flat-stadium"><div className="flat-pitch">
    <svg viewBox="0 0 600 330" fill="none"><rect x="20" y={sport === 'nfl' ? 48 : 20} width="560" height={sport === 'nfl' ? 234 : 290} rx="2" />
      {sport === 'nfl' ? <>{Array.from({ length: 11 }, (_, index) => <path key={index} d={`M${66.65 + index * 46.67} 48v234`} />)}<path d="M67 133h466M67 197h466" strokeDasharray="2 12" /></>
        : <><path d="M300 20v290" /><circle cx="300" cy="165" r="44" />
          {sport === 'soccer' ? <path d="M20 80h90v170H20m560-170h-90v170h90M20 120h32v90H20m560-90h-32v90h32" />
            : <><path d="M20 100h110v130H20m560-130H470v130h110M20 50h75a155 155 0 0 1 0 230H20m560-230h-75a155 155 0 0 0 0 230h75" /><circle cx="130" cy="165" r="40" /><circle cx="470" cy="165" r="40" /></>}
        </>}
      <g className="arena-flat-players">{PLAYERS[sport].map(actor => {
        const pose = playerPose(actor, 0);
        return <circle key={actor.id} data-team={actor.team} cx={300 + pose.x * 56} cy={165 + pose.z * 52.7}
          r={sport === 'nba' ? 7 : 5} fill={TEAM_COLORS[actor.team]} stroke="#04131c" strokeWidth="2" />;
      })}</g>
      <circle cx={300 + ball.x * 56} cy={165 + ball.z * 52.7} r="3.5" fill={sport === 'nba' ? '#f39943' : '#ffffff'} stroke="#263c46" strokeWidth="1" />
    </svg></div></div></div>;
}

class ArenaBoundary extends Component<{ children: ReactNode; fallback: ReactNode; onFailure: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onFailure(); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export default function SportsArena({ sport, compact = false }: { sport: ArenaSport; compact?: boolean }) {
  const element = useRef<HTMLDivElement>(null);
  const reduced = useMedia('(prefers-reduced-motion: reduce)');
  const mobile = useMedia('(max-width: 600px)');
  const [visible, setVisible] = useState(false);
  const [foreground, setForeground] = useState(document.visibilityState === 'visible');
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [side, setSide] = useState<'home' | 'away'>('home');
  const [playing, setPlaying] = useState(true);
  const [replay, setReplay] = useState(0);
  const [lite, setLite] = useState(() => {
    try { return localStorage.getItem('scorpred-graphics') === 'lite' || new URLSearchParams(location.search).get('graphics') === 'lite'; }
    catch { return true; }
  });
  useEffect(() => {
    const observer = new IntersectionObserver(entries => setVisible(entries[0].isIntersecting), { rootMargin: '80px' });
    if (element.current) observer.observe(element.current);
    const visibility = () => setForeground(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', visibility);
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  useEffect(() => {
    if (!visible || lite || reduced || failed) return;
    const timer = setTimeout(() => setReady(true), 900);
    return () => clearTimeout(timer);
  }, [visible, lite, reduced, failed]);
  const render3D = ready && !lite && !reduced && !failed;
  const active = render3D && visible && foreground;
  const fallback = <FlatArena sport={sport} />;
  return <div className="arena-experience"><div ref={element} className={`sports-arena arena-${sport}${compact ? ' arena-compact' : ''}`} data-active={active} data-side={side}
    data-playing={active && playing} data-players={PLAYERS[sport].length} aria-label={`Interactive ${sport === 'nba' ? 'basketball court' : sport === 'nfl' ? 'football field' : 'soccer pitch'}`}>
    <div className="arena-grid" aria-hidden="true" />
    {render3D ? <ArenaBoundary onFailure={() => setFailed(true)} fallback={fallback}><Suspense fallback={fallback}>
      <ArenaScene sport={sport} active={active} side={side} playing={playing} replay={replay} dpr={mobile ? 1 : 1.5} />
    </Suspense></ArenaBoundary> : fallback}
    <div className="arena-toolbar"><span className="arena-label">{sport === 'nba' ? 'COURTSIDE' : sport === 'nfl' ? 'THE GRIDIRON' : 'THE PITCH'}<span>ILLUSTRATIVE PLAY</span></span>
      <button className="arena-mode" disabled={reduced || failed} onClick={() => {
        setLite(value => { try { localStorage.setItem('scorpred-graphics', value ? '3d' : 'lite'); } catch { /* Preference is optional. */ } return !value; });
      }}>{reduced ? 'Reduced motion' : failed ? 'Lite view · WebGL unavailable' : lite ? 'Enable 3D' : 'Use lite view'}</button>
    </div>
    <div className="arena-bottom"><span>{render3D ? 'Move pointer to explore' : 'Static formation'}<small>Home attack → away goal</small></span>
      <div className="arena-view-controls" aria-label="Arena perspective"><button aria-pressed={side === 'home'} onClick={() => setSide('home')}>Home view</button><button aria-pressed={side === 'away'} onClick={() => setSide('away')}>Away view</button></div>
    </div>
  </div><div className="arena-playbar">
    <div className="arena-play-description"><strong>{PLAY_LABELS[sport]}</strong><span>Illustration · not confirmed lineups or live tracking</span></div>
    <div className="arena-team-key" aria-label="Illustrative teams"><span><i style={{ background: TEAM_COLORS.home }} />Home</span><span><i style={{ background: TEAM_COLORS.away }} />Away</span></div>
    {render3D && <div className="arena-play-actions"><button type="button" onClick={() => setPlaying(value => !value)}>{playing ? 'Pause play' : 'Resume play'}</button>
      <button type="button" onClick={() => { setReplay(value => value + 1); setPlaying(true); }}>Replay</button></div>}
  </div></div>;
}
