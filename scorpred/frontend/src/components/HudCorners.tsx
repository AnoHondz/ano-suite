/**
 * HUD-style corner brackets — decorative overlay for DarkHUD cards.
 * Renders four thin L-shaped lines in the corners of the parent element.
 */
export default function HudCorners({ color = '#00ff87', size = 14, thickness = 1.5 }: {
  color?: string;
  size?: number;
  thickness?: number;
}) {
  const style = (pos: Record<string, number | string>): React.CSSProperties => ({
    position: 'absolute',
    width: size,
    height: size,
    pointerEvents: 'none',
    ...pos,
  });

  const border = `${thickness}px solid ${color}`;

  return (
    <>
      <span style={{ ...style({ top: 0, left: 0 }), borderTop: border, borderLeft: border }} />
      <span style={{ ...style({ top: 0, right: 0 }), borderTop: border, borderRight: border }} />
      <span style={{ ...style({ bottom: 0, left: 0 }), borderBottom: border, borderLeft: border }} />
      <span style={{ ...style({ bottom: 0, right: 0 }), borderBottom: border, borderRight: border }} />
    </>
  );
}
