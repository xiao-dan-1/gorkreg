"""Env / proxy diagnostics CLI commands (from grokreg.cli)."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any, Optional

from .. import logutil

log = logging.getLogger(__name__)

def _fmt_proxy_preflight(info: dict) -> str:
    """One-line structured log for proxy preflight (logging only, no print)."""
    nested = info.get("nested") or {}
    probe = info.get("probe") or {}
    nested_ms = info.get("nested_ms")
    if nested_ms is None and nested:
        nested_ms = nested.get("ms")
    probe_ms = info.get("probe_ms")
    if probe_ms is None and probe:
        probe_ms = probe.get("ms")
    fields = {
        "ok": 1 if info.get("ok") else 0,
        "chain": 1 if info.get("needed_chain") else 0,
        "hop1": info.get("hop1") or "-",
        "upstream": info.get("upstream") or "-",
        "nested_ms": nested_ms if nested_ms is not None else "-",
        "probe_ms": probe_ms if probe_ms is not None else "-",
        "total_ms": info.get("total_ms") if info.get("total_ms") is not None else "-",
        "ip": probe.get("ip") or "-",
    }
    if nested:
        fields["nested"] = (
            f"{1 if nested.get('hop1_listen') else 0}/"
            f"{1 if nested.get('hop1_connect_hop2') else 0}/"
            f"{1 if nested.get('hop2_connect_target') else 0}"
        )
    if info.get("skipped"):
        fields["skipped"] = 1
    if info.get("error") and not info.get("ok"):
        fields["err"] = str(info.get("error") or "").replace("\n", " ").strip()[:160]
    return logutil.kv("proxy-preflight", **fields)


def _print_check_chain_table(info: dict) -> None:
    """Human-readable table for --check-chain (stdout). Logs stay one-line elsewhere."""
    nested = info.get("nested") or {}
    probe = info.get("probe") or {}
    ok = bool(info.get("ok"))
    status = "PASS" if ok else "FAIL"
    if info.get("skipped"):
        status = "SKIP"

    def _yn(v: Any) -> str:
        return "yes" if v else "no"

    def _ms(v: Any) -> str:
        if v is None or v == "" or v == "-":
            return "-"
        try:
            return f"{float(v):.0f} ms"
        except (TypeError, ValueError):
            return str(v)

    rows: list[tuple[str, str]] = [
        ("result", status),
        ("chain", _yn(info.get("needed_chain"))),
        ("hop1", str(info.get("hop1") or "-")),
        ("upstream", str(info.get("upstream") or "-")),
    ]
    if nested:
        rows.extend(
            [
                ("hop1_listen", _yn(nested.get("hop1_listen"))),
                ("hop1→hop2", _yn(nested.get("hop1_connect_hop2"))),
                ("hop2→target", _yn(nested.get("hop2_connect_target"))),
                ("nested", _ms(info.get("nested_ms") if info.get("nested_ms") is not None else nested.get("ms"))),
            ]
        )
    rows.extend(
        [
            ("exit_ip", str(probe.get("ip") or "-")),
            ("probe_http", str(probe.get("status") if probe.get("status") is not None else "-")),
            ("probe", _ms(info.get("probe_ms") if info.get("probe_ms") is not None else probe.get("ms"))),
            ("total", _ms(info.get("total_ms"))),
        ]
    )
    if info.get("warning"):
        rows.append(("warning", str(info.get("warning"))))
    if info.get("error") and not ok:
        rows.append(("error", str(info.get("error")).replace("\n", " ").strip()[:200]))
    if info.get("skipped") and info.get("error"):
        rows.append(("note", str(info.get("error"))))

    key_w = max(len(k) for k, _ in rows)
    val_w = max(len(v) for _, v in rows)
    key_w = max(key_w, 6)
    val_w = max(min(val_w, 72), 12)
    sep = f"+-{'-' * key_w}-+-{'-' * val_w}-+"
    print(sep)
    print(f"| {'field':<{key_w}} | {'value':<{val_w}} |")
    print(sep)
    for k, v in rows:
        vv = v if len(v) <= val_w else v[: val_w - 1] + "…"
        print(f"| {k:<{key_w}} | {vv:<{val_w}} |")
    print(sep)
    if ok:
        print("check-chain: OK")
    else:
        print("check-chain: FAIL — chain_via=Clash:7890 (not 10808); or --skip-proxy-preflight")


def run_proxy_preflight(
    cfg: dict,
    proxy_override: Optional[str],
    *,
    require_ok: bool = True,
    full_probe: bool = True,
    table: bool = False,
) -> int:
    """Return 0 if ok/skipped; 2 if failed and require_ok.

    Logging contract:
      - one INFO summary line (always)
      - WARNING for 10808 heuristic only
      - ERROR on fail (+ one action line if require_ok)
      - table=True: also print human table to stdout (--check-chain)
    """
    from ..proxyutil import preflight_register_proxy

    impersonate = (cfg.get("browser") or {}).get("impersonate") or "chrome131"
    info = preflight_register_proxy(
        cfg,
        proxy_override,
        full_probe=full_probe,
        impersonate=impersonate,
        geo=False,
    )
    line = _fmt_proxy_preflight(info)
    if info.get("ok"):
        logging.info("%s", line)
    else:
        logging.error("%s", line)

    if info.get("warning"):
        logutil.warning("proxy-preflight", warn=info["warning"])

    if table:
        _print_check_chain_table(info)

    if not info.get("ok"):
        if require_ok:
            if not table:
                logutil.error(
                    "proxy-preflight",
                    refuse=1,
                    hint="chain_via=Clash:7890 not v2rayN:10808; or --skip-proxy-preflight",
                )
            return 2
        return 1
    return 0


_run_proxy_preflight = run_proxy_preflight  # compat private alias


def _cmd_check_chain(cfg: dict, proxy_override: Optional[str]) -> int:
    """Standalone nested CONNECT + full-chain exit probe (table output)."""
    return run_proxy_preflight(
        cfg, proxy_override, require_ok=True, full_probe=True, table=True
    )


def _cmd_check_proxy(cfg: dict, proxy_override: Optional[str], times: int = 1) -> int:
    from ..proxyutil import probe_proxy, resolve_proxy

    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if dyn.get("enabled"):
        logging.info(
            "动态代理: region=%s rotate_sid=%s chain_via=%s",
            dyn.get("region"),
            dyn.get("rotate_sid"),
            dyn.get("chain_via") or "(无)",
        )
    else:
        logging.info("固定/池代理模式 default=%s", (cfg.get("proxy") or {}).get("default"))

    ok_n = 0
    ips: list[str] = []
    impersonate = (cfg.get("browser") or {}).get("impersonate") or "chrome131"
    for i in range(times):
        resolved = resolve_proxy(cfg, proxy_override)
        try:
            logging.info(
                "[%s/%s] proxy=%s session=%s region=%s sid=%s",
                i + 1,
                times,
                resolved.label(),
                resolved.session_url or "直连",
                resolved.region or "-",
                resolved.sid or "-",
            )
            info = probe_proxy(resolved.session_url, impersonate=impersonate)
            ip = info.get("ip") or ""
            ips.append(ip)
            ipinfo = info.get("ipinfo") or {}
            logging.info(
                "  status=%s ip=%s country=%s city=%s org=%s",
                info.get("status"),
                ip or "?",
                ipinfo.get("country") or "-",
                ipinfo.get("city") or "-",
                ipinfo.get("org") or "-",
            )
            if info.get("status") == 200 and ip:
                ok_n += 1
        except Exception as exc:
            logging.error("  probe 失败: %s", exc)
        finally:
            resolved.close()

    unique = sorted({x for x in ips if x})
    logging.info("汇总: ok=%s/%s unique_ip=%s %s", ok_n, times, len(unique), unique)
    return 0 if ok_n == times else 1


def _cmd_half_chain(cfg: dict, proxy_override: Optional[str], args: argparse.Namespace) -> int:
    from ..backends.mail.codes import normalize_xai_code
    from ..client import GrokAuthClient
    from ..proxyutil import resolve_proxy

    resolved = resolve_proxy(cfg, proxy_override)
    client: GrokAuthClient | None = None
    try:
        logging.info("使用代理: %s session=%s", resolved.label(), resolved.session_url or "直连")
        client = GrokAuthClient(cfg, session_url=resolved.session_url, debug=args.verbose)

        if args.scrape or args.create_code or args.verify_code:
            try:
                info = client.load_signup_page()
                logging.info(
                    "scrape ok next-action=%s sitekey=%s",
                    (info.get("next_action") or "")[:24],
                    info.get("turnstile_sitekey"),
                )
            except Exception as exc:
                logging.error("scrape 失败: %s", exc)
                if args.scrape and not (args.create_code or args.verify_code):
                    return 1
                logging.warning("继续尝试 gRPC（可能失败）")

        if args.create_code:
            res = client.create_email_validation_code(args.create_code.strip())
            if not res.ok:
                logging.error("Create 失败: %s trailers=%s", res.error, res.trailers)
                return 1
            logging.info("Create 成功，请查收邮箱验证码")

        if args.verify_code:
            email, code = args.verify_code
            res = client.verify_email_validation_code(
                email.strip(), normalize_xai_code(code.strip())
            )
            if not res.ok:
                logging.error("Verify 失败: %s trailers=%s", res.error, res.trailers)
                return 1
            logging.info("Verify 成功")

        return 0
    finally:
        if client is not None:
            client.close()
        resolved.close()


def _cmd_env_check(cfg: dict) -> int:
    """Show whether .env / critical env vars are present (no secret values)."""
    from ..config import ROOT, ensure_dotenv

    ensure_dotenv()
    env_path = ROOT / ".env"
    print(f".env path: {env_path}")
    print(f".env exists: {env_path.is_file()}")
    print(f"config: {cfg.get('_config_path')}")

    def _mask(name: str) -> str:
        v = os.environ.get(name) or ""
        if not v:
            return "MISSING"
        if name.endswith("_KEY") or "TOKEN" in name or "PASSWORD" in name:
            return f"set len={len(v)}"
        if "PROXY" in name or name.endswith("_URL") or name.endswith("_FILE"):
            return v
        return f"set len={len(v)}"

    keys = [
        "TWOCAPTCHA_API_KEY",
        "YESCAPTCHA_API_KEY",
        "CAPSOLVER_API_KEY",
        "CAPTCHA_BACKEND",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "AUTH_FILE",
        "MAIL_API_URL",
        "GROK_MAIL_LINE",
        "CPA_SECRET_KEY",
        "CPA_BASE_URL",
    ]
    print("-" * 48)
    for k in keys:
        print(f"{k:<22} {_mask(k)}")
    cpa = cfg.get("cpa") or {}
    if isinstance(cpa, dict):
        print("-" * 48)
        # env non-empty overlays yaml in memory only (never rewrites config.yaml)
        env_base = (os.environ.get("CPA_BASE_URL") or "").strip()
        env_sk = (os.environ.get("CPA_SECRET_KEY") or "").strip()
        env_dir = (os.environ.get("CPA_AUTH_DIR") or "").strip()
        base_src = "env" if env_base else "config.yaml"
        sk_src = "env" if env_sk else "config.yaml"
        dir_src = "env" if env_dir else "config.yaml"
        print(f"{'cpa.base_url':<22} {cpa.get('base_url') or ''}  [{base_src}]")
        sk = str(cpa.get("secret_key") or "")
        print(
            f"{'cpa.secret_key':<22} "
            f"{'set len=' + str(len(sk)) if sk else 'MISSING'}  [{sk_src}]"
        )
        print(f"{'cpa.auth_dir':<22} {cpa.get('auth_dir') or 'cpa_export'}  [{dir_src}]")
        print(
            "cpa.precedence: env non-empty → overlay in memory; "
            "env empty → keep config.yaml (file not modified)"
        )
    tw = bool(os.environ.get("TWOCAPTCHA_API_KEY") or os.environ.get("TWO_CAPTCHA_API_KEY"))
    yes = bool(
        os.environ.get("YESCAPTCHA_API_KEY")
        or os.environ.get("YESCAPTCHA_KEY")
        or os.environ.get("GROK2API_YESCAPTCHA_KEY")
    )
    cap = bool(
        os.environ.get("CAPSOLVER_API_KEY")
        or os.environ.get("CAP_SOLVER_API_KEY")
        or os.environ.get("CAPSOLVER_KEY")
    )
    captcha_cfg = (cfg.get("captcha") or {}) if isinstance(cfg.get("captcha"), dict) else {}
    backend_pin = (
        (os.environ.get("CAPTCHA_BACKEND") or "").strip()
        or str(captcha_cfg.get("backend") or "").strip()
        or "auto"
    ).lower()
    captcha_ready = tw or yes or cap
    px = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"))
    cpa_ready = bool(isinstance(cpa, dict) and cpa.get("secret_key") and cpa.get("base_url"))
    print("-" * 48)
    print(f"{'captcha.backend':<22} {backend_pin}")
    # Live balance probe (no secrets) — honor pinned backend when set
    bal_ok = captcha_ready
    if captcha_ready:
        try:
            from ..backends.captcha.balance import check_captcha_balances, format_balance_report

            bal = check_captcha_balances(backend=backend_pin or "auto", cfg=cfg)
            print(format_balance_report(bal))
            yes_b = (bal.get("yes") or {}).get("balance")
            tw_b = (bal.get("twocaptcha") or {}).get("balance")
            cap_b = (bal.get("capsolver") or {}).get("balance")
            if yes and yes_b is not None:
                print(f"{'YesCaptcha.balance':<22} {yes_b}")
            if cap and cap_b is not None:
                print(f"{'CapSolver.balance':<22} {cap_b}")
            if tw and tw_b is not None:
                print(f"{'2Captcha.balance':<22} {tw_b}")
            bal_ok = bool(bal.get("ok"))
            if not bal_ok:
                print(f"captcha_balance: FAIL — {bal.get('error')}")
        except Exception as e:  # noqa: BLE001
            print(f"captcha_balance: probe error {type(e).__name__}: {e}")
            bal_ok = False
    if captcha_ready and bal_ok:
        which = []
        if yes:
            which.append("YesCaptcha")
        if cap:
            which.append("CapSolver")
        if tw:
            which.append("2Captcha")
        print(
            f"ready_for_register: yes (keys={'+'.join(which) or '-'}; "
            f"backend={backend_pin}; balance ok)"
        )
    elif captcha_ready and not bal_ok:
        print("ready_for_register: no (key set but captcha balance preflight failed)")
    else:
        print(
            "ready_for_register: no "
            "(need CAPSOLVER_API_KEY or YESCAPTCHA_API_KEY or TWOCAPTCHA_API_KEY)"
        )
    # local hop (register) — env overlay
    px_cfg = cfg.get("proxy") or {}
    dyn = (px_cfg.get("dynamic") or {}) if isinstance(px_cfg, dict) else {}
    env_local = (os.environ.get("LOCAL_PROXY") or "").strip()
    env_def = (os.environ.get("PROXY_DEFAULT") or "").strip()
    env_via = (os.environ.get("PROXY_CHAIN_VIA") or "").strip()
    def_src = "env" if (env_local or env_def) else ("config.yaml" if (px_cfg.get("default") or "").strip() else "code-default")
    via_src = "env" if (env_local or env_via) else ("config.yaml" if (dyn.get("chain_via") or "").strip() else "code-default")
    print("-" * 48)
    print(f"{'proxy.default':<22} {px_cfg.get('default') or '(code 7890)'}  [{def_src}]")
    print(f"{'proxy.chain_via':<22} {dyn.get('chain_via') or '(code 7890)'}  [{via_src}]")
    env_tmpl = (os.environ.get("PROXY_DYNAMIC_TEMPLATE") or "").strip()
    tmpl = str(dyn.get("template") or "")
    tmpl_src = "env" if env_tmpl else ("config.yaml" if tmpl.strip() else "none")
    if tmpl.strip():
        # never print user:pass — host only
        try:
            from urllib.parse import urlparse

            u = urlparse(tmpl if "://" in tmpl else "http://" + tmpl)
            host = u.hostname or "?"
            port = f":{u.port}" if u.port else ""
            tmpl_show = f"{u.scheme or 'http'}://***@{host}{port}"
        except Exception:
            tmpl_show = f"set len={len(tmpl)}"
    else:
        tmpl_show = "(empty)"
    env_en = (os.environ.get("PROXY_DYNAMIC_ENABLED") or "").strip()
    en_src = "env" if env_en else "config/code"
    print(f"{'proxy.dynamic.enabled':<22} {dyn.get('enabled')}  [{en_src}]")
    print(f"{'proxy.dynamic.template':<22} {tmpl_show}  [{tmpl_src}]")
    print(f"{'proxy.dynamic.region':<22} {dyn.get('region') or '-'}")
    print(
        "proxy.precedence: LOCAL_PROXY|PROXY_* / PROXY_DYNAMIC_* non-empty → overlay; "
        "empty → config.yaml; hop1 blank → 7890"
    )
    # mint/probe/register share LOCAL_PROXY (MINT_PROXY cancelled)
    from .mint_proxy import resolve_mint_proxy

    mint_px = resolve_mint_proxy(None, cfg=cfg)
    if (os.environ.get("LOCAL_PROXY") or "").strip():
        mint_src = "env:LOCAL_PROXY"
    elif (os.environ.get("PROXY_DEFAULT") or "").strip():
        mint_src = "env:PROXY_DEFAULT"
    elif (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip():
        mint_src = "env:HTTPS_PROXY"
    elif (os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip():
        mint_src = "env:HTTP_PROXY"
    elif (px_cfg.get("default") or "").strip():
        mint_src = "config:proxy.default"
    else:
        mint_src = "code-default-7890"
    print(f"{'mint.proxy':<22} {mint_px}  [{mint_src}]")
    hop1_show = (px_cfg.get("default") or dyn.get("chain_via") or "http://127.0.0.1:7890")
    print(f"ready_for_mint:     yes (outbound={mint_px}; hop1={hop1_show}; via LOCAL_PROXY)")
    print(f"ready_for_cpa_upload: {'yes' if cpa_ready else 'no (set CPA_BASE_URL + CPA_SECRET_KEY)'}")
    return 0 if (captcha_ready and bal_ok) else 1
