"""Turnstile solvers — compat re-export.

Canonical implementation: ``grokreg.backends.captcha.impl``.
New code should use ``grokreg.backends.captcha.get_captcha_backend``.
"""
from __future__ import annotations

from .backends.captcha.impl import (  # noqa: F401
    BrowserTurnstileSolver,
    DEFAULT_ENDPOINTS,
    LocalHttpTurnstileSolver,
    ManualTurnstileSolver,
    TURNSTILE_API_JS,
    TwoCaptchaSolver,
    YesCaptchaSolver,
    resolve_twocaptcha_key,
    resolve_yescaptcha_endpoint,
    resolve_yescaptcha_key,
    solve_turnstile_auto,
)

__all__ = [
    "DEFAULT_ENDPOINTS",
    "TURNSTILE_API_JS",
    "resolve_yescaptcha_endpoint",
    "resolve_yescaptcha_key",
    "resolve_twocaptcha_key",
    "YesCaptchaSolver",
    "TwoCaptchaSolver",
    "ManualTurnstileSolver",
    "LocalHttpTurnstileSolver",
    "BrowserTurnstileSolver",
    "solve_turnstile_auto",
]
