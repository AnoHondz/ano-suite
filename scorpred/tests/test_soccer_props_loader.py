from threading import Event
from services import soccer_props_loader as loader


def test_slow_requests_share_one_job_and_return_pending_then_result():
    gate = Event()
    calls = []
    def calculate():
        calls.append(1)
        gate.wait(5)
        return {"result": {"picks": []}}, 200
    key = ("slow-test",)
    try:
        assert loader.load(key, calculate)[1] == 202
        assert loader.load(key, calculate)[1] == 202
        gate.set()
        loader._jobs[key][0].result(timeout=5)
        assert loader.load(key, calculate) == ({"result": {"picks": []}}, 200)
        assert calls == [1]
    finally:
        gate.set()
        loader._jobs.pop(key, None)


def test_expired_jobs_are_recomputed_and_errors_can_retry():
    from concurrent.futures import Future
    stale = Future()
    stale.set_result(({"result": "old"}, 200))
    stale.ready_at = loader.monotonic() - 121
    key = ("expired-test",)
    loader._jobs[key] = (stale, loader.monotonic() - 121)
    gate = Event()
    def calculate():
        gate.wait(5)
        raise RuntimeError("provider failure")
    loader.load(key, calculate)
    future = loader._jobs[key][0]
    gate.set()
    try:
        future.result(timeout=5)
    except RuntimeError:
        pass
    assert loader.load(key, calculate)[1] == 503
    assert key not in loader._jobs
