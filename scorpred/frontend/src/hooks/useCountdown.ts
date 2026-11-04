import { useEffect, useState } from 'react';

export type CountdownState = 'future' | 'soon' | 'imminent' | 'live';

export interface CountdownResult {
  label: string;
  state: CountdownState;
}

export function useCountdown(targetIso: string | undefined): CountdownResult {
  const compute = (): CountdownResult => {
    if (!targetIso) return { label: '', state: 'future' };
    const diff = new Date(targetIso).getTime() - Date.now();
    if (diff <= 0) return { label: 'Live', state: 'live' };
    if (diff < 3_600_000) {
      const m = Math.floor(diff / 60_000);
      return { label: `${m}m`, state: 'imminent' };
    }
    if (diff < 86_400_000) {
      const h = Math.floor(diff / 3_600_000);
      const m = Math.floor((diff % 3_600_000) / 60_000);
      return { label: `${h}h ${m}m`, state: 'soon' };
    }
    const label = new Date(targetIso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return { label, state: 'future' };
  };

  const [result, setResult] = useState<CountdownResult>(compute);

  useEffect(() => {
    if (!targetIso) return;
    setResult(compute());
    const id = setInterval(() => setResult(compute()), 30_000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetIso]);

  return result;
}
