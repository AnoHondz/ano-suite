export default function RequestError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="card border-rose-400/30 space-y-3">
      <p className="text-sm text-rose-200">{message}</p>
      <button type="button" className="btn-analyze" onClick={onRetry}>Try again</button>
    </div>
  );
}
