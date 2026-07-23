"""Zero-balance / soft-skip for captcha auto chain — avoid burning wall on empty wallet."""
from __future__ import annotations

import time

import pytest

from grokreg.backends.captcha import impl


@pytest.fixture(autouse=True)
def _clear_skip_state():
    impl.clear_captcha_provider_skips()
    yield
    impl.clear_captcha_provider_skips()


def test_zero_balance_marks_provider_skipped():
    assert not impl.is_captcha_provider_skipped("yescaptcha")
    impl.mark_captcha_provider_skip(
        "yescaptcha", RuntimeError("ERROR_ZERO_BALANCE: 帐户余额不足")
    )
    assert impl.is_captcha_provider_skipped("yescaptcha")


def test_non_balance_error_does_not_skip():
    impl.mark_captcha_provider_skip(
        "yescaptcha", RuntimeError("timeout waiting for task")
    )
    assert not impl.is_captcha_provider_skipped("yescaptcha")


def test_auto_skips_zero_balance_provider(monkeypatch):
    """Yes raises ZERO_BALANCE once → Cap used; second auto call never hits Yes."""
    calls: list[str] = []

    class FakeYes:
        def __init__(self, *a, **k):
            pass

        def solve_turnstile(self, *a, **k):
            calls.append("yes")
            raise RuntimeError("YesCaptcha createTask failed: ERROR_ZERO_BALANCE: 余额不足")

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def solve_turnstile(self, *a, **k):
            calls.append("cap")
            return "tok-cap"

    monkeypatch.setattr(impl, "YesCaptchaSolver", FakeYes)
    monkeypatch.setattr(impl, "CapSolverSolver", FakeCap)
    monkeypatch.setattr(impl, "resolve_yescaptcha_key", lambda k=None: "YES-KEY")
    monkeypatch.setattr(impl, "resolve_capsolver_key", lambda k=None: "CAP-KEY")
    monkeypatch.setattr(impl, "resolve_twocaptcha_key", lambda k=None: None)

    t1 = impl.solve_turnstile_auto(
        website_url="https://accounts.x.ai/sign-up",
        website_key="0xTEST",
        yescaptcha_key="YES-KEY",
        capsolver_key="CAP-KEY",
    )
    assert t1 == "tok-cap"
    assert calls == ["yes", "cap"]

    calls.clear()
    t2 = impl.solve_turnstile_auto(
        website_url="https://accounts.x.ai/sign-up",
        website_key="0xTEST",
        yescaptcha_key="YES-KEY",
        capsolver_key="CAP-KEY",
    )
    assert t2 == "tok-cap"
    assert calls == ["cap"], "zero-balance Yes must be soft-skipped on subsequent solves"


def test_preferred_provider_reorders_chain(monkeypatch):
    """When process prefers capsolver, Cap is tried before Yes."""
    calls: list[str] = []

    class FakeYes:
        def __init__(self, *a, **k):
            pass

        def solve_turnstile(self, *a, **k):
            calls.append("yes")
            return "tok-yes"

    class FakeCap:
        def __init__(self, *a, **k):
            pass

        def solve_turnstile(self, *a, **k):
            calls.append("cap")
            return "tok-cap"

    monkeypatch.setattr(impl, "YesCaptchaSolver", FakeYes)
    monkeypatch.setattr(impl, "CapSolverSolver", FakeCap)
    monkeypatch.setattr(impl, "resolve_yescaptcha_key", lambda k=None: "YES-KEY")
    monkeypatch.setattr(impl, "resolve_capsolver_key", lambda k=None: "CAP-KEY")
    monkeypatch.setattr(impl, "resolve_twocaptcha_key", lambda k=None: None)

    impl.set_preferred_captcha_provider("capsolver")
    tok = impl.solve_turnstile_auto(
        website_url="https://accounts.x.ai/sign-up",
        website_key="0xTEST",
        yescaptcha_key="YES-KEY",
        capsolver_key="CAP-KEY",
    )
    assert tok == "tok-cap"
    assert calls[0] == "cap"
