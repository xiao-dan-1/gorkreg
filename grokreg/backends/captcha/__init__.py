"""Captcha backends: auto / yescaptcha / capsolver / twocaptcha (+ impl).

Canonical:
  - get_captcha_backend / CaptchaBackend — preferred for register
  - impl — Yes/2C/browser/manual solvers
  - balance — preflight getBalance

Compat: ``grokreg.solver`` re-exports impl for older imports.
"""
from .base import CaptchaBackend
from .factory import get_captcha_backend
from .impl import (
    BrowserTurnstileSolver,
    LocalHttpTurnstileSolver,
    ManualTurnstileSolver,
    CapSolverSolver,
    TwoCaptchaSolver,
    YesCaptchaSolver,
    resolve_capsolver_endpoint,
    resolve_capsolver_key,
    resolve_twocaptcha_key,
    resolve_yescaptcha_endpoint,
    resolve_yescaptcha_key,
    solve_turnstile_auto,
)

__all__ = [
    "CaptchaBackend",
    "get_captcha_backend",
    "YesCaptchaSolver",
    "CapSolverSolver",
    "TwoCaptchaSolver",
    "ManualTurnstileSolver",
    "LocalHttpTurnstileSolver",
    "BrowserTurnstileSolver",
    "solve_turnstile_auto",
    "resolve_yescaptcha_key",
    "resolve_yescaptcha_endpoint",
    "resolve_capsolver_key",
    "resolve_capsolver_endpoint",
    "resolve_twocaptcha_key",
]
