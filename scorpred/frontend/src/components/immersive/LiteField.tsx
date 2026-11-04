import type { ArenaSport } from './SportsArena';

/** Decorative field markings; no tracking, lineup or prediction data. */
export default function LiteField({ sport }: { sport: ArenaSport }) {
  return <div className={`prediction-surface surface-${sport}`} aria-hidden="true" data-renderer="lite">
    <svg viewBox="0 0 1000 500" fill="none" preserveAspectRatio="xMidYMid slice">
      <rect className="field-turf" x="0" y="0" width="1000" height="500" rx="18" />
      {sport === 'nba' ? <>
        {Array.from({ length: 16 }, (_, i) => <path className="court-plank" key={i} d={`M0 ${i * 32}H1000`} />)}
        <rect x="28" y="25" width="944" height="450" rx="3" />
        <path d="M500 25V475" /><circle cx="500" cy="250" r="68" />
        <path d="M28 158H186V342H28M972 158H814V342H972M28 60H95A225 225 0 0 1 95 440H28M972 60H905A225 225 0 0 0 905 440H972" />
        <circle cx="186" cy="250" r="58" /><circle cx="814" cy="250" r="58" />
      </> : sport === 'nfl' ? <>
        <rect x="28" y="40" width="944" height="420" rx="3" />
        <path d="M107 40V460M893 40V460" />
        {Array.from({ length: 9 }, (_, i) => <path key={i} d={`M${185.6 + i * 78.6} 40V460`} />)}
        <path d="M107 187H893M107 313H893" strokeDasharray="3 15" />
      </> : <>
        <rect x="28" y="25" width="944" height="450" rx="3" /><path d="M500 25V475" /><circle cx="500" cy="250" r="68" />
        <path d="M28 110H192V390H28M972 110H808V390H972M28 185H86V315H28M972 185H914V315H972" />
        <circle cx="150" cy="250" r="3" /><circle cx="850" cy="250" r="3" />
      </>}
    </svg>
  </div>;
}
