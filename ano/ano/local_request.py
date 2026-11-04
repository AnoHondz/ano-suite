"""Interrupt a local inference wait without executing any late tool calls."""
import threading
import time

from .claude import ClaudeError


def request(api, arguments, budget, timeout=180, progress=None, clock=time.monotonic, use_gate=True):
    from .inference_gate import acquire, InferenceBusy
    try:lease=acquire() if use_gate else None
    except InferenceBusy as error:raise ClaudeError(str(error)) from None
    completed = threading.Event()
    result = []

    def run():
        try:
            result.append((True, api.chat.completions.create(**arguments)))
        except Exception as error:
            result.append((False, error))
        finally:
            if lease:lease.close()
            completed.set()

    try:threading.Thread(target=run, name='ano-local-request', daemon=True).start()
    except Exception:
        if lease:lease.close()
        raise
    started = clock()
    deadline = started + timeout
    next_progress = started + 5
    while not completed.wait(0.1):
        now = clock()
        if not budget.can_act() or now >= deadline:
            # Closing the client interrupts the socket; a late answer is discarded.
            threading.Thread(target=api.close, name='ano-local-close', daemon=True).start()
            raise ClaudeError(budget.failure_message(budget.reason or
                'The local model took too long. The request was stopped; no paid fallback was used.'))
        if progress is not None and now >= next_progress:
            try:
                progress(f'Waiting for the local model · {int(now - started)}s elapsed · Stop/Pause is available')
            except Exception:
                pass  # UI diagnostics must not change or replay the request.
            next_progress = now + 5
    if not budget.can_act():
        raise ClaudeError(budget.failure_message(budget.reason))
    success, value = result[0]
    if not success:
        raise value
    return value
