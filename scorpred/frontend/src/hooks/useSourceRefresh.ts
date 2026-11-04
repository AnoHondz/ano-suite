import { useEffect, useRef } from 'react';

/** Revalidate at the real source deadline without extending eligibility locally. */
export function useSourceRefresh(expires: number | null, retry: () => void, kickoff?: number) {
  const attempted = useRef(0);
  useEffect(() => {
    if (expires == null || !Number.isFinite(expires)) return;
    let timer: ReturnType<typeof setTimeout>;
    function check() {
      clearTimeout(timer);
      const now = Date.now();
      if (document.visibilityState !== 'visible' || (kickoff != null && now >= kickoff)) return;
      if (now >= expires! && now - attempted.current >= 30_000) {
        attempted.current = now;
        retry();
      }
      const next = Math.max(500, expires! - now + 100, attempted.current + 30_000 - now);
      timer = setTimeout(check, Math.min(next, 30_000));
    }
    check();
    document.addEventListener('visibilitychange', check);
    return () => { clearTimeout(timer); document.removeEventListener('visibilitychange', check); };
  }, [expires, kickoff, retry]);
}
