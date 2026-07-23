"""Captcha dual-path: solver.py must stay shim; impl lives under backends.captcha."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_solver_is_shim_reexport():
    src = (ROOT / "grokreg" / "solver.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    defs = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    names = [getattr(d, "name", type(d).__name__) for d in defs]
    assert not defs, f"solver.py must not define solvers (got {names})"
    assert "backends.captcha.impl" in src or "from .backends.captcha.impl" in src


def test_impl_has_solver_classes():
    src = (ROOT / "grokreg" / "backends" / "captcha" / "impl.py").read_text(encoding="utf-8")
    assert "class YesCaptchaSolver" in src
    assert "class TwoCaptchaSolver" in src
    assert "def solve_turnstile_auto" in src


def test_backends_import_impl_not_root_solver():
    for rel in (
        "grokreg/backends/captcha/auto.py",
        "grokreg/backends/captcha/yescaptcha.py",
        "grokreg/backends/captcha/twocaptcha.py",
        "grokreg/backends/captcha/factory.py",
        "grokreg/backends/captcha/balance.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "from ...solver" not in src, rel
        assert "from .impl" in src, rel


def test_public_api_compat():
    from grokreg.solver import (
        YesCaptchaSolver,
        resolve_yescaptcha_key,
        solve_turnstile_auto,
    )
    from grokreg.backends.captcha import YesCaptchaSolver as Y2
    from grokreg.backends.captcha import get_captcha_backend

    assert YesCaptchaSolver is Y2
    assert callable(solve_turnstile_auto)
    assert callable(resolve_yescaptcha_key)
    assert callable(get_captcha_backend)
