/** Server ladder: over[i] = P(stat >= start + i + 1), the over price of line start + i + 0.5. */
export interface Ladder { start: number; over: number[] }

function atLeastNext(ladder: Ladder, k: number) {
  const index = k - ladder.start;
  if (index < 0) return 1;
  return index < ladder.over.length ? ladder.over[index] : 0;
}

export function priceLine(ladder: Ladder, line: number) {
  const floor = Math.floor(line);
  const over = atLeastNext(ladder, floor);
  // Whole-number lines push when the stat lands exactly on the line.
  const push = Number.isInteger(line) && line > 0 ? atLeastNext(ladder, floor - 1) - over : 0;
  return { over, push, under: Math.max(0, 1 - over - push) };
}
