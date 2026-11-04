import { useSyncExternalStore } from 'react';

const subscribe = (callback: () => void) => {
  window.addEventListener('popstate', callback);
  return () => window.removeEventListener('popstate', callback);
};

export function useLocation() {
  return useSyncExternalStore(subscribe, () => window.location.pathname + window.location.search);
}

export function navigate(path: string) {
  if (path === window.location.pathname + window.location.search) return;
  window.history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
  window.scrollTo(0, 0);
}

export function localToday() {
  return calendarDate();
}

export const CALENDAR_TIMEZONE = 'America/New_York';

export function calendarDate(value: string | number | Date = Date.now()) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: CALENDAR_TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date);
  const part = (name: string) => parts.find(item => item.type === name)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export function calendarFormat(value: string | number | Date, options: Intl.DateTimeFormatOptions) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-US', { ...options, timeZone: CALENDAR_TIMEZONE }).format(date) : '';
}

export function dateTabLabel(iso: string, now = Date.now()) {
  const today = calendarDate(now);
  // Calendar-date arithmetic is independent of the browser timezone and DST.
  const tomorrow = new Date(`${today}T12:00:00Z`);
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  if (iso === today) return 'Today';
  if (iso === tomorrow.toISOString().slice(0, 10)) return 'Tomorrow';
  return calendarFormat(`${iso}T12:00:00Z`, { weekday: 'short', month: 'short', day: 'numeric' }) || iso;
}
