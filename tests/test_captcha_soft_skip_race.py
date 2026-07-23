"""Concurrent captcha soft-skip: only one doomed createTask when wallet empty."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from grokreg.backends.captcha import impl


@pytest.fixture(autouse=True)
def _clear():
    impl.clear_captcha_provider_skips()
    yield
    impl.clear_captcha_provider_skips()


def test_concurrent_zero_balance_only_one_yes_create(monkeypatch):
    """j>1: first Yes ZERO_BALANCE soft-skips; siblings must not call Yes again."""
    barrier = threading.Barrier(4)
    calls = []
    lock = threading.Lock()

    class FakeYes:
        def __init__(self, *a, **k):
            pass

        def solve_turnstile(self, *a, **k):
            barrier.wait(timeout=2)
            with lock:
                calls.append("yes")
            raise RuntimeError("ERROR_ZERO_BALANCE: 余额不足")

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def solve_turnstile(self, *a, **k):
            with lock:
                calls.append("cap")
            return "tok-cap"

    monkeypatch.setattr(impl, "YesCaptchaSolver", FakeYes)
    monkeypatch.setattr(impl, "CapSolverSolver", FakeCap)
    monkeypatch.setattr(impl, "resolve_yescaptcha_key", lambda k=None: "YES")
    monkeypatch.setattr(impl, "resolve_capsolver_key", lambda k=None: "CAP")
    monkeypatch.setattr(impl, "resolve_twocaptcha_key", lambda k=None: None)

    def one():
        return impl.solve_turnstile_auto(
            website_url="https://accounts.x.ai/sign-up",
            website_key="0xTEST",
            yescaptcha_key="YES",
            capsolver_key="CAP",
        )

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(one) for _ in range(4)]
        toks = [f.result(timeout=10) for f in as_completed(futs)]

    assert all(t == "tok-cap" for t in toks)
    # At most one Yes createTask in the race window after soft-skip coordination
    assert calls.count("yes") <= 1, calls
    assert calls.count("cap") == 4
