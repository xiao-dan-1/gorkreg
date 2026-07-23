#!/usr/bin/env python3
"""Recover sso_failed accounts via CreateSession (password + Turnstile).

Does NOT re-register. Uses email/password from account JSON + fresh Cap/Yes solve.

Usage (project root):
  python scripts/recover_sso_failed.py ei7re9fb2u@bj01.xdauv.xyz
  python scripts/recover_sso_failed.py --from-fails
  python scripts/recover_sso_failed.py --from-output -j 1
  python scripts/recover_sso_failed.py --from-fails --dry-run
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

from grokreg.backends.captcha import get_captcha_backend
from grokreg.config import normalize_captcha_backend
from grokreg.client import GrokAuthClient
from grokreg.config import ensure_dotenv, load_config
from grokreg.ops.ledger_ops import iter_account_evidence_paths
from grokreg.proxyutil import resolve_proxy

ensure_dotenv()
ROOT = _ROOT
OUT = ROOT / "output" / "sso_rescue"
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sso_recover")
_cli_lock = threading.Lock()


def _load_latest_account(email: str) -> dict | None:
    safe = email.lower().replace("@", "_at_")
    files = [
        p
        for p in iter_account_evidence_paths(ROOT)
        if f"account_{safe}_" in p.name.lower()
    ]
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _emails_from_output() -> list[str]:
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
        if "sso_failed" in err or code == "sso_failed":
            picked.append(em)
    return picked


def _emails_from_fails() -> list[str]:
    d = ROOT / "output" / "prod_cloudmail"
    if not d.is_dir():
        return []
    emails: list[str] = []
    for p in sorted(d.glob("batch_*_fails.json"), key=lambda x: x.stat().st_mtime):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in data.get("emails") or []:
            if (row.get("bucket") or "") != "sso_failed":
                continue
            em = (row.get("email") or "").strip().lower()
            if em and em not in emails:
                emails.append(em)
    return emails


def _append_roster(email: str, password: str, sso: str) -> bool:
    cli = ROOT / "sso_roster.txt"
    with _cli_lock:
        existing = (
            cli.read_text(encoding="utf-8", errors="ignore").lower()
            if cli.exists()
            else ""
        )
        if email.lower() in existing:
            # update line if present without sso? skip for KISS — already has email
            for line in existing.splitlines():
                if line.startswith(email.lower() + "----") and "----" in line:
                    parts = line.split("----")
                    if len(parts) >= 3 and parts[2].strip():
                        return False
            # append anyway if no good sso line
        with cli.open("a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{sso}\n")
        return True


def _patch_account_json(email: str, sso: str) -> str | None:
    safe = email.lower().replace("@", "_at_")
    files = [
        p
        for p in iter_account_evidence_paths(ROOT)
        if f"account_{safe}_" in p.name.lower()
    ]
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime)
    path = files[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sso"] = sso
        data["error"] = None
        data["error_code"] = None
        data["sso_via"] = "create_session_recover"
        data["recovered_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception as exc:
        log.warning("patch account json: %s", exc)
        return None


def _recover_one(cfg: dict, email: str, *, dry_run: bool) -> dict:
    prev = _load_latest_account(email) or {}
    password = (prev.get("password") or "").strip()
    row = {
        "email": email,
        "had_password": bool(password),
        "prev_error": prev.get("error"),
        "ok": False,
        "sso": False,
        "cli_appended": False,
        "error": None,
        "wall_s": 0.0,
    }
    if not password:
        row["error"] = "no_password_in_account_json"
        return row
    if dry_run:
        row["error"] = "dry_run"
        row["ok"] = True
        return row

    t0 = time.time()
    try:
        resolved = resolve_proxy(cfg, None)
        session_url = getattr(resolved, "session_url", None) or ""
        client = GrokAuthClient(cfg, session_url=session_url, debug=False)
        # need sitekey + page warm for turnstile
        info = client.load_signup_page(use_cache=True, cache_ttl=600.0)
        sitekey = (info or {}).get("turnstile_sitekey") or client.turnstile_sitekey
        backend = normalize_captcha_backend(
            str((cfg.get("captcha") or {}).get("backend") or "capsolver")
        )
        captcha = get_captcha_backend(backend, cfg)
        ts_token = captcha.solve_turnstile(client.signup_url, sitekey)
        sso = client.obtain_session_via_password(
            email=email,
            password=password,
            turnstile_token=ts_token,
            retries=2,
        )
        if not sso:
            # also try full fetch path once
            sso = client.fetch_sso_token(
                retries=1,
                email=email,
                password=password,
                turnstile_token=ts_token,
            )
        client.close()
        if sso:
            row["ok"] = True
            row["sso"] = True
            row["cli_appended"] = _append_roster(email, password, sso)
            row["account_patched"] = _patch_account_json(email, sso)
            log.info("RECOVERED %s roster=%s", email, row["cli_appended"])
        else:
            row["error"] = "create_session_no_sso"
            log.warning("FAIL recover %s", email)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}:{exc}"
        log.exception("recover exception %s", email)
    row["wall_s"] = round(time.time() - t0, 3)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Recover sso_failed via CreateSession")
    ap.add_argument("emails", nargs="*", help="emails to recover")
    ap.add_argument(
        "--from-output",
        action="store_true",
        help="scan account_*.json for sso_failed without sso",
    )
    ap.add_argument(
        "--from-fails",
        action="store_true",
        help="scan output/prod_cloudmail/batch_*_fails.json",
    )
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="max emails (0=all)")
    args = ap.parse_args()

    emails: list[str] = []
    for e in args.emails or []:
        e = (e or "").strip().lower()
        if e and e not in emails:
            emails.append(e)
    if args.from_fails:
        for e in _emails_from_fails():
            if e not in emails:
                emails.append(e)
    if args.from_output:
        for e in _emails_from_output():
            if e not in emails:
                emails.append(e)
    if args.limit and args.limit > 0:
        emails = emails[: int(args.limit)]
    if not emails:
        log.error("no targets: pass emails / --from-fails / --from-output")
        return 2

    cfg = load_config()
    log.info("recover targets=%s dry=%s j=%s", len(emails), args.dry_run, args.jobs)
    rows: list[dict] = []
    jobs = max(1, int(args.jobs or 1))
    if jobs == 1:
        for em in emails:
            rows.append(_recover_one(cfg, em, dry_run=bool(args.dry_run)))
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {
                ex.submit(_recover_one, cfg, em, dry_run=bool(args.dry_run)): em
                for em in emails
            }
            by: dict[str, dict] = {}
            for fut in as_completed(futs):
                em = futs[fut]
                try:
                    by[em] = fut.result()
                except Exception as exc:
                    by[em] = {"email": em, "ok": False, "error": str(exc)}
            rows = [by[em] for em in emails if em in by]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ok_n = sum(1 for r in rows if r.get("ok") and (r.get("sso") or r.get("error") == "dry_run"))
    out = {
        "ts_utc": ts,
        "targets": emails,
        "ok": ok_n,
        "n": len(rows),
        "dry_run": bool(args.dry_run),
        "rows": rows,
    }
    path = OUT / f"recover_{ts}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"recovered={ok_n}/{len(rows)}  → {path}")
    for r in rows:
        print(
            f"  {r.get('email')} ok={r.get('ok')} sso={r.get('sso')} "
            f"err={r.get('error')} wall={r.get('wall_s')}"
        )
    return 0 if ok_n == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
