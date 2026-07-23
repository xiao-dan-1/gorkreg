"""ZERO_BALANCE: no prefetch double-pay; soft-skip; batch fuse."""
from __future__ import annotations

from concurrent.futures import Future

import pytest

from grokreg.backends.captcha.impl import (
    clear_captcha_provider_skips,
    is_captcha_provider_skipped,
    mark_captcha_provider_skip,
)
from grokreg.pipeline import register as reg


def test_mark_zero_balance_skips_capsolver():
    clear_captcha_provider_skips()
    assert not is_captcha_provider_skipped("capsolver")
    ok = mark_captcha_provider_skip(
        "capsolver",
        "CapSolver createTask failed: ERROR_ZERO_BALANCE: insufficient",
    )
    assert ok
    assert is_captcha_provider_skipped("capsolver")
    clear_captcha_provider_skips()


def test_join_prefetch_zero_balance_does_not_sync():
    clear_captcha_provider_skips()
    calls = {"sync": 0}

    def sync_solve():
        calls["sync"] += 1
        return "should-not-run"

    fut: Future = Future()
    fut.set_exception(
        RuntimeError(
            "CapSolver createTask failed: ERROR_ZERO_BALANCE: Your balance is insufficient"
        )
    )
    import time

    t0 = time.time()
    with pytest.raises(RuntimeError, match="ZERO_BALANCE"):
        reg._join_turnstile_prefetch(
            pref_future=fut, pref_t0=t0, sync_solve=sync_solve
        )
    assert calls["sync"] == 0
    assert is_captcha_provider_skipped("capsolver")
    clear_captcha_provider_skips()


def test_batch_fuse_trips_on_first_zero_balance():
    import importlib.util
    from pathlib import Path

    path = Path("scripts/prod_cloudmail_batch.py")
    spec = importlib.util.spec_from_file_location("prod_cloudmail_batch", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    assert mod._ZERO_BALANCE_FUSE == 1
    with mod._ZERO_BALANCE_LOCK:
        mod._ZERO_BALANCE_STREAK = 0
        mod._ZERO_BALANCE_TRIPPED = False
    assert not mod._zero_balance_tripped()
    mod._note_zero_balance_result(
        {"ok": False, "error": "exception:ERROR_ZERO_BALANCE: x"}
    )
    assert mod._zero_balance_tripped()
    with mod._ZERO_BALANCE_LOCK:
        mod._ZERO_BALANCE_STREAK = 0
        mod._ZERO_BALANCE_TRIPPED = False

def test_batch_source_has_sliding_window():
    from pathlib import Path
    src = Path("scripts/prod_cloudmail_batch.py").read_text(encoding="utf-8")
    assert "rows_by_idx" in src
    assert "fused_stop" in src
    assert "Sliding window" in src or "never queue all n" in src.lower()
