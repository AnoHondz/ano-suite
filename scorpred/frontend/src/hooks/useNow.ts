import { useSyncExternalStore } from 'react';

let now = Date.now();
let timer: ReturnType<typeof setInterval> | undefined;
const listeners = new Set<() => void>();
const tick = () => {
  now = Date.now();
  listeners.forEach(listener => listener());
};
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  if (!timer) {
    now = Date.now();
    timer = setInterval(tick, 1000);
    window.addEventListener('focus', tick);
    document.addEventListener('visibilitychange', tick);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer);
      timer = undefined;
      window.removeEventListener('focus', tick);
      document.removeEventListener('visibilitychange', tick);
    }
  };
};

/** One shared clock withdraws expired predictions on open pages and tab return. */
export function useNow() {
  return useSyncExternalStore(subscribe, () => now);
}
