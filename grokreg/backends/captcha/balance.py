"""Captcha balance preflight — fail-fast before scrape/OTP when cloud solvers empty.

YesCaptcha: POST /getBalance (platform credits)
2Captcha:   GET  res.php?action=getbalance (USD)
CapSolver:  POST /getBalance (USD)

Optimizations (2026-07-20 j8 RATE_LIMIT):
  - process-local cache + single-flight (j workers share one probe)
  - transient ERROR_RATE_LIMIT retries with backoff
  - probed flag so pinned backend does not soft-skip unprobed wallets
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from typing import Any

from .impl import (
    CapSolverSolver,
    TwoCaptchaSolver,
    YesCaptchaSolver,
    resolve_capsolver_endpoint,
    resolve_capsolver_key,
    resolve_twocaptcha_key,
    resolve_yescaptcha_endpoint,
    resolve_yescaptcha_key,
)

logger = logging.getLogger(__name__)

# Process-local balance cache (single-flight). Keyed by backend+keys fingerprint.
_BAL_LOCK = threading.Lock()
_BAL_CV = threading.Condition(_BAL_LOCK)
_BAL_CACHE: dict[str, Any] | None = None
_BAL_CACHE_KEY: str | None = None
_BAL_CACHE_MONO: float = 0.0
_BAL_INFLIGHT: bool = False
_BAL_CACHE_TTL_SEC = 45.0  # short: enough to cover a batch wave; re-probe often
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BASE_SLEEP = 0.35


def clear_balance_cache() -> None:
    """Test/helper: drop cached balance preflight result."""
    global _BAL_CACHE, _BAL_CACHE_KEY, _BAL_CACHE_MONO, _BAL_INFLIGHT
    with _BAL_CV:
        _BAL_CACHE = None
        _BAL_CACHE_KEY = None
        _BAL_CACHE_MONO = 0.0
        _BAL_INFLIGHT = False
        _BAL_CV.notify_all()


def _proxy_from_env() -> str | None:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None


def _is_rate_limit(exc: BaseException | str) -> bool:
    s = str(exc or "").upper()
    return "RATE_LIMIT" in s or "ERROR_RATE_LIMIT" in s or "TOO MANY REQUESTS" in s


def _get_balance_with_retry(label: str, fn) -> float:
    """Call get_balance with backoff on transient rate limits."""
    last: BaseException | None = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return float(fn())
        except Exception as e:  # noqa: BLE001
            last = e
            if _is_rate_limit(e) and attempt < _RATE_LIMIT_RETRIES:
                sleep_s = _RATE_LIMIT_BASE_SLEEP * (2**attempt)
                logger.warning(
                    "%s getBalance rate-limit attempt=%d/%d sleep=%.2fs: %s",
                    label,
                    attempt + 1,
                    _RATE_LIMIT_RETRIES + 1,
                    sleep_s,
                    e,
                )
                time.sleep(sleep_s)
                continue
            raise
    assert last is not None
    raise last


def _cache_key(
    name: str,
    yc_key: str | None,
    cs_key: str | None,
    tc_key: str | None,
    yc_ep: str | None,
    cs_ep: str | None,
) -> str:
    # fingerprint keys by length + suffix only (no secret in memory key beyond that)
    def _fp(k: str | None) -> str:
        if not k:
            return "-"
        return f"{len(k)}:{k[-4:]}"

    return "|".join(
        [
            name or "auto",
            _fp(yc_key),
            _fp(cs_key),
            _fp(tc_key),
            (yc_ep or "").strip() or "-",
            (cs_ep or "").strip() or "-",
        ]
    )


def _probe_balances(
    *,
    name: str,
    yc_key: str | None,
    tc_key: str | None,
    cs_key: str | None,
    yc_ep: str | None,
    cs_ep: str | None,
    px: str | None,
    min_yes: float,
    min_2c: float,
    require_configured: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "yes": {
            "configured": bool(yc_key),
            "probed": False,
            "balance": None,
            "ok": False,
            "error": None,
        },
        "twocaptcha": {
            "configured": bool(tc_key),
            "probed": False,
            "balance": None,
            "ok": False,
            "error": None,
        },
        "capsolver": {
            "configured": bool(cs_key),
            "probed": False,
            "balance": None,
            "ok": False,
            "error": None,
        },
        "primary": None,
        "error": None,
        "backend": name,
        "cached": False,
        "from_cache": False,
    }

    need_yes = name in {"yescaptcha", "yes", "yc", "auto", "default", ""}
    need_2c = name in {"twocaptcha", "2captcha", "tc", "auto", "default", ""}
    need_cs = name in {"capsolver", "cap", "cs", "auto", "default", ""}

    if need_yes and yc_key:
        out["yes"]["probed"] = True
        try:
            bal = _get_balance_with_retry(
                "YesCaptcha",
                lambda: YesCaptchaSolver(
                    yc_key, endpoint=yc_ep or resolve_yescaptcha_endpoint(), proxy=px
                ).get_balance(),
            )
            out["yes"]["balance"] = bal
            out["yes"]["ok"] = bal >= float(min_yes)
            if not out["yes"]["ok"]:
                out["yes"]["error"] = f"balance={bal} < min={min_yes}"
        except Exception as e:  # noqa: BLE001
            out["yes"]["error"] = f"{type(e).__name__}: {e}"
            logger.warning("YesCaptcha getBalance failed: %s", e)
    elif need_yes and require_configured and name in {"yescaptcha", "yes", "yc"}:
        out["yes"]["error"] = "YESCAPTCHA_API_KEY missing"

    if need_2c and tc_key:
        out["twocaptcha"]["probed"] = True
        try:
            bal = _get_balance_with_retry(
                "2Captcha",
                lambda: TwoCaptchaSolver(tc_key, proxy=px).get_balance(),
            )
            out["twocaptcha"]["balance"] = bal
            out["twocaptcha"]["ok"] = bal >= float(min_2c)
            if not out["twocaptcha"]["ok"]:
                out["twocaptcha"]["error"] = f"balance={bal} < min={min_2c}"
        except Exception as e:  # noqa: BLE001
            out["twocaptcha"]["error"] = f"{type(e).__name__}: {e}"
            logger.warning("2Captcha getbalance failed: %s", e)
    elif need_2c and require_configured and name in {"twocaptcha", "2captcha", "tc"}:
        out["twocaptcha"]["error"] = "TWOCAPTCHA_API_KEY missing"

    if need_cs and cs_key:
        out["capsolver"]["probed"] = True
        try:
            bal = _get_balance_with_retry(
                "CapSolver",
                lambda: CapSolverSolver(
                    cs_key,
                    endpoint=resolve_capsolver_endpoint(cs_ep),
                    proxy=px,
                ).get_balance(),
            )
            out["capsolver"]["balance"] = bal
            out["capsolver"]["ok"] = bal >= float(min_2c)
            if not out["capsolver"]["ok"]:
                out["capsolver"]["error"] = f"balance={bal} < min={min_2c}"
        except Exception as e:  # noqa: BLE001
            out["capsolver"]["error"] = f"{type(e).__name__}: {e}"
            logger.warning("CapSolver getBalance failed: %s", e)
    elif need_cs and require_configured and name in {"capsolver", "cap", "cs"}:
        out["capsolver"]["error"] = "CAPSOLVER_API_KEY missing"

    # pick primary
    if name in {"yescaptcha", "yes", "yc"}:
        out["ok"] = bool(out["yes"]["ok"])
        out["primary"] = "yescaptcha" if out["ok"] else None
        if not out["ok"]:
            out["error"] = out["yes"].get("error") or "YesCaptcha not ready"
    elif name in {"twocaptcha", "2captcha", "tc"}:
        out["ok"] = bool(out["twocaptcha"]["ok"])
        out["primary"] = "twocaptcha" if out["ok"] else None
        if not out["ok"]:
            out["error"] = out["twocaptcha"].get("error") or "2Captcha not ready"
    elif name in {"capsolver", "cap", "cs"}:
        out["ok"] = bool(out["capsolver"]["ok"])
        out["primary"] = "capsolver" if out["ok"] else None
        if not out["ok"]:
            out["error"] = out["capsolver"].get("error") or "CapSolver not ready"
    else:
        # auto: Yes → CapSolver → 2C
        if out["yes"]["ok"]:
            out["ok"] = True
            out["primary"] = "yescaptcha"
        elif out["capsolver"]["ok"]:
            out["ok"] = True
            out["primary"] = "capsolver"
        elif out["twocaptcha"]["ok"]:
            out["ok"] = True
            out["primary"] = "twocaptcha"
        else:
            out["ok"] = False
            parts = []
            if yc_key:
                parts.append(f"Yes={out['yes'].get('error') or out['yes'].get('balance')}")
            if cs_key:
                parts.append(
                    f"Cap={out['capsolver'].get('error') or out['capsolver'].get('balance')}"
                )
            if tc_key:
                parts.append(
                    f"2C={out['twocaptcha'].get('error') or out['twocaptcha'].get('balance')}"
                )
            if not yc_key and not cs_key and not tc_key:
                parts.append("no YES/CAPSOLVER/2C API keys")
            out["error"] = "captcha balance preflight failed: " + "; ".join(parts)

    return out


def check_captcha_balances(
    *,
    backend: str = "auto",
    cfg: dict[str, Any] | None = None,
    yescaptcha_key: str | None = None,
    twocaptcha_key: str | None = None,
    yescaptcha_endpoint: str | None = None,
    proxy: str | None = None,
    min_yes: float = 0.01,
    min_2c: float = 0.001,
    require_configured: bool = True,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Probe configured captcha wallets (cached + single-flight).

    Returns:
      {
        ok: bool,
        yes|capsolver|twocaptcha: {configured, probed, balance|None, ok, error?}
        primary: str|None
        error: str|None
        cached/from_cache: bool
      }

    Policy by backend name:
      yescaptcha — only Yes must be ok (only Yes probed)
      capsolver  — only Cap probed
      twocaptcha — only 2C probed
      auto       — Yes → Cap → 2C; any ok is enough
    """
    cfg = cfg or {}
    yc_cfg = cfg.get("yescaptcha") if isinstance(cfg.get("yescaptcha"), dict) else {}
    tc_cfg = cfg.get("twocaptcha") if isinstance(cfg.get("twocaptcha"), dict) else {}
    name = (backend or "auto").strip().lower()
    px = proxy if proxy is not None else _proxy_from_env()

    yc_key = resolve_yescaptcha_key(yescaptcha_key or yc_cfg.get("api_key"))
    tc_key = resolve_twocaptcha_key(twocaptcha_key or tc_cfg.get("api_key"))
    cs_cfg = cfg.get("capsolver") if isinstance(cfg.get("capsolver"), dict) else {}
    cs_key = resolve_capsolver_key(cs_cfg.get("api_key") if isinstance(cs_cfg, dict) else None)
    cs_ep = cs_cfg.get("endpoint") if isinstance(cs_cfg, dict) else None
    yc_ep = (yescaptcha_endpoint or yc_cfg.get("endpoint") or "").strip() or None

    global _BAL_CACHE, _BAL_CACHE_KEY, _BAL_CACHE_MONO, _BAL_INFLIGHT

    key = _cache_key(name, yc_key, cs_key, tc_key, yc_ep, str(cs_ep) if cs_ep else None)

    if use_cache:
        with _BAL_CV:
            now = time.monotonic()
            if (
                _BAL_CACHE is not None
                and _BAL_CACHE_KEY == key
                and (now - _BAL_CACHE_MONO) < _BAL_CACHE_TTL_SEC
            ):
                hit = copy.deepcopy(_BAL_CACHE)
                hit["cached"] = True
                hit["from_cache"] = True
                return hit
            # wait for in-flight probe of same key
            while _BAL_INFLIGHT:
                _BAL_CV.wait(timeout=30.0)
                now = time.monotonic()
                if (
                    _BAL_CACHE is not None
                    and _BAL_CACHE_KEY == key
                    and (now - _BAL_CACHE_MONO) < _BAL_CACHE_TTL_SEC
                ):
                    hit = copy.deepcopy(_BAL_CACHE)
                    hit["cached"] = True
                    hit["from_cache"] = True
                    return hit
            # we are the prober
            _BAL_INFLIGHT = True

    try:
        out = _probe_balances(
            name=name,
            yc_key=yc_key,
            tc_key=tc_key,
            cs_key=cs_key,
            yc_ep=yc_ep,
            cs_ep=cs_ep,
            px=px,
            min_yes=min_yes,
            min_2c=min_2c,
            require_configured=require_configured,
        )
        # only cache successful or definitive empty results — not pure rate-limit collapses
        if use_cache:
            with _BAL_CV:
                # Do not cache pure rate-limit failures (allow next wave to retry)
                err = (out.get("error") or "").upper()
                cap_err = ((out.get("capsolver") or {}).get("error") or "").upper()
                yes_err = ((out.get("yes") or {}).get("error") or "").upper()
                pure_rl = (not out.get("ok")) and (
                    "RATE_LIMIT" in err or "RATE_LIMIT" in cap_err or "RATE_LIMIT" in yes_err
                )
                if not pure_rl:
                    _BAL_CACHE = copy.deepcopy(out)
                    _BAL_CACHE_KEY = key
                    _BAL_CACHE_MONO = time.monotonic()
                out["cached"] = False
                out["from_cache"] = False
        return out
    finally:
        if use_cache:
            with _BAL_CV:
                _BAL_INFLIGHT = False
                _BAL_CV.notify_all()


def format_balance_report(info: dict[str, Any]) -> str:
    """One-line human summary (no secrets)."""
    yes = info.get("yes") or {}
    tw = info.get("twocaptcha") or {}

    def _one(label: str, d: dict) -> str:
        if not d.get("configured"):
            return f"{label}=n/a"
        if d.get("probed") is False:
            return f"{label}=skip"
        if d.get("ok"):
            return f"{label}={d.get('balance')}"
        err = d.get("error") or "fail"
        bal = d.get("balance")
        if bal is not None:
            return f"{label}={bal}({err})"
        return f"{label}=ERR({err})"

    cs = info.get("capsolver") or {}
    status = "ok" if info.get("ok") else "FAIL"
    primary = info.get("primary") or "-"
    cache_tag = " cache" if info.get("from_cache") or info.get("cached") else ""
    return (
        f"captcha-balance {status} primary={primary}{cache_tag} "
        f"{_one('Yes', yes)} {_one('Cap', cs)} {_one('2C', tw)}"
    )
