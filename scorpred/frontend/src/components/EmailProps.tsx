import { useEffect, useState } from 'react';
import { Mail } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';

interface Job { id?: string; state: string; message: string; pick_count?: number }
interface Session { user: { email: string } | null; csrf_token: string }

export default function EmailProps({ date, sport }: { date: string; sport: string }) {
  const session = useFetch<Session>('/api/session');
  const sports = sport === 'overall' ? 'soccer,nba,nfl' : sport;
  const setup = useFetch<{ ready: boolean; recipient: string; job?: Job | null }>(session.data?.user
    ? `/api/props/email?date=${encodeURIComponent(date)}&sports=${sports}` : null);
  const [requestedJob, setJob] = useState<Job | null>(null);
  const job = requestedJob ?? setup.data?.job;
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const pending = job?.state === 'collecting' || job?.state === 'submitting';
  const complete = job?.state === 'sent' || job?.state === 'delivery_unknown';

  useEffect(() => {
    if (!job?.id || !pending) return;
    let stopped = false;
    const poll = setInterval(async () => {
      try {
        const response = await fetch(`/api/props/email?id=${job.id}`, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Could not check email status.');
        if (!stopped) { setJob(payload.job); setError(''); }
      } catch (reason) {
        if (!stopped) setError(`${(reason as Error).message} Collection may still be running; check status again before resending.`);
      }
    }, 5000);
    return () => { stopped = true; clearInterval(poll); };
  }, [job?.id, pending]);

  async function send() {
    setSending(true);
    setError('');
    try {
      const response = await fetch('/api/props/email', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.data!.csrf_token },
        body: JSON.stringify({ date, sports: sport === 'overall' ? ['soccer', 'nba', 'nfl'] : [sport] }),
      });
      const payload = await response.json();
      if (payload.job) setJob(payload.job);
      else if (!response.ok) throw new Error(payload.error || 'Could not start email collection.');
    } catch (reason) { setError((reason as Error).message); }
    finally { setSending(false); }
  }

  if (!session.data?.user) return <a className="text-button" href={`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`}>Sign in to email props</a>;
  return <div className="card" aria-label="Email player props">
    <button type="button" className="text-button" onClick={send}
      disabled={sending || pending || complete || !setup.data?.ready}>
      <Mail size={16} /> {pending || sending ? 'Collecting props…' : complete ? 'Email requested' : 'Email these props'}
    </button>
    <p className="quiet-label">{date} · {sport === 'overall' ? 'All sports' : sport.toUpperCase()} · to {session.data.user.email}</p>
    {setup.data && !setup.data.ready && <p className="coverage-note">Email setup required: configure the sender’s SMTP app password locally, then restart ScorPred.</p>}
    {setup.error && <p role="alert">Email setup could not be checked. <button type="button" onClick={setup.retry}>Check again</button></p>}
    {job && <p role="status">{job.state === 'sent' ? `Sent ${job.pick_count ?? 0} props to your mail server.` : job.message}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
