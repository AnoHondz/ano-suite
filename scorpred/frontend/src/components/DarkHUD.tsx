import { motion } from 'framer-motion';
import HudCorners from './HudCorners';
import scorpredMark from '../assets/scorpred-player-logo.png';

const TICKER_ITEMS = [
  'BET - Arsenal - 68% - Strong Data',
  'CONSIDER - Celtics - 59% - Partial Data',
  'CONSIDER - Inter Milan - 55% - Limited Data',
  'Opportunity radar refreshed for the current slate',
];

export default function DarkHUD() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[#060b13] text-white">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            'linear-gradient(#14f195 1px, transparent 1px), linear-gradient(90deg, #14f195 1px, transparent 1px)',
          backgroundSize: '48px 48px',
        }}
      />

      <div className="relative z-10 overflow-hidden border-b border-emerald-400/10 bg-black/25 py-2">
        <motion.div
          className="flex gap-16 whitespace-nowrap text-xs uppercase tracking-[0.18em] text-slate-500"
          animate={{ x: ['0%', '-50%'] }}
          transition={{ repeat: Infinity, duration: 30, ease: 'linear' }}
        >
          {[...TICKER_ITEMS, ...TICKER_ITEMS].map((item, index) => (
            <span key={`${item}-${index}`} className="shrink-0">
              {item}
            </span>
          ))}
        </motion.div>
      </div>

      <nav className="relative z-10 flex items-center justify-between border-b border-emerald-400/10 px-8 py-5">
        <div className="flex items-center gap-3">
          <img src={scorpredMark} alt="ScorPred celebration mark" className="h-9 w-9 rounded-xl object-cover shadow-[0_0_24px_rgba(20,184,166,0.18)]" />
          <div>
            <p className="font-oswald text-xl uppercase tracking-normal">ScorPred</p>
            <p className="text-xs text-slate-500">Decision Intelligence</p>
          </div>
        </div>
        <div className="hidden gap-6 text-xs uppercase tracking-[0.16em] text-slate-500 md:flex">
          <span>Home</span>
          <span>Soccer</span>
          <span>NBA</span>
          <span>Match Analysis</span>
          <span>Insights</span>
        </div>
      </nav>

      <section className="relative z-10 mx-auto grid max-w-7xl items-center gap-12 px-8 py-20 lg:grid-cols-2">
        <div className="space-y-7">
          <p className="text-xs uppercase tracking-[0.26em] text-emerald-300">Decision-first sports intelligence</p>
          <h1 className="font-oswald text-5xl uppercase leading-tight tracking-[0.08em] lg:text-6xl">
            What should I do,
            <span className="block text-emerald-300">and can I trust it?</span>
          </h1>
          <p className="max-w-xl text-base leading-7 text-slate-400">
            ScorPred turns noisy matchday data into ranked sides, confidence context, richer reasoning, and live slate insights.
          </p>
          <div className="flex flex-wrap gap-4">
            <span className="rounded-full border border-emerald-400/25 bg-emerald-400/10 px-5 py-2 text-sm font-semibold text-emerald-200">
              BET
            </span>
            <span className="rounded-full border border-amber-400/25 bg-amber-400/10 px-5 py-2 text-sm font-semibold text-amber-200">
              CONSIDER
            </span>
            <span className="rounded-full border border-slate-500/30 bg-slate-500/10 px-5 py-2 text-sm font-semibold text-slate-300">
              SKIP
            </span>
          </div>
        </div>

        <motion.div
          className="relative hidden lg:block"
          animate={{ y: [0, -12, 0] }}
          transition={{ repeat: Infinity, duration: 4, ease: 'easeInOut' }}
        >
          <div className="relative rounded-2xl border border-emerald-400/20 bg-black/35 p-6 backdrop-blur-sm">
            <HudCorners />
            <p className="text-xs uppercase tracking-[0.18em] text-emerald-300">Top opportunity</p>
            <h2 className="mt-4 font-oswald text-4xl uppercase tracking-normal text-white">BET - Arsenal</h2>
            <div className="mt-6">
              <div className="mb-2 flex justify-between text-sm text-slate-400">
                <span>Confidence</span>
                <strong className="text-white">68%</strong>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/10">
                <motion.div
                  className="h-full rounded-full bg-emerald-300"
                  initial={{ width: 0 }}
                  animate={{ width: '68%' }}
                  transition={{ duration: 1.2, ease: 'easeOut' }}
                />
              </div>
            </div>
            <p className="mt-5 text-slate-300">Strong attacking form plus home advantage.</p>
            <span className="mt-5 inline-flex rounded-full border border-emerald-400/25 bg-emerald-400/10 px-3 py-1 text-xs text-emerald-200">
              Strong Data
            </span>
          </div>
        </motion.div>
      </section>
    </div>
  );
}
