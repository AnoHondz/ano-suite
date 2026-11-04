import { useEffect, useState } from 'react';
import type { Decision } from './DecisionCard';

// Fires a browser notification with today's top picks.
// Only works while the tab is open — no service worker / background delivery.
const STORAGE_KEY_SUBSCRIBED = 'sp_notify_subscribed';
const STORAGE_KEY_DATE = 'sp_notify_date';

export default function NotificationSubscriber({ topCards }: { topCards: Decision[] }) {
  const [permission, setPermission] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'default'
  );
  const subscribed = localStorage.getItem(STORAGE_KEY_SUBSCRIBED) === '1';

  // Fire notification once per day after 9am if subscribed
  useEffect(() => {
    if (!subscribed || permission !== 'granted' || topCards.length === 0) return;
    const today = new Date().toISOString().slice(0, 10);
    if (localStorage.getItem(STORAGE_KEY_DATE) === today) return;
    if (new Date().getHours() < 9) return;

    const body = topCards
      .slice(0, 3)
      .map(c => `${c.action}: ${c.side} (${c.confidence}%)`)
      .join(' · ');
    new Notification('ScorPred — Top Picks', { body, icon: '/favicon.ico' });
    localStorage.setItem(STORAGE_KEY_DATE, today);
  }, [subscribed, permission, topCards]);

  if (typeof Notification === 'undefined') return null;

  async function handleClick() {
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === 'granted') {
      localStorage.setItem(STORAGE_KEY_SUBSCRIBED, '1');
    }
  }

  if (permission === 'granted' && subscribed) {
    return (
      <span className="text-[11px] text-slate-500 flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-sky-400 shrink-0" />
        Daily alerts on
      </span>
    );
  }

  if (permission === 'denied') return null;

  return (
    <button
      type="button"
      onClick={handleClick}
      className="text-[11px] text-slate-500 hover:text-slate-300 transition flex items-center gap-1.5"
    >
      🔔 Daily alerts
    </button>
  );
}
