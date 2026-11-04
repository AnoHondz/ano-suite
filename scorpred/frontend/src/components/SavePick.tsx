import { useState } from 'react';
import { Bookmark, BookmarkCheck } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import type { Decision } from './DecisionCard';

interface Session { user: { email: string } | null; csrf_token: string }

/** Save this pick to the signed-in account (POST /api/bets); signed-out viewers get a sign-in link. */
export default function SavePick({ decision }: { decision: Decision }) {
  const session = useFetch<Session>('/api/session');
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [message, setMessage] = useState('');
  if (decision.side === 'Unavailable' || !decision.matchId) return null;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  if (!session.data?.user) {
    return <a className="text-button save-pick" href={`/login?next=${next}`}><Bookmark size={15} />Sign in to save this pick</a>;
  }

  async function save() {
    setState('saving');
    try {
      const response = await fetch('/api/bets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.data!.csrf_token },
        body: JSON.stringify({ match_id: decision.matchId, matchup: decision.matchup, recommended_side: decision.side,
          action: decision.action, confidence: decision.winProbability ?? decision.confidence, sport: decision.sport }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || payload.message || `Server returned ${response.status}`);
      setState('saved');
    } catch (error) {
      setMessage((error as Error).message);
      setState('error');
    }
  }

  return <div className="save-pick-row">
    <button type="button" className="text-button save-pick" onClick={save} disabled={state === 'saving' || state === 'saved'}>
      {state === 'saved' ? <BookmarkCheck size={15} /> : <Bookmark size={15} />}
      {state === 'saved' ? 'Saved' : state === 'saving' ? 'Saving…' : 'Save pick'}
    </button>
    {state === 'saved' && <a className="quiet-label" href="/my-bets">View saved picks</a>}
    {state === 'error' && <span className="coverage-note" role="alert">{message}</span>}
  </div>;
}
