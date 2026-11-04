import { useState } from 'react';
import type { Decision } from './DecisionCard';

interface Props {
  cards: Decision[];
}

function kellyStake(modelProb: number, impliedProb: number, bankroll: number): number {
  if (impliedProb <= 0 || impliedProb >= 1) return 0;
  const b = (1 / impliedProb) - 1; // decimal odds minus 1
  const p = modelProb;
  const q = 1 - p;
  const f = (b * p - q) / b;
  return Math.max(0, bankroll * f * 0.25); // quarter-Kelly
}

export default function BankrollCalc({ cards }: Props) {
  const [open, setOpen] = useState(false);
  const [bankroll, setBankroll] = useState(500);

  const betCards = cards.filter(c => c.action === 'BET');
  if (betCards.length === 0) return null;

  return (
    <div className="card">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 w-full text-left"
      >
        <span className="text-base">💰</span>
        <span className="text-sm font-semibold text-slate-300">Bankroll Calculator</span>
        <span className="ml-auto text-slate-600 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-4 space-y-4">
          <div className="flex items-center gap-3">
            <label className="text-[11px] uppercase tracking-widest text-slate-500 shrink-0">
              Bankroll
            </label>
            <input
              type="range"
              min={50}
              max={5000}
              step={50}
              value={bankroll}
              onChange={e => setBankroll(Number(e.target.value))}
              className="flex-1 accent-sky-400"
            />
            <span className="text-sm font-semibold text-white w-16 text-right">
              ${bankroll.toLocaleString()}
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-[10px] uppercase tracking-widest text-slate-600 border-b border-white/[0.06]">
                  <th className="text-left pb-2 pr-3">Match</th>
                  <th className="text-left pb-2 pr-3">Side</th>
                  <th className="text-right pb-2 pr-3">Conf.</th>
                  <th className="text-right pb-2">Stake</th>
                </tr>
              </thead>
              <tbody>
                {betCards.map(c => {
                  const modelProb = c.confidence / 100;
                  const impliedProb = c.odds
                    ? c.odds.implied_prob / 100
                    : modelProb * 0.9; // proxy: slight book margin
                  const stake = kellyStake(modelProb, impliedProb, bankroll);
                  return (
                    <tr
                      key={`${c.action}-${c.side}-${c.matchup}`}
                      className="border-b border-white/[0.04] last:border-0"
                    >
                      <td className="py-2 pr-3 text-slate-500 truncate max-w-[120px]">
                        {c.matchup || `${c.side} vs ${c.opponent}`}
                      </td>
                      <td className="py-2 pr-3 text-white font-medium truncate max-w-[90px]">
                        {c.side}
                      </td>
                      <td className="py-2 pr-3 text-right text-slate-400">
                        {c.confidence}%
                      </td>
                      <td className="py-2 text-right font-semibold text-sky-300">
                        {stake > 0 ? `$${stake.toFixed(0)}` : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-slate-600 leading-relaxed">
            Quarter-Kelly sizing. Stakes are suggestions only — always bet within your limits.
            {!betCards.some(c => c.odds) && ' Full Kelly requires live odds; estimates shown.'}
          </p>
        </div>
      )}
    </div>
  );
}
