"""Prefetch join: reclaim / wait paid Cap task instead of double createTask."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import time

from grokreg.pipeline import register as reg


def test_join_budget_takes_done_future_not_sync():
    fut: Future = Future()
    fut.set_result(("token-from-prefetch", 1.2))
    calls = []

    def sync():
        calls.append(1)
        return "sync-token"

    t0 = time.time() - 60.0
    tok, meta = reg._join_turnstile_prefetch(
        pref_future=fut, pref_t0=t0, sync_solve=sync, max_join_s=45.0
    )
    assert tok == "token-from-prefetch"
    assert calls == []
    assert meta.get("prefetch") is True


def test_join_timeout_waits_paid_future_not_immediate_sync():
    """Future finishes after join budget — must take it, not sync."""
    ex = ThreadPoolExecutor(max_workers=1)
    calls = []

    def slow():
        time.sleep(0.6)
        return ("paid-token", 0.6)

    fut = ex.submit(slow)

    def sync():
        calls.append(1)
        return "sync-bad"

    t0 = time.time()
    tok, meta = reg._join_turnstile_prefetch(
        pref_future=fut, pref_t0=t0, sync_solve=sync, max_join_s=0.15
    )
    ex.shutdown(wait=False, cancel_futures=True)
    assert tok == "paid-token"
    assert calls == []
    assert meta.get("prefetch") is True
