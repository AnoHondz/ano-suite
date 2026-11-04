export default function FieldGraphic() {
  return <div className="field-graphic" aria-hidden="true">
    <div className="field-label"><span className="field-dot" />THE MATCHDAY PERSPECTIVE<span>SC / 03</span></div>
    <svg viewBox="0 0 400 230" fill="none">
      <rect x="22" y="25" width="356" height="180" rx="4" />
      <path d="M200 25v180M22 68h52v94H22m356-94h-52v94h52M22 91h22v48H22m356-48h-22v48h22" />
      <circle cx="200" cy="115" r="38" />
      <path d="m115 156 47-66 69 48 63-66" className="field-route" strokeDasharray="5 7" />
      <g className="field-player"><circle cx="115" cy="156" r="8" /><circle cx="162" cy="90" r="8" /><circle cx="231" cy="138" r="8" /></g>
      <circle cx="294" cy="72" r="17" className="field-target" /><path d="m287 72 5 5 9-10" className="field-check" />
    </svg>
    <div className="field-caption"><span>Different sports.<br /><strong>A clearer perspective.</strong></span><span className="field-sports">SOCCER · NBA · NFL</span></div>
  </div>;
}
