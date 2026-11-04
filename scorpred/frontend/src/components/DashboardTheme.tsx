import { type ReactNode } from 'react';
import { ArrowUpRight, Bookmark, Home, Layers, RefreshCw, TrendingUp, ShieldCheck, Settings, UserRound } from 'lucide-react';
import scorpredMark from '../assets/scorpred-player-logo.png';
import { refreshData, useFetch, useFetchActivity } from '../hooks/useFetch';
import { useNow } from '../hooks/useNow';
import { calendarFormat } from '../navigation';

interface NavItem {
  label: string;
  icon: ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard',      icon: <Home className="h-4 w-4" /> },
  { label: 'NFL',            icon: <FootballIcon /> },
  { label: 'NBA',            icon: <BasketballIcon /> },
  { label: 'Soccer',         icon: <SoccerIcon /> },
  { label: 'Insights',       icon: <TrendingUp className="h-4 w-4" /> },
  { label: 'Parlay Mode',    icon: <Layers className="h-4 w-4" /> },
];

function FootballIcon() {
  return <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
    <path d="M3 21C0 9 9 0 21 3c3 12-6 21-18 18Z M7 17 17 7 M9 10l5 5 M11 8l5 5 M7 12l5 5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

function SoccerIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="m9 8 3-2 3 2 1 4-4 3-4-3 1-4Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

function BasketballIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M3.5 12h17M12 3c2.2 2.3 3.3 5.3 3.3 9S14.2 18.7 12 21M12 3C9.8 5.3 8.7 8.3 8.7 12S9.8 18.7 12 21"
        fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function LiveClock() {
  const time = useNow();
  return (
    <span className="live-chip">
      <span className="clock-label">Eastern time</span>
      {calendarFormat(time, { hour: '2-digit', minute: '2-digit' })} ET
    </span>
  );
}

export default function DashboardLayout({
  children,
  activeItem = 'Dashboard',
  onNavigate,
}: {
  children: ReactNode;
  activeItem?: string;
  onNavigate?: (item: string) => void;
}) {
  const account = useFetch<{ user: { email: string } | null }>('/api/session');
  const pending = useFetchActivity();
  return (
    <div className="app-layout">
      <a className="skip-link" href="#main-content">Skip to content</a>
      {/* ── Desktop sidebar ──────────────────────────── */}
      <aside className="sidebar hidden md:block">
        <div className="sidebar-inner">
          {/* Brand block */}
          <div className="sidebar-brand">
            <div className="sidebar-logo">
              <img src={scorpredMark} alt="ScorPred" className="h-full w-full object-cover" />
            </div>
            <div><p className="sidebar-title">scorpred<span>•</span></p>
            <p className="sidebar-subtitle">The matchday perspective</p></div>
          </div>

          {/* Navigation */}
          <p className="nav-section-label">WORKSPACE</p>
          <nav className="sidebar-nav" aria-label="Main navigation">
            {NAV_ITEMS.map((item, index) => {
              const active = item.label === activeItem;
              return (
                <button
                  key={item.label}
                  type="button"
                  onClick={() => onNavigate?.(item.label)}
                  aria-current={active ? 'page' : undefined}
                  className={`nav-item ${active ? 'nav-item-active' : ''}`}
                >
                  <span className="nav-icon">{item.icon}</span>
                  <span>{item.label}</span>
                  <span className="nav-number" aria-hidden="true">0{index + 1}</span>
                </button>
              );
            })}
          </nav>

          {/* Footer */}
          <div className="sidebar-footer">
            <a className="nav-item" href={account.data?.user ? '/my-bets' : '/login?next=/'}><UserRound size={16} />Account<ArrowUpRight size={13} /></a>
            <a className="nav-item" href="/settings"><Settings size={16} />Settings<ArrowUpRight size={13} /></a>
            <div className="sidebar-note"><ShieldCheck size={19} /><p>Evidence before instinct.<span>Check the data behind every decision.</span></p></div>
            <p className="sidebar-smallprint">Independent sports analysis <span>SC / 01</span></p>
          </div>
        </div>
      </aside>

      {/* ── Content area ────────────────────────────── */}
      <div className="content-area">
        <header className="topbar">
          <div>
            <p className="topbar-breadcrumb">Workspace <span>/</span></p>
            <p className="topbar-title">
              {activeItem}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button className="icon-button" onClick={refreshData} disabled={pending > 0} aria-label={pending ? 'Refreshing data' : 'Refresh data'} title="Refresh this view from the server"><RefreshCw size={16} className={pending ? 'refresh-spinning' : ''} /></button>
            <a className="icon-button" href="/settings" aria-label="Settings"><Settings size={16} /></a>
            <a className="account-link" href={account.data?.user ? '/my-bets' : '/login?next=/'}>
              <Bookmark size={14} />{account.data?.user ? 'Saved picks' : 'Sign in'}<ArrowUpRight size={13} />
            </a>
            <LiveClock />
          </div>
        </header>

        {/* Mobile bottom tab bar */}
        <nav className="mobile-tab-bar md:hidden" aria-label="Mobile navigation">
          {NAV_ITEMS.map((item) => {
            const active = item.label === activeItem;
            return (
              <button
                key={item.label}
                type="button"
                onClick={() => onNavigate?.(item.label)}
                aria-current={active ? 'page' : undefined}
                className={`mobile-tab ${active ? 'mobile-tab-active' : ''}`}
              >
                <span className="mobile-tab-icon">{item.icon}</span>
                <span className="mobile-tab-label">{item.label}</span>
              </button>
            );
          })}
        </nav>

        <main id="main-content" className="main-content">{children}
          <footer className="workspace-footer"><span>SCORPRED <span className="footer-dot">/</span> THE MATCHDAY PERSPECTIVE</span><span>Independent analysis. Outcomes are uncertain.</span></footer>
        </main>
      </div>
    </div>
  );
}

export function DashboardCard({
  title,
  children,
  className = '',
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && <p className="section-label mb-3">{title}</p>}
      {children}
    </section>
  );
}
