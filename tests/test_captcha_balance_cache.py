"""Captcha balance preflight: cache + single-flight + pinned backend hygiene."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from grokreg.backends.captcha import balance as bal_mod
from grokreg.backends.captcha import impl


@pytest.fixture(autouse=True)
def _clear_state():
    bal_mod.clear_balance_cache()
    impl.clear_captcha_provider_skips()
    yield
    bal_mod.clear_balance_cache()
    impl.clear_captcha_provider_skips()


def _patch_keys(monkeypatch, *, yes="YES-KEY", cap="CAP-KEY", tc=None):
    monkeypatch.setattr(bal_mod, "resolve_yescaptcha_key", lambda k=None: yes)
    monkeypatch.setattr(bal_mod, "resolve_capsolver_key", lambda k=None: cap)
    monkeypatch.setattr(bal_mod, "resolve_twocaptcha_key", lambda k=None: tc)
    monkeypatch.setattr(bal_mod, "resolve_yescaptcha_endpoint", lambda *a, **k: "https://api.yescaptcha.com")
    monkeypatch.setattr(bal_mod, "resolve_capsolver_endpoint", lambda *a, **k: "https://api.capsolver.com")


def test_pinned_capsolver_does_not_call_yes_get_balance(monkeypatch):
    """backend=capsolver must not hit Yes getBalance (key may still be set)."""
    yes_calls = []
    cap_calls = []

    class FakeYes:
        def __init__(self, *a, **k):
            pass

        def get_balance(self):
            yes_calls.append(1)
            return 10.0

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def get_balance(self):
            cap_calls.append(1)
            return 5.5

    _patch_keys(monkeypatch)
    monkeypatch.setattr(bal_mod, "YesCaptchaSolver", FakeYes)
    monkeypatch.setattr(bal_mod, "CapSolverSolver", FakeCap)

    info = bal_mod.check_captcha_balances(backend="capsolver", cfg={})
    assert info["ok"] is True
    assert info["primary"] == "capsolver"
    assert cap_calls == [1]
    assert yes_calls == [], "pinned capsolver must not probe Yes"
    assert info["yes"].get("probed") is False
    assert info["capsolver"].get("probed") is True


def test_balance_cache_second_call_skips_network(monkeypatch):
    """Within TTL, second check_captcha_balances reuses cache (1 Cap get_balance)."""
    cap_calls = []

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def get_balance(self):
            cap_calls.append(time.monotonic())
            return 5.5

    _patch_keys(monkeypatch, yes=None)
    monkeypatch.setattr(bal_mod, "CapSolverSolver", FakeCap)

    a = bal_mod.check_captcha_balances(backend="capsolver", cfg={})
    b = bal_mod.check_captcha_balances(backend="capsolver", cfg={})
    assert a["ok"] and b["ok"]
    assert a["capsolver"]["balance"] == b["capsolver"]["balance"] == 5.5
    assert len(cap_calls) == 1, f"expected 1 getBalance, got {len(cap_calls)}"
    assert b.get("cached") is True or b.get("from_cache") is True


def test_balance_single_flight_eight_threads(monkeypatch):
    """j=8 style: concurrent preflight → only one Cap get_balance."""
    cap_calls = []
    barrier = threading.Barrier(8)

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def get_balance(self):
            cap_calls.append(1)
            time.sleep(0.05)  # hold so others pile up
            return 5.5

    _patch_keys(monkeypatch, yes=None)
    monkeypatch.setattr(bal_mod, "CapSolverSolver", FakeCap)

    def worker():
        barrier.wait()
        return bal_mod.check_captcha_balances(backend="capsolver", cfg={})

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(worker) for _ in range(8)]
        results = [f.result(timeout=5) for f in as_completed(futs)]

    assert all(r["ok"] for r in results)
    assert len(cap_calls) == 1, f"single-flight failed: {len(cap_calls)} getBalance calls"


def test_rate_limit_retries_then_ok(monkeypatch):
    """Transient ERROR_RATE_LIMIT should retry, not permanent fail on first hit."""
    attempts = []

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def get_balance(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError(
                    "CapSolver getBalance failed: ERROR_RATE_LIMIT: request rate limit"
                )
            return 5.5

    _patch_keys(monkeypatch, yes=None)
    monkeypatch.setattr(bal_mod, "CapSolverSolver", FakeCap)
    monkeypatch.setattr(bal_mod, "time", time)  # real sleep ok short

    info = bal_mod.check_captcha_balances(backend="capsolver", cfg={})
    assert info["ok"] is True
    assert info["capsolver"]["balance"] == 5.5
    assert len(attempts) == 3


def test_pinned_register_preflight_does_not_soft_skip_yes(monkeypatch):
    """register_one balance path: backend=capsolver must NOT mark Yes soft-skipped."""
    from grokreg.pipeline import register as reg
    from grokreg.pipeline.register import RegisterOptions

    monkeypatch.setattr(
        reg,
        "check_captcha_balances",
        lambda **kw: {
            "ok": True,
            "primary": "capsolver",
            "yes": {
                "configured": True,
                "probed": False,
                "ok": False,
                "balance": None,
                "error": None,
            },
            "capsolver": {
                "configured": True,
                "probed": True,
                "ok": True,
                "balance": 5.5,
                "error": None,
            },
            "twocaptcha": {"configured": False, "probed": False, "ok": False},
            "error": None,
        },
    )
    monkeypatch.setattr(reg, "format_balance_report", lambda info: "ok")
    marks: list[tuple] = []

    def fake_mark(name, reason, force=False):
        marks.append((name, str(reason), force))
        return True

    monkeypatch.setattr(reg, "mark_captcha_provider_skip", fake_mark)
    if hasattr(reg, "set_preferred_captcha_provider"):
        monkeypatch.setattr(reg, "set_preferred_captcha_provider", lambda n: None)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def scrape_signup(self, *a, **k):
            raise RuntimeError("stop-after-balance")

        # any later methods should not matter if we fail early enough
        def __getattr__(self, name):
            def _(*a, **k):
                raise RuntimeError(f"unexpected {name}")

            return _

    monkeypatch.setattr(reg, "GrokAuthClient", FakeClient)

    class FakeResolved:
        url = "http://127.0.0.1:9"
        sid = "test"
        region = "US"
        chain = False

        def label(self) -> str:
            return "fake-proxy"

        def close(self) -> None:
            return None

    monkeypatch.setattr(reg, "resolve_proxy", lambda *a, **k: FakeResolved())

    opts = RegisterOptions(
        captcha_backend="capsolver",
        captcha_balance_check=True,
        require_captcha_config=False,
        scrape_cache=False,
        mail_backend="cloudmail",
    )
    # signature: register_one(cfg, raw, proxy_override, opts)
    result = reg.register_one(
        {"yescaptcha": {}, "capsolver": {}, "twocaptcha": {}},
        "a@b.com----cloudmail",
        None,
        opts,
    )
    yes_marks = [m for m in marks if m[0] == "yescaptcha"]
    assert yes_marks == [], f"pinned capsolver must not soft-skip Yes: {marks} result={result}"
