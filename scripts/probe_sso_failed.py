#!/usr/bin/env python3
"""Verify sso_failed accounts: same email re-register probe.

Evidence levels:
  email_already_in_use / already_registered → HIGH: account exists server-side
  full success + new sso → either not created before, or re-path worked
  other errors → inconclusive

Does NOT invent login API. Warmup note: scrape-only never burns accounts.

Usage (project root):
  python scripts/probe_sso_failed.py a@dom b@dom
  python scripts/probe_sso_failed.py --from-output
  python scripts/probe_sso_failed.py --from-output -j 3
  python scripts/probe_sso_failed.py          # fallback: script TARGETS list
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grokreg.config import ensure_dotenv, load_config
from grokreg.pipeline.register import RegisterOptions, register_one, result_ok
from grokreg.ops.ledger_ops import iter_account_evidence_paths, save_result

ensure_dotenv()
ROOT = _ROOT
OUT = ROOT / "output" / "sso_rescue"
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sso_rescue")

# Fallback only when no CLI emails and no --from-output hits
TARGETS = [
    # "example@bj01.xdauv.xyz",
]

_cli_lock = threading.Lock()


def _load_account(email: str) -> dict | None:
    safe = email.lower().replace("@", "_at_")
    files = [p for p in iter_account_evidence_paths(ROOT) if f"account_{safe}_" in p.name]
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _emails_from_output() -> list[str]:
    """Latest account_*.json per email where no sso and error looks sso_failed/short create."""
    out_dir = ROOT / "output"  # legacy note; scan via iter_account_evidence_paths
    if not out_dir.is_dir():
        return []
    latest: dict[str, Path] = {}
    for p in iter_account_evidence_paths(ROOT):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        em = (data.get("email") or "").strip().lower()
        if not em:
            continue
        prev = latest.get(em)
        if prev is None or p.stat().st_mtime >= prev.stat().st_mtime:
            latest[em] = p

    picked: list[str] = []
    for em, p in sorted(latest.items()):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("sso"):
            continue
        err = (data.get("error") or "").lower()
        code = (data.get("error_code") or "").lower()
        if (
            "sso_failed" in err
            or code == "sso_failed"
            or ("sso" in err and "fail" in err)
        ):
            picked.append(em)
    return picked


def _classify(result: dict) -> str:
    err = (result.get("error") or "").lower()
    if result_ok(result):
        return "RESCUED_SSO"
    for needle, label in (
        ("email_already_in_use", "EXISTS_EMAIL_IN_USE"),
        ("user_already_exists", "EXISTS_USER"),
        ("already_registered", "EXISTS_REGISTERED"),
        ("account already exists", "EXISTS_ACCOUNT"),
        ("already registered", "EXISTS_REGISTERED"),
        ("sso_failed", "CREATE_OK_SSO_FAIL_AGAIN"),
        ("create_account:", "CREATE_FAIL"),
        ("create_code:", "CREATE_CODE_FAIL"),
        ("mail_timeout", "MAIL_TIMEOUT"),
        ("proxy:", "PROXY"),
    ):
        if needle in err:
            return label
    if err:
        return f"OTHER:{err[:80]}"
    return "UNKNOWN"


def _resolve_targets(args: argparse.Namespace) -> list[str]:
    emails: list[str] = []
    for e in args.emails or []:
        e = (e or "").strip().lower()
        if e and e not in emails:
            emails.append(e)
    if args.from_output:
        for e in _emails_from_output():
            if e not in emails:
                emails.append(e)
    if not emails:
        for e in TARGETS:
            e = (e or "").strip().lower()
            if e and e not in emails:
                emails.append(e)
    return emails


def _append_cli(email: str, password: str, sso: str) -> bool:
    """Append sso_roster line if email missing. Thread-safe."""
    cli = ROOT / "sso_roster.txt"
    with _cli_lock:
        existing = (
            cli.read_text(encoding="utf-8", errors="ignore").lower()
            if cli.exists()
            else ""
        )
        if email.lower() in existing:
            return False
        with cli.open("a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{sso}\n")
        return True


def _probe_one(cfg: dict, email: str) -> dict:
    prev = _load_account(email) or {}
    password = (prev.get("password") or "").strip()
    log.info(
        "=== probe email=%s had_sso=%s prev_err=%s body=%s ===",
        email,
        bool(prev.get("sso")),
        prev.get("error"),
        prev.get("create_body_len"),
    )
    opts = RegisterOptions(
        mail_backend="cloudmail",
        captcha_backend="auto",
        password=password,
        verbose=True,
        require_captcha_config=True,
        scrape_cache=True,
        scrape_cache_ttl=600.0,
        create_short_body_retries=3,
    )
    t0 = time.time()
    try:
        result = register_one(cfg, email, None, opts)
    except Exception as exc:
        result = {
            "email": email,
            "error": f"exception:{type(exc).__name__}:{exc}",
            "sso": None,
        }
    wall = time.time() - t0
    label = _classify(result)
    try:
        path = save_result(cfg, None, result)
        saved = str(path)
    except Exception as exc:
        saved = f"save_fail:{exc}"

    cli_appended = False
    if result_ok(result):
        cli_appended = _append_cli(
            email,
            result.get("password") or password,
            result["sso"],
        )

    row = {
        "email": email,
        "prev_error": prev.get("error"),
        "prev_create_body_len": prev.get("create_body_len"),
        "prev_had_password": bool(password),
        "probe_error": result.get("error"),
        "probe_ok": bool(result_ok(result)),
        "probe_sso": bool(result.get("sso")),
        "probe_create_body_len": result.get("create_body_len"),
        "classification": label,
        "wall_s": round(wall, 3),
        "cli_appended": cli_appended,
        "saved": saved,
    }
    log.info(
        "result email=%s class=%s err=%s sso=%s body=%s",
        email,
        label,
        result.get("error"),
        bool(result.get("sso")),
        result.get("create_body_len"),
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-register probe for sso_failed / no-SSO accounts (CloudMail)",
    )
    ap.add_argument(
        "emails",
        nargs="*",
        help="emails to probe (preferred; no need to edit TARGETS)",
    )
    ap.add_argument(
        "--from-output",
        action="store_true",
        help="scan output/account_*.json for latest no-SSO + sso_failed",
    )
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help="concurrent workers (default 1=serial; e.g. 3)",
    )
    ap.add_argument(
        "--skip-proxy-preflight",
        action="store_true",
        help="skip hop1 preflight (default: require ok)",
    )
    args = ap.parse_args()

    targets = _resolve_targets(args)
    if not targets:
        log.error(
            "no targets: pass emails, or --from-output, or fill TARGETS in script"
        )
        return 2

    jobs = max(1, int(args.jobs or 1))

    cfg = load_config()
    from grokreg.ops.env_cmds import run_proxy_preflight

    if not args.skip_proxy_preflight:
        code = run_proxy_preflight(cfg, None, require_ok=True, full_probe=True)
        if code != 0:
            log.error("preflight refused")
            return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log.info("sso_rescue targets=%s jobs=%s", len(targets), jobs)

    # preserve input order in report
    by_email: dict[str, dict] = {}
    if jobs == 1:
        for email in targets:
            by_email[email] = _probe_one(cfg, email)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_probe_one, cfg, em): em for em in targets}
            for fut in as_completed(futs):
                em = futs[fut]
                try:
                    by_email[em] = fut.result()
                except Exception as exc:
                    log.exception("worker fail email=%s", em)
                    by_email[em] = {
                        "email": em,
                        "probe_error": f"worker:{type(exc).__name__}:{exc}",
                        "probe_ok": False,
                        "probe_sso": False,
                        "classification": f"OTHER:worker:{type(exc).__name__}",
                        "wall_s": 0,
                        "cli_appended": False,
                        "saved": "",
                        "prev_error": None,
                        "prev_create_body_len": None,
                        "prev_had_password": False,
                        "probe_create_body_len": None,
                    }

    rows = [by_email[em] for em in targets if em in by_email]

    out = {
        "ts_utc": ts,
        "note": "re-register same email; EXISTS_* = server already has account",
        "warmup_burns_account": False,
        "targets": targets,
        "jobs": jobs,
        "rows": rows,
    }
    out_path = OUT / f"probe_{ts}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        f"# sso_failed probe  {ts}",
        "",
        f"jobs={jobs}  targets={len(targets)}",
        "",
        "warmup 不烧号：只 GET 注册页公开参数。",
        "",
        "| email | class | prev_err | probe_err | sso | body |",
        "|-------|-------|----------|-----------|-----|------|",
    ]
    for r in rows:
        md.append(
            f"| {r['email']} | {r['classification']} | {r.get('prev_error')} | "
            f"{r.get('probe_error')} | {r.get('probe_sso')} | {r.get('probe_create_body_len')} |"
        )
    (OUT / f"probe_{ts}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print("JSON", out_path)
    rescued = sum(1 for r in rows if r.get("probe_ok"))
    log.info(
        "sso_rescue done rescued=%s total=%s jobs=%s", rescued, len(rows), jobs
    )
    return 0 if rescued == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
