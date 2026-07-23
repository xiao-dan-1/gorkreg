"""Captcha backend factory.

Names:
  auto       — solve_turnstile_auto fallback chain (default for single register)
  twocaptcha — 2Captcha only (batch path historical default)
  yescaptcha — YesCaptcha only
  capsolver  — CapSolver only
"""
from __future__ import annotations

import os
from typing import Any

from ...errors import ConfigError
from .impl import (
    resolve_capsolver_endpoint,
    resolve_capsolver_key,
    resolve_twocaptcha_key,
    resolve_yescaptcha_key,
)
from .auto import AutoCaptchaBackend
from .base import CaptchaBackend
from .twocaptcha import TwoCaptchaBackend
from .yescaptcha import YesCaptchaBackend
from .capsolver import CapSolverBackend


def get_captcha_backend(
    name: str | None = "auto",
    cfg: dict[str, Any] | None = None,
    *,
    turnstile_token: str | None = None,
    yescaptcha_key: str | None = None,
    yescaptcha_endpoint: str | None = None,
    yescaptcha_premium: bool = True,
    capsolver_key: str | None = None,
    capsolver_endpoint: str | None = None,
    twocaptcha_key: str | None = None,
    twocaptcha_endpoint: str | None = None,
    cloud_proxy: str | None = None,
    browser: bool = False,
    browser_channel: str = "chrome",
    browser_headless: bool = False,
    browser_proxy: str | None = None,
    local_solver_url: str | None = None,
    manual: bool = False,
    timeout: float = 180.0,
    debug: bool = False,
) -> CaptchaBackend:
    """
    Build a CaptchaBackend. Prefer explicit keys; fall back to cfg / env via solver resolvers.
    """
    cfg = cfg or {}
    yc_cfg = cfg.get("yescaptcha") or {}
    tc_cfg = cfg.get("twocaptcha") or {}
    cs_cfg = cfg.get("capsolver") or {}
    key = (name or "auto").strip().lower()

    yc_key = resolve_yescaptcha_key(yescaptcha_key or yc_cfg.get("api_key"))
    tc_key = resolve_twocaptcha_key(twocaptcha_key or tc_cfg.get("api_key"))
    cs_key = resolve_capsolver_key(capsolver_key or cs_cfg.get("api_key"))
    cs_ep = (
        (capsolver_endpoint or cs_cfg.get("endpoint") or "").strip()
        or None
    )
    yc_ep = (yescaptcha_endpoint or yc_cfg.get("endpoint") or "").strip() or None
    tc_ep = (twocaptcha_endpoint or tc_cfg.get("endpoint") or "").strip() or "https://2captcha.com"
    proxy = cloud_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
    to = float(timeout or tc_cfg.get("timeout") or yc_cfg.get("timeout") or 180)

    if key in {"auto", "default", ""}:
        return AutoCaptchaBackend(
            turnstile_token=turnstile_token,
            yescaptcha_key=yc_key or None,
            yescaptcha_endpoint=yc_ep,
            yescaptcha_premium=yescaptcha_premium if yescaptcha_premium is not None else bool(yc_cfg.get("premium", True)),
            yescaptcha_proxy=proxy,
            capsolver_key=cs_key or None,
            capsolver_endpoint=cs_ep,
            capsolver_proxy=proxy,
            twocaptcha_key=tc_key or None,
            twocaptcha_endpoint=tc_ep,
            twocaptcha_proxy=proxy,
            browser=browser,
            browser_channel=browser_channel,
            browser_headless=browser_headless,
            browser_proxy=browser_proxy,
            local_solver_url=local_solver_url,
            manual=manual,
            timeout=to,
            debug=debug,
        )

    if key in {"twocaptcha", "2captcha", "tc"}:
        if not tc_key:
            raise ConfigError("2Captcha key 未配置", code="captcha_config")
        return TwoCaptchaBackend(
            tc_key,
            endpoint=tc_ep,
            timeout=to,
            debug=debug,
            proxy=proxy,
        )

    if key in {"capsolver", "cap", "cs"}:
        if not cs_key:
            raise ConfigError("CapSolver key 未配置", code="captcha_config")
        return CapSolverBackend(
            cs_key,
            endpoint=cs_ep,
            timeout=to,
            debug=debug,
            proxy=proxy,
        )

    if key in {"yescaptcha", "yes", "yc"}:
        if not yc_key:
            raise ConfigError("YesCaptcha key 未配置", code="captcha_config")
        return YesCaptchaBackend(
            yc_key,
            endpoint=yc_ep,
            timeout=to,
            debug=debug,
            proxy=proxy,
            premium=bool(yc_cfg.get("premium", True)) if yescaptcha_premium is None else yescaptcha_premium,
        )

    raise ConfigError(f"unknown captcha backend: {name!r}", code="captcha_backend")
