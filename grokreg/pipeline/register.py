"""register_one orchestration — steps only; no protocol HTTP details.

P1 extract from cli._cmd_register / batch worker.
Behavior preserved defaults: captcha via factory (auto prefers Yes then 2C);
batch no longer hardcodes twocaptcha — same plugin path as single register.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..backends.captcha import get_captcha_backend
from ..backends.captcha.balance import check_captcha_balances, format_balance_report
from ..backends.captcha.impl import (
    mark_captcha_provider_skip,
    set_preferred_captcha_provider,
)
from ..backends.mail import get_mail_backend
from ..client import GrokAuthClient
from ..mail import normalize_xai_code, parse_mail_line
from ..proxyutil import resolve_proxy
from ..backends.captcha import (
    resolve_capsolver_key,
    resolve_twocaptcha_key,
    resolve_yescaptcha_key,
)
from ..sso import parse_sso_jwt_payload


@dataclass
class RegisterOptions:
    """Inputs for one registration run (CLI maps argparse → this)."""

    password: str = ""
    given_name: str = "Jennifer"
    family_name: str = "Mitchell"
    # Pre-supplied code skips mail wait (single-register path)
    code: str = ""
    skip_create: bool = False
    # Outlook 同根别名：create/SSO 用此邮箱；收码仍用 mail line 主号 cid/rt
    # 空 = 与 account.email 相同（默认）
    signup_email: str = ""
    # captcha: plugin name — auto | twocaptcha | yescaptcha
    # single/batch both go through factory (no hardcode)
    captcha_backend: str = "auto"
    # Fail-fast if cloud captcha wallets empty (Yes/Cap/2C getBalance)
    captcha_balance_check: bool = True
    # Overlap turnstile solve with wait_code (wall ≈ max, not sum). Safe: token TTL ~5min.
    captcha_prefetch: bool = True
    # Mail poll interval (seconds). None = adaptive by backend:
    #   cloudmail 0.5 (API cheap), graph/imap 2.0 (rate-friendlier than hard 3).
    mail_poll_interval: float | None = None
    mail_backend: str = "graph"
    mail_api_url: str = "https://outlook.xdauv.xyz"
    turnstile_token: str = ""
    castle_token: str = ""
    # keys / browser (single auto path)
    yescaptcha_key: str | None = None
    twocaptcha_key: str | None = None
    browser_turnstile: bool = False
    browser_channel: str = "chrome"
    browser_headless: bool = False
    local_solver_url: str | None = None
    manual_turnstile: bool = False
    verbose: bool = False
    # batch hardening
    proxy_probe_retries: int = 0
    create_short_body_retries: int = 0
    concurrent_jitter: bool = False
    index: int | None = None
    # scrape page-meta cache (public next-action/sitekey only; not cookies)
    scrape_cache: bool = True
    scrape_cache_ttl: float = 600.0
    # if True, require captcha keys when not skip_create (single CLI check)
    require_captcha_config: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def gen_password() -> str:
    return "Pw" + secrets.token_hex(6) + "!a#A"


def annotate_result_error(result: dict) -> None:
    """Fill error_code / retryable from result['error'] if missing."""
    err = result.get("error")
    if not err:
        result.setdefault("retryable", False)
        return
    if result.get("error_code") is not None and "retryable" in result:
        return
    from ..errors import CreateAccountError, ProtocolError, SSOError, classify

    s = str(err)
    low = s.lower()
    if low.startswith("create_code:") or low.startswith("create_account:"):
        e = CreateAccountError(s, code="create", retryable=True)
    elif low.startswith("verify:"):
        e = ProtocolError(s, code="verify", retryable=False)
    elif low == "sso_failed" or low.startswith("sso_failed"):
        e = SSOError(s, code="sso_failed", retryable=True)
    elif low == "no_code_and_no_mail_oauth":
        e = ProtocolError(s, code="mail_oauth", retryable=False)
    elif "mail_auth" in low or "aadsts70000" in low or "service abuse" in low:
        from ..errors import MailError

        e = MailError(s, code="mail_auth", retryable=False)
    else:
        e = classify(Exception(s))
    result.setdefault("error_code", e.code)
    result.setdefault("retryable", bool(e.retryable))


def result_ok(r: dict) -> bool:
    return bool(r.get("sso")) and not r.get("error")


def result_retryable(r: dict) -> bool:
    if result_ok(r):
        return False
    annotate_result_error(r)
    return bool(r.get("retryable"))



def _log_prefetch_fallback(exc: BaseException) -> None:
    """Prefetch turnstile failed → will sync-retry. One-line WARNING, no traceback.

    CapSolver ERROR_TASK_NOT_FOUND / expired tasks are recoverable; exc_info
    traceback spam only inflates logs and hides real failures.
    """
    msg = str(exc) or type(exc).__name__
    # keep short for batch walls
    if len(msg) > 160:
        msg = msg[:157] + "..."
    low = msg.lower()
    if "task_not_found" in low or "expired" in low:
        logging.warning("Turnstile prefetch expired/not found → sync retry (%s)", msg)
    else:
        logging.warning("Turnstile prefetch failed → sync retry (%s)", msg)




def _join_turnstile_prefetch(
    *,
    pref_future,
    pref_t0: float | None,
    now: float | None = None,
    max_join_s: float = 45.0,
    sync_solve,
) -> tuple[str, dict]:
    """Join prefetch with a wall budget; over-budget or error → sync_solve().

    Caps how long we wait on a stale Cap future (TASK_NOT_FOUND / long queue)
    so batch wall is not dominated by one expired prefetch + sync double-pay
    without bound. ``max_join_s`` is remaining wait from *now*, after age of
    prefetch start is accounted for.

    Important: if the future already finished, **always take it** before
    sync_solve (avoids double createTask when mail/wait outlives Cap).
    """
    import time as _time
    from concurrent.futures import TimeoutError as FuturesTimeout

    now = float(now if now is not None else _time.time())
    t0 = float(now if pref_t0 is None else pref_t0)
    age = max(0.0, now - t0)
    remain = max(0.5, float(max_join_s) - age)
    meta: dict = {
        "prefetch": False,
        "reason": "",
        "pref_age_s": round(age, 3),
        "join_budget_s": round(remain, 3),
    }

    def _take_if_done(reason: str) -> tuple[str, dict] | None:
        try:
            if not pref_future.done():
                return None
            tok, solve_sec = pref_future.result(timeout=0)
            meta["prefetch"] = True
            meta["reason"] = reason
            meta["solve_sec"] = float(solve_sec)
            return str(tok), meta
        except Exception:
            return None

    if age >= float(max_join_s):
        got = _take_if_done("prefetch_ok_late")
        if got is not None:
            logging.info(
                "Turnstile prefetch late-take age=%.1fs (avoid sync double-pay)",
                age,
            )
            return got
        try:
            pref_future.cancel()
        except Exception:
            pass
        # brief grace: Cap may finish mid-cancel
        try:
            tok, solve_sec = pref_future.result(timeout=1.5)
            meta["prefetch"] = True
            meta["reason"] = "prefetch_ok_grace"
            meta["solve_sec"] = float(solve_sec)
            return str(tok), meta
        except Exception:
            pass
        # Cap task already billed — wait it out (cap 90s) instead of second createTask
        meta["reason"] = "join_budget_wait"
        logging.warning(
            "Turnstile prefetch over join budget age=%.1fs → wait paid task (no sync double-pay)",
            age,
        )
        try:
            tok, solve_sec = pref_future.result(timeout=90.0)
            meta["prefetch"] = True
            meta["reason"] = "prefetch_ok_wait"
            meta["solve_sec"] = float(solve_sec)
            return str(tok), meta
        except Exception as wait_exc:
            meta["error"] = str(wait_exc)[:160]
            logging.warning("Turnstile wait paid task failed → sync: %s", str(wait_exc)[:120])
            return str(sync_solve()), meta
    try:
        tok, solve_sec = pref_future.result(timeout=remain)
        meta["prefetch"] = True
        meta["reason"] = "prefetch_ok"
        meta["solve_sec"] = float(solve_sec)
        return str(tok), meta
    except FuturesTimeout:
        got = _take_if_done("prefetch_ok_race")
        if got is not None:
            return got
        try:
            pref_future.cancel()
        except Exception:
            pass
        # short grace before paying a second createTask
        try:
            tok, solve_sec = pref_future.result(timeout=2.0)
            meta["prefetch"] = True
            meta["reason"] = "prefetch_ok_grace"
            meta["solve_sec"] = float(solve_sec)
            return str(tok), meta
        except Exception:
            pass
        # Prefer finishing the already-submitted Cap task over a second bill
        meta["reason"] = "join_timeout_wait"
        logging.warning(
            "Turnstile prefetch join timeout remain=%.1fs → wait paid task (no sync double-pay)",
            remain,
        )
        try:
            tok, solve_sec = pref_future.result(timeout=90.0)
            meta["prefetch"] = True
            meta["reason"] = "prefetch_ok_wait"
            meta["solve_sec"] = float(solve_sec)
            return str(tok), meta
        except Exception as wait_exc:
            meta["error"] = str(wait_exc)[:160]
            logging.warning("Turnstile wait paid task failed → sync: %s", str(wait_exc)[:120])
            return str(sync_solve()), meta
    except Exception as pref_exc:
        _log_prefetch_fallback(pref_exc)
        meta["reason"] = "prefetch_error"
        meta["error"] = str(pref_exc)[:200]
        # ZERO_BALANCE: do NOT sync-retry (would pay/createTask again for nothing)
        err_s = str(pref_exc)
        err_u = err_s.upper()
        if any(
            m in err_u or m in err_s
            for m in (
                "ERROR_ZERO_BALANCE",
                "ZERO_BALANCE",
                "INSUFFICIENT",
                "余额不足",
                "PREPAYMENT",
            )
        ):
            meta["reason"] = "zero_balance"
            try:
                from ..backends.captcha.impl import mark_captcha_provider_skip

                mark_captcha_provider_skip("capsolver", pref_exc, force=True)
            except Exception:
                pass
            raise RuntimeError(err_s) from pref_exc
        return str(sync_solve()), meta



def _is_verify_transport_error(err: Any) -> bool:
    """TLS/proxy/timeout/parse flakes on Verify — retry Verify, do NOT re-mail."""
    if not err:
        return False
    s = str(err)
    low = s.lower()
    if "request_error" in low:
        return True
    if "parse_error" in low or "wire type" in low:
        return True
    if "curl: (" in low or "failed to perform" in low:
        return True
    if "timed out" in low or "timeout" in low or "connection closed" in low:
        return True
    if "tls" in low or "ssl" in low or "openssl" in low:
        return True
    return False


def _is_invalid_code_verify_error(err: Any) -> bool:
    """Server rejected the OTP (or empty) — safe to fetch another code.

    Must NOT treat transport strings containing the substring ``invalid``
    (e.g. OpenSSL ``invalid library``) as bad codes.
    """
    if not err:
        return False
    if _is_verify_transport_error(err):
        return False
    low = str(err).lower().strip()
    if low in {"empty_body", "invalid", "invalid_code"}:
        return True
    if "invalid_code" in low or "invalid code" in low:
        return True
    if "empty_body" in low:
        return True
    # bare "invalid" only when not a transport/openssl sentence
    if low == "invalid" or low.startswith("invalid:") or low.endswith(":invalid"):
        return True
    # grpc business rejection sometimes embeds invalid without request_error
    if "invalid" in low and "library" not in low and "openssl" not in low and "curl" not in low:
        # still exclude long openssl-like noise
        if "error:00000000" in low or "openssl_internal" in low:
            return False
        return True
    return False



def register_one(
    cfg: dict,
    raw: str,
    proxy_override: Optional[str],
    opts: RegisterOptions,
) -> dict:
    """
    Full register: scrape → create_code → mail/code → verify → turnstile
    → create_account → SSO.

    ``raw``: full mail line (email----...) or bare email (needs opts.code).
    Returns result dict (does not write files — CLI/ops save).
    """
    account: dict | None = None
    email = ""
    raw = (raw or "").strip()
    mail_name_early = (opts.mail_backend or "graph").strip().lower()
    is_cloudmail = mail_name_early in {"cloudmail", "cloud-mail", "cloud_mail", "cm"}
    if "----" in raw:
        parts = [p.strip() for p in raw.split("----")]
        # email----cloudmail marker, or forced --mail-backend cloudmail
        if is_cloudmail or (
            len(parts) >= 2
            and parts[1].lower() in {"cloudmail", "cm", "catch-all", "catchall"}
        ):
            email = parts[0]
            if "@" not in email:
                return {
                    "email": email,
                    "password": opts.password or "",
                    "error": "invalid_email",
                    "error_code": "config",
                    "retryable": False,
                    "sso": None,
                    "code": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            account = {
                "email": email,
                "password": "",
                "client_id": "cloudmail",
                "refresh_token": "cloudmail_catch_all",
            }
            opts.mail_backend = "cloudmail"
        else:
            account = parse_mail_line(raw)
            email = account["email"]
    else:
        email = raw
        if "@" not in email:
            return {
                "email": email,
                "password": opts.password or "",
                "error": "invalid_email",
                "error_code": "config",
                "retryable": False,
                "sso": None,
                "code": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        if is_cloudmail:
            account = {
                "email": email,
                "password": "",
                "client_id": "cloudmail",
                "refresh_token": "cloudmail_catch_all",
            }

    # 同根别名：注册邮箱可覆盖；收码 account 仍是主号 OAuth 线
    signup = (opts.signup_email or "").strip()
    if signup:
        if "@" not in signup:
            return {
                "email": signup,
                "password": opts.password or "",
                "error": "invalid_signup_email",
                "error_code": "config",
                "retryable": False,
                "sso": None,
                "code": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        if not account and not (opts.code or "").strip():
            return {
                "email": signup,
                "password": opts.password or "",
                "error": "signup_email_needs_mail_line_or_code",
                "error_code": "config",
                "retryable": False,
                "sso": None,
                "code": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        email = signup

    password = (opts.password or "").strip() or gen_password()
    given = (opts.given_name or "Jennifer").strip() or "Jennifer"
    family = (opts.family_name or "Mitchell").strip() or "Mitchell"

    mail_root = ""
    if account and account.get("email"):
        mail_root = str(account.get("email") or "").strip()
    is_alias = bool(mail_root and email and mail_root.lower() != email.lower())
    mail_root_out = mail_root if is_alias else (mail_root or "")

    yc_cfg = cfg.get("yescaptcha") or {}
    tc_cfg = cfg.get("twocaptcha") or {}
    browser_cfg = cfg.get("browser_turnstile") or {}

    yc_key = resolve_yescaptcha_key(opts.yescaptcha_key)
    if not yc_key:
        yc_key = resolve_yescaptcha_key(yc_cfg.get("api_key"))
    tc_key = resolve_twocaptcha_key(opts.twocaptcha_key)
    if not tc_key:
        tc_key = resolve_twocaptcha_key(tc_cfg.get("api_key"))
    cs_cfg = cfg.get("capsolver") or {}
    cs_key = resolve_capsolver_key(cs_cfg.get("api_key") if isinstance(cs_cfg, dict) else None)

    local_solver_url = (
        (opts.local_solver_url or "").strip()
        or str(browser_cfg.get("local_solver_url") or "").strip()
        or None
    )
    use_browser = bool(opts.browser_turnstile or browser_cfg.get("enabled"))
    use_manual = bool(opts.manual_turnstile)
    turnstile_token = (opts.turnstile_token or "").strip()

    if opts.require_captcha_config and not opts.skip_create:
        if (
            not turnstile_token
            and not yc_key
            and not tc_key
            and not cs_key
            and not use_browser
            and not use_manual
            and not local_solver_url
        ):
            logging.error(
                "缺少 Turnstile。你有 2Captcha 时推荐：\n"
                "  set TWOCAPTCHA_API_KEY=你的key\n"
                "  或 set CAPSOLVER_API_KEY=… / YESCAPTCHA_API_KEY=…\n"
                "  或 --twocaptcha-key KEY（勿提交 git）\n"
                "其它：--yescaptcha-key / --browser-turnstile / --turnstile-token / --skip-create"
            )
            return {
                "email": email,
                "password": password,
                "error": "missing_captcha_config",
                "error_code": "captcha_config",
                "retryable": False,
                "sso": None,
                "code": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

    # Balance preflight: avoid scrape/OTP burn when Yes/2C wallets empty
    if (
        opts.captcha_balance_check
        and not opts.skip_create
        and not turnstile_token
        and not use_browser
        and not use_manual
        and not local_solver_url
    ):
        bal = check_captcha_balances(
            backend=opts.captcha_backend or "auto",
            cfg=cfg,
            yescaptcha_key=yc_key or None,
            twocaptcha_key=tc_key or None,
        )
        logging.info("%s", format_balance_report(bal))
        # Soft-skip only wallets we actually probed empty (auto chain).
        # Pinned backend (capsolver/yescaptcha/twocaptcha) must NOT mark unprobed
        # siblings — that produced spam WARNING "balance empty" every account when
        # YESCAPTCHA_API_KEY was set but CAPTCHA_BACKEND=capsolver.
        selected_backend = (opts.captcha_backend or "auto").strip().lower()
        if selected_backend in {"auto", "default", ""}:
            for pname, bkey in (
                ("yescaptcha", "yes"),
                ("capsolver", "capsolver"),
                ("twocaptcha", "twocaptcha"),
            ):
                d = bal.get(bkey) or {}
                if d.get("configured") and d.get("probed") and not d.get("ok"):
                    mark_captcha_provider_skip(
                        pname,
                        d.get("error") or f"balance empty ({pname})",
                        force=True,
                    )
        if bal.get("primary"):
            try:
                set_preferred_captcha_provider(str(bal.get("primary")))
            except Exception:  # noqa: BLE001
                pass
        if not bal.get("ok"):
            msg = bal.get("error") or "captcha balance preflight failed"
            logging.error("打码余额预检失败，中止注册（免烧 scrape/收码）: %s", msg)
            return {
                "email": email,
                "password": password,
                "error": f"captcha_balance: {msg}",
                "error_code": "captcha_balance",
                "retryable": False,
                "sso": None,
                "code": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "captcha_balance": {
                    "ok": False,
                    "primary": bal.get("primary"),
                    "error": msg,
                },
            }


    # Soft-skip: process already saw ZERO_BALANCE on pinned CapSolver
    try:
        from ..backends.captcha.impl import is_captcha_provider_skipped

        pin = (opts.captcha_backend or "auto").strip().lower()
        if pin in {"capsolver", "cap", "cs"} and is_captcha_provider_skipped("capsolver"):
            logging.error(
                "CapSolver 已 soft-skip（余额不足），跳过本号免烧 scrape/OTP"
            )
            return {
                "email": email,
                "password": password,
                "error": "captcha_zero_balance: soft-skipped",
                "error_code": "captcha_zero_balance",
                "retryable": False,
                "sso": None,
                "code": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception:
        pass

    resolved = resolve_proxy(cfg, proxy_override)
    # Proxy connectivity check (batch): retry with new SID on timeout
    probe_n = max(0, int(opts.proxy_probe_retries or 0))
    if probe_n and resolved.session_url:
        for _proxy_attempt in range(probe_n):
            try:
                from ..proxyutil import probe_proxy as _pp

                _pp(resolved.session_url, timeout=10)
                break
            except Exception:
                tag = f"[#{opts.index}] " if opts.index is not None else ""
                logging.warning(
                    "%sproxy %s unreachable, retry %s/%s",
                    tag,
                    resolved.sid or "?",
                    _proxy_attempt + 1,
                    probe_n,
                )
                resolved.close()
                if _proxy_attempt < probe_n - 1:
                    resolved = resolve_proxy(cfg, proxy_override)
                # else keep last; fail later with clear error

    client: GrokAuthClient | None = None
    timings: dict[str, float] = {}
    t_all0 = time.time()
    result: dict = {
        "email": email,
        "password": password,
        "given_name": given,
        "family_name": family,
        "code": None,
        "sso": None,
        "session_id": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timings_sec": timings,
        "elapsed_sec": None,
        "proxy_sid": resolved.sid or "",
        "proxy_region": resolved.region or "",
        # create soft-reject / short-body diagnostics (for bench)
        "create_attempts": 0,
        "short_body_hits": 0,
        "create_body_len": None,
        # 别名台账：注册邮箱 vs 收码主号
        "signup_email": email,
        "mail_root": mail_root_out,
        "is_alias": is_alias,
    }

    def _mark(step: str, t_start: float) -> float:
        dt = round(time.time() - t_start, 3)
        timings[step] = dt
        logging.info("耗时 %s=%.3fs", step, dt)
        return time.time()

    def _finalize_timing() -> None:
        result["elapsed_sec"] = round(time.time() - t_all0, 3)
        result["timings_sec"] = dict(timings)
        logging.info(
            "总耗时 %.3fs | %s",
            result["elapsed_sec"],
            " ".join(f"{k}={v:.3f}s" for k, v in timings.items()),
        )

    idx_tag = f"[#{opts.index}] " if opts.index is not None else ""

    try:
        logging.info(
            "%s注册开始 email=%s proxy=%s sid=%s region=%s session=%s",
            idx_tag,
            email,
            resolved.label(),
            resolved.sid or "-",
            resolved.region or "-",
            resolved.session_url or "直连",
        )
        client = GrokAuthClient(cfg, session_url=resolved.session_url, debug=opts.verbose)

        # 1. scrape (public page meta; optional process cache)
        t = time.time()
        use_scrape_cache = bool(getattr(opts, "scrape_cache", True))
        scrape_ttl = float(getattr(opts, "scrape_cache_ttl", 600.0) or 600.0)
        info = client.load_signup_page(use_cache=use_scrape_cache, cache_ttl=scrape_ttl)
        t = _mark("scrape", t)
        result["scrape_cache"] = info.get("scrape_cache") or "miss"
        sitekey = info.get("turnstile_sitekey") or client.turnstile_sitekey
        logging.info(
            "scrape ok cache=%s next-action=%s sitekey=%s",
            result["scrape_cache"],
            (info.get("next_action") or "")[:24],
            sitekey,
        )

        # Build captcha + optional prefetch ASAP (overlap create_code + wait_code)
        captcha_name = (opts.captcha_backend or "auto").strip().lower()
        cloud_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
        browser_proxy = resolved.session_url or (cfg.get("proxy") or {}).get("default")
        endpoint = (yc_cfg.get("endpoint") or "").strip() or None
        tc_endpoint = (tc_cfg.get("endpoint") or "").strip() or "https://2captcha.com"
        timeout = float(
            tc_cfg.get("timeout") or yc_cfg.get("timeout") or browser_cfg.get("timeout") or 180
        )
        captcha = None
        pref_executor: ThreadPoolExecutor | None = None
        pref_future: Future | None = None
        pref_t0: float | None = None
        captcha_prefetch = bool(getattr(opts, "captcha_prefetch", True)) and not turnstile_token

        def _build_captcha():
            if captcha_name in {"twocaptcha", "2captcha", "tc"}:
                return get_captcha_backend(
                    "twocaptcha",
                    cfg,
                    twocaptcha_key=tc_key,
                    twocaptcha_endpoint=tc_endpoint,
                    cloud_proxy=cloud_proxy,
                    timeout=timeout,
                    debug=opts.verbose,
                )
            if captcha_name in {"yescaptcha", "yes", "yc"}:
                return get_captcha_backend(
                    "yescaptcha",
                    cfg,
                    yescaptcha_key=yc_key,
                    yescaptcha_endpoint=endpoint,
                    yescaptcha_premium=bool(yc_cfg.get("premium", True)),
                    cloud_proxy=cloud_proxy,
                    timeout=timeout,
                    debug=opts.verbose,
                )
            if captcha_name in {"capsolver", "cap", "cs"}:
                return get_captcha_backend(
                    "capsolver",
                    cfg,
                    capsolver_key=cs_key,
                    cloud_proxy=cloud_proxy,
                    timeout=timeout,
                    debug=opts.verbose,
                )
            return get_captcha_backend(
                "auto",
                cfg,
                turnstile_token=None,
                yescaptcha_key=yc_key,
                yescaptcha_endpoint=endpoint,
                yescaptcha_premium=bool(yc_cfg.get("premium", True)),
                twocaptcha_key=tc_key,
                twocaptcha_endpoint=tc_endpoint,
                cloud_proxy=cloud_proxy,
                browser=use_browser,
                browser_channel=(opts.browser_channel or browser_cfg.get("channel") or "chrome"),
                browser_headless=bool(
                    opts.browser_headless
                    if opts.browser_turnstile
                    else browser_cfg.get("headless", False)
                ),
                browser_proxy=browser_proxy,
                local_solver_url=local_solver_url,
                manual=use_manual,
                timeout=timeout,
                debug=opts.verbose,
            )

        if captcha_prefetch and not opts.skip_create:
            captcha = _build_captcha()
            logging.info(
                "Turnstile prefetch start (overlap create_code+wait) backend=%s yescaptcha=%s "
                "capsolver=%s 2captcha=%s sitekey=%s",
                captcha_name,
                bool(yc_key),
                bool(cs_key),
                bool(tc_key),
                sitekey,
            )
            pref_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="captcha-pref")
            pref_t0 = time.time()

            def _pref_solve() -> tuple[str, float]:
                t_solve0 = time.time()
                tok = captcha.solve_turnstile(client.signup_url, sitekey)
                return tok, round(time.time() - t_solve0, 3)

            pref_future = pref_executor.submit(_pref_solve)

        # 2. create code (runs in parallel with captcha prefetch when enabled)
        # Fixed session baseline (unix UTC epoch; ref: xconsole since_now).
        # Set once BEFORE create; stale code → same baseline, never refresh.
        # Mail backends parse naive createTime as UTC (timeutil).
        code_baseline = time.time()
        create_res = client.create_email_validation_code(email)
        t = _mark("create_code", t)
        if not create_res.ok:
            result["error"] = f"create_code:{create_res.error}"
            logging.error("Create 失败: %s", create_res.error)
            annotate_result_error(result)
            if pref_future is not None:
                pref_future.cancel()
            if pref_executor is not None:
                pref_executor.shutdown(wait=False, cancel_futures=True)
            _finalize_timing()
            return result

        # 3. wait code / use provided
        code = normalize_xai_code(opts.code) if opts.code else ""
        mail = None
        tried_codes: set[str] = set()
        if not code:
            if not account:
                logging.error("未提供 --code，且 --register 不是完整 mail line，无法自动收码")
                result["error"] = "no_code_and_no_mail_oauth"
                annotate_result_error(result)
                if pref_future is not None:
                    pref_future.cancel()
                if pref_executor is not None:
                    pref_executor.shutdown(wait=False, cancel_futures=True)
                _finalize_timing()
                return result
            # Graph/OAuth 读信不需要动态代理；仅注册 client 走辣椒 session。
            # IMAP API 默认也直连（中转自有出口）；显式 proxy 仅在需要时再加。
            mail_name = opts.mail_backend or "graph"
            mail_api = opts.mail_api_url or "https://outlook.xdauv.xyz"
            mail_proxy = None  # 收码直连
            mail = get_mail_backend(
                mail_name,
                account,
                proxy=mail_proxy,
                api_url=mail_api,
                impersonate=(cfg.get("browser") or {}).get("impersonate") or "chrome131",
                cfg=cfg,
            )
            # Adaptive poll: CloudMail self-hosts → 0.5s; Graph/IMAP → 2s (was hard 3).
            explicit_poll = getattr(opts, "mail_poll_interval", None)
            if explicit_poll is not None:
                mail_poll = max(0.2, float(explicit_poll))
            else:
                mn = (mail_name or "").strip().lower()
                if mn in {"cloudmail", "cm", "cloud-mail"}:
                    mail_poll = 0.5
                else:
                    mail_poll = 2.0
            logging.info(
                "等待收 xAI 验证码 (backend=%s proxy=%s poll=%.2fs baseline=%.0f)...",
                mail_name,
                "direct" if not mail_proxy else "via-proxy",
                mail_poll,
                code_baseline,
            )
            code = mail.wait_for_xai_code(
                after_ts=code_baseline,
                timeout=120,
                interval=mail_poll,
                exclude_codes=tried_codes or None,
            )
            t = _mark("wait_code", t)
        else:
            timings["wait_code"] = 0.0
            mail_poll = 2.0
            logging.info("使用已有验证码，跳过收码")
        result["code"] = code
        logging.info("验证码: %s (baseline=%.0f)", code, code_baseline)

        # 4. verify + validate_password in parallel (they're independent)
        # skip_create 时不需要 validate_password
        should_validate = not opts.skip_create
        
        with ThreadPoolExecutor(max_workers=2 if should_validate else 1) as pool:
            # verify 必须在有 code 后才能开始
            verify_future = pool.submit(client.verify_email_validation_code, email, code)
            
            # validate_password 可以和 verify 并行（如果需要）
            validate_future = None
            if should_validate:
                validate_future = pool.submit(client.validate_password, email, password)
            
            # 等待 verify 完成
            verify_res = verify_future.result()
            verr = verify_res.error or ""
            # 1) transport flake: re-POST Verify with SAME code (no 90s re-mail)
            if (
                not verify_res.ok
                and bool(code)
                and _is_verify_transport_error(verr)
            ):
                logging.warning(
                    "Verify transport flake (%s) → retry same code (no re-mail)",
                    verr,
                )
                verify_res = client.verify_email_validation_code(email, code)
            # 2) true invalid OTP: exclude code and wait for a new mail (90s)
            retry_new_code = (
                not verify_res.ok
                and mail is not None
                and bool(code)
                and _is_invalid_code_verify_error(verify_res.error or "")
            )
            if retry_new_code:
                tried_codes.add(normalize_xai_code(code))
                logging.warning(
                    "Verify 失败 (%s)，同 baseline=%.0f 排除 %s 再收码…",
                    verify_res.error,
                    code_baseline,
                    sorted(tried_codes),
                )
                try:
                    code2 = mail.wait_for_xai_code(
                        after_ts=code_baseline,  # unchanged
                        timeout=90,
                        interval=mail_poll,
                        exclude_codes=tried_codes,
                    )
                    code2n = normalize_xai_code(code2) if code2 else ""
                    if code2n and code2n not in tried_codes:
                        code = code2n
                        result["code"] = code
                        logging.info("换新码再验: %s", code)
                        verify_res = client.verify_email_validation_code(email, code)
                except Exception as exc:
                    logging.warning("再收码失败: %s", exc)
            t = _mark("verify", t)
            
            # 等待 validate_password 完成（如果已开始）
            if validate_future is not None:
                try:
                    validate_future.result(timeout=10)
                except Exception as exc:
                    logging.warning("ValidatePassword 跳过: %s", exc)
                timings["validate_password"] = round(time.time() - t, 3)
                t = time.time()
            
        if not verify_res.ok:
            result["error"] = f"verify:{verify_res.error}"
            logging.error("Verify 失败: %s", verify_res.error)
            annotate_result_error(result)
            if pref_future is not None:
                pref_future.cancel()
            if pref_executor is not None:
                pref_executor.shutdown(wait=False, cancel_futures=True)
            _finalize_timing()
            return result
        logging.info("Verify 成功")

        if opts.skip_create:
            logging.info("skip-create：停在验码成功")
            result["error"] = None
            if pref_future is not None:
                pref_future.cancel()
            if pref_executor is not None:
                pref_executor.shutdown(wait=False, cancel_futures=True)
            _finalize_timing()
            return result

        # 6. Turnstile — join prefetch or solve now
        if not turnstile_token:
            if captcha is None:
                captcha = _build_captcha()
                logging.info(
                    "Turnstile: backend=%s yescaptcha=%s capsolver=%s 2captcha=%s "
                    "browser=%s local=%s manual=%s sitekey=%s",
                    captcha_name,
                    bool(yc_key),
                    bool(cs_key),
                    bool(tc_key),
                    use_browser,
                    bool(local_solver_url),
                    use_manual,
                    sitekey,
                )

            def _do_turnstile() -> str:
                return captcha.solve_turnstile(client.signup_url, sitekey)

            if pref_future is not None:
                join_t0 = time.time()
                # Cap join budget: avoid waiting forever on expired Cap tasks.
                # Env GROK_CAPTCHA_PREFETCH_JOIN_S overrides (default 45s).
                try:
                    import os as _os

                    max_join = float(
                        (_os.environ.get("GROK_CAPTCHA_PREFETCH_JOIN_S") or "45").strip()
                        or "45"
                    )
                except ValueError:
                    max_join = 45.0
                max_join = max(5.0, min(180.0, max_join))
                turnstile_token, jmeta = _join_turnstile_prefetch(
                    pref_future=pref_future,
                    pref_t0=pref_t0,
                    now=join_t0,
                    max_join_s=max_join,
                    sync_solve=_do_turnstile,
                )
                result["captcha_prefetch"] = bool(jmeta.get("prefetch"))
                if jmeta.get("error"):
                    result["captcha_prefetch_error"] = str(jmeta.get("error"))[:200]
                result["captcha_prefetch_meta"] = {
                    k: jmeta.get(k)
                    for k in ("reason", "pref_age_s", "join_budget_s", "solve_sec")
                    if jmeta.get(k) is not None
                }
                if jmeta.get("prefetch") and jmeta.get("solve_sec") is not None:
                    timings["turnstile"] = float(jmeta["solve_sec"])
                    logging.info(
                        "Turnstile ok (prefetch) len=%s solve=%.3fs age=%.1fs",
                        len(turnstile_token),
                        float(jmeta["solve_sec"]),
                        float(jmeta.get("pref_age_s") or 0),
                    )
                else:
                    t = _mark("turnstile", t)
                    logging.info(
                        "Turnstile ok (sync fallback reason=%s) len=%s",
                        jmeta.get("reason"),
                        len(turnstile_token),
                    )
                if pref_executor is not None:
                    pref_executor.shutdown(wait=False, cancel_futures=True)
                    pref_executor = None
                    pref_future = None
            else:
                turnstile_token = _do_turnstile()
                t = _mark("turnstile", t)
                logging.info("Turnstile ok len=%s", len(turnstile_token))
                result["captcha_prefetch"] = False
        else:
            timings["turnstile"] = 0.0

            def _do_turnstile() -> str:
                return turnstile_token

        # Light stagger before create when batch -j N (was 0.5–2.5s pure wall tax).
        # Keep tiny random to de-sync create_account storms without eating per_ok.
        if opts.concurrent_jitter:
            import random as _random

            jitter = _random.uniform(0.05, 0.35)
            time.sleep(jitter)

        # 7. create_account (+ optional short-body retry)
        signup = client.create_account(
            email=email,
            given_name=given,
            family_name=family,
            password=password,
            email_validation_code=code,
            turnstile_token=turnstile_token,
            castle_request_token=(opts.castle_token or ""),
        )
        create_attempts = 1
        short_hits = 0
        short_retries = max(0, int(opts.create_short_body_retries or 0))
        for _retry in range(short_retries):
            body_len = len(getattr(signup, "rsc_body", "") or "")
            if signup.ok and body_len >= 1000:
                break
            if signup.ok and body_len < 1000:
                short_hits += 1
                logging.warning(
                    "%screate_account short body=%s, retry %s/%s",
                    idx_tag,
                    body_len,
                    _retry + 1,
                    short_retries,
                )
            elif not signup.ok:
                break
            time.sleep(1.5 + _retry * 1.0)
            turnstile_token = _do_turnstile()
            signup = client.create_account(
                email=email,
                given_name=given,
                family_name=family,
                password=password,
                email_validation_code=code,
                turnstile_token=turnstile_token,
                castle_request_token=(opts.castle_token or ""),
            )
            create_attempts += 1
        body_len_final = len(getattr(signup, "rsc_body", "") or "")
        # last response still short but ok → count as hit (retry budget exhausted)
        if signup.ok and body_len_final < 1000:
            short_hits = max(short_hits, 1)
        result["create_attempts"] = create_attempts
        result["short_body_hits"] = short_hits
        result["create_body_len"] = body_len_final
        t = _mark("create_account", t)
        if not signup.ok:
            result["error"] = f"create_account:{signup.error or signup.http_status}"
            from .. import scrape_cache as _scrape_cache
            if _scrape_cache.should_invalidate_error(str(signup.error or result["error"])):
                _scrape_cache.invalidate(str(signup.error or "")[:120])
            logging.error(
                "create_account 失败: %s body_preview=%r",
                signup.error,
                (signup.rsc_body or "")[:300],
            )
            annotate_result_error(result)
            _finalize_timing()
            return result
        logging.info(
            "create_account 成功 HTTP=%s body_len=%s short_hits=%s attempts=%s",
            signup.http_status,
            body_len_final,
            short_hits,
            create_attempts,
        )

        # 8. SSO (hop chain; optional CreateSession password rescue)
        sso = client.fetch_sso_token(retries=3)
        t = _mark("sso", t)
        result["sso"] = sso
        if not sso and password:
            # Fresh Turnstile + CreateSession — does not re-create account
            try:
                captcha = captcha or _build_captcha()
                rescue_ts = captcha.solve_turnstile(client.signup_url, sitekey)
                logging.warning(
                    "SSO hop empty → CreateSession rescue email=%s (burns 1 captcha)",
                    email,
                )
                sso = client.fetch_sso_token(
                    retries=1,
                    email=email,
                    password=password,
                    turnstile_token=rescue_ts,
                )
                if sso:
                    result["sso"] = sso
                    result["sso_via"] = "create_session"
                    timings["sso_rescue"] = round(time.time() - t, 3)
                    logging.info("SSO rescued via CreateSession")
            except Exception as exc:
                logging.warning("SSO CreateSession rescue failed: %s", exc)
        if sso:
            payload = parse_sso_jwt_payload(sso) or {}
            result["session_id"] = payload.get("session_id")
            logging.info("SSO ok session_id=%s", str(result["session_id"] or "?")[:16])
            # clear prior error if any
            if result.get("error") == "sso_failed":
                result.pop("error", None)
                result.pop("error_code", None)
        else:
            result["error"] = "sso_failed"
            annotate_result_error(result)
            logging.warning(
                "账号可能已创建，但 SSO 提取失败；请保存 email/password "
                "(可用 scripts/recover_sso_failed.py 批后补救)"
            )

        _finalize_timing()
        return result
    except Exception as exc:
        from ..errors import classify

        err = classify(exc)
        result["error"] = f"{err.code}:{exc}"
        result["error_code"] = err.code
        result["retryable"] = err.retryable
        logging.exception("%s注册异常: %s", idx_tag, err)
        _finalize_timing()
        return result
    finally:
        if client is not None:
            client.close()
        resolved.close()
