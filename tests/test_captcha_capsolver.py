"""CapSolver captcha backend plugin."""
from __future__ import annotations

import inspect

from grokreg.backends.captcha import factory as fac
from grokreg.backends.captcha import impl
from grokreg.backends.captcha.capsolver import CapSolverBackend


def test_capsolver_resolver_and_exports():
    assert impl.resolve_capsolver_key("abc") == "abc"
    assert "capsolver.com" in impl.resolve_capsolver_endpoint()
    assert hasattr(impl, "CapSolverSolver")


def test_factory_builds_capsolver():
    b = fac.get_captcha_backend("capsolver", capsolver_key="CAP-TEST")
    assert isinstance(b, CapSolverBackend)
    assert hasattr(b, "solve_turnstile")
    assert hasattr(b, "get_balance")


def test_factory_auto_accepts_capsolver_kwargs():
    src = inspect.getsource(fac.get_captcha_backend)
    assert "capsolver" in src
    assert "CapSolverBackend" in src


def test_auto_solve_includes_capsolver():
    src = inspect.getsource(impl.solve_turnstile_auto)
    assert "CapSolverSolver" in src or "capsolver" in src


def test_balance_report_mentions_cap():
    from grokreg.backends.captcha.balance import format_balance_report

    line = format_balance_report(
        {
            "ok": True,
            "primary": "capsolver",
            "yes": {"configured": False},
            "twocaptcha": {"configured": False},
            "capsolver": {"configured": True, "ok": True, "balance": 1.23},
        }
    )
    assert "Cap=" in line or "Cap" in line
