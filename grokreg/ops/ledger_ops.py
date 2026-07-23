"""SSO roster + register evidence ledger helpers.

Ledger roles (do not merge):
  auth.json                  — OAuth RT/AT write model (no SSO field)
  sso_roster.txt             — email----password----sso  (mint/remint + login index)
  output/accounts/account_*.json — full register evidence (rolling, not full history)
                                 legacy: also scanned under output/ root

Public API: read_sso_roster / append_sso_roster / record_register_success /
            recover_sso_roster_from_output / migrate_sso_roster_ensure_passwords /
            account_evidence_dir / iter_account_evidence_paths / save_result
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

SSO_ROSTER_FILE = "sso_roster.txt"
# Register evidence lives under output/accounts/ (not scattered on output/ root).
ACCOUNT_EVIDENCE_SUBDIR = "accounts"

_sso_roster_lock = threading.Lock()


def parse_sso_roster_line(raw: str) -> dict[str, str] | None:
    """Parse one roster line → {email, password, sso} or None.

    Accepts:
      email----password----sso   (preferred)
      email----sso               (legacy 2-part; password empty)
    """
    s = (raw or "").strip()
    if not s or s.startswith("#"):
        return None
    parts = s.split("----")
    if len(parts) == 2:
        email, sso = parts[0].strip(), parts[1].strip()
        if email and sso:
            return {"email": email, "password": "", "sso": sso}
        return None
    if len(parts) >= 3:
        email, password, sso = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if email and sso:
            return {"email": email, "password": password, "sso": sso}
        return None
    return None


def format_sso_roster_line(email: str, password: str, sso: str) -> str:
    """Canonical on-disk line: email----password----sso."""
    return f"{email}----{password or ''}----{sso}\n"


def read_sso_roster(path: Path | str | None = None) -> list[dict[str, str]]:
    """Read SSO roster → [{email, password, sso}] in file order."""
    p = Path(path) if path is not None else Path(SSO_ROSTER_FILE)
    if not p.is_file():
        log.error("sso roster missing: %s", p)
        return []
    rows: list[dict[str, str]] = []
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        log.error("read sso roster failed: %s", exc)
        return []
    for raw in lines:
        row = parse_sso_roster_line(raw)
        if row:
            rows.append(row)
    return rows


def append_sso_roster(result: dict, *, path: Path | str | None = None) -> bool:
    """Append email----password----sso when register produced SSO. Thread-safe."""
    email = (result.get("email") or "").strip()
    sso = (result.get("sso") or "").strip()
    if not email or not sso or result.get("error"):
        return False
    password = (result.get("password") or "").strip()
    roster = Path(path) if path is not None else Path(SSO_ROSTER_FILE)
    needle = email.lower() + "----"
    line = format_sso_roster_line(email, password, sso)
    with _sso_roster_lock:
        if roster.is_file():
            try:
                text = roster.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            for row in text.splitlines():
                if row.strip().lower().startswith(needle):
                    return False
        with roster.open("a", encoding="utf-8") as f:
            f.write(line)
    log.info("sso roster append email=%s path=%s", email, roster.name)
    return True


def account_evidence_dir(cfg: dict | None = None, root: Path | str | None = None) -> Path:
    """Directory for account_*.json evidence: <root>/output/accounts."""
    if root is not None:
        base = Path(root)
    else:
        base = Path((cfg or {}).get("_root") or ".")
    return base / "output" / ACCOUNT_EVIDENCE_SUBDIR


def iter_account_evidence_paths(
    root: Path | str | None = None,
    *,
    cfg: dict | None = None,
    include_legacy_root: bool = True,
) -> list[Path]:
    """List account_*.json under output/accounts/ (+ legacy output/ root)."""
    if root is not None:
        base = Path(root)
    else:
        base = Path((cfg or {}).get("_root") or ".")
    out_root = base / "output"
    paths: list[Path] = []
    primary = out_root / ACCOUNT_EVIDENCE_SUBDIR
    if primary.is_dir():
        paths.extend(sorted(primary.glob("account_*.json")))
    if include_legacy_root and out_root.is_dir():
        # Only direct children of output/ (not nested prod_cloudmail etc.)
        for p in sorted(out_root.glob("account_*.json")):
            if p not in paths:
                paths.append(p)
    return paths


def migrate_account_evidence_to_subdir(
    root: Path | str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move output/account_*.json → output/accounts/ (dedupe by name)."""
    base = Path(root) if root is not None else Path(".")
    out_root = base / "output"
    dest = out_root / ACCOUNT_EVIDENCE_SUBDIR
    stats: dict[str, Any] = {
        "dest": str(dest),
        "dry_run": bool(dry_run),
        "moved": 0,
        "skipped_exists": 0,
        "scanned": 0,
    }
    if not out_root.is_dir():
        return stats
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for src in sorted(out_root.glob("account_*.json")):
        stats["scanned"] += 1
        target = dest / src.name
        if target.exists():
            stats["skipped_exists"] += 1
            continue
        if dry_run:
            stats["moved"] += 1
            continue
        src.replace(target)
        stats["moved"] += 1
    return stats


def _password_map_from_evidence(root: Path | str | None = None) -> dict[str, str]:
    """email.lower() → password from account evidence (prefer non-empty)."""
    out: dict[str, str] = {}
    for path in iter_account_evidence_paths(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("error"):
            continue
        em = (data.get("email") or "").strip().lower()
        pw = (data.get("password") or "").strip()
        if em and pw and em not in out:
            out[em] = pw
    return out


def migrate_sso_roster_ensure_passwords(
    path: Path | str | None = None,
    *,
    dry_run: bool = False,
    evidence_root: Path | str | None = None,
) -> dict[str, Any]:
    """Rewrite roster to email----password----sso; fill password from evidence when missing.

    Accepts legacy 2-part lines. Dedupes by email (first wins).
    """
    roster = Path(path) if path is not None else Path(SSO_ROSTER_FILE)
    stats: dict[str, Any] = {
        "path": str(roster),
        "dry_run": bool(dry_run),
        "lines_in": 0,
        "rows_out": 0,
        "filled_password": 0,
        "already_three_part": 0,
        "two_part_in": 0,
        "still_empty_password": 0,
        "skipped_bad": 0,
        "rewritten": False,
    }
    if not roster.is_file():
        return stats
    try:
        raw_lines = roster.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        stats["error"] = str(exc)
        return stats

    pw_map = _password_map_from_evidence(evidence_root)
    out_lines: list[str] = []
    seen: set[str] = set()
    for raw in raw_lines:
        stats["lines_in"] += 1
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split("----")
        if len(parts) == 2:
            stats["two_part_in"] += 1
        elif len(parts) >= 3:
            stats["already_three_part"] += 1
        row = parse_sso_roster_line(raw)
        if not row:
            stats["skipped_bad"] += 1
            continue
        em = row["email"].lower()
        if em in seen:
            continue
        seen.add(em)
        password = (row.get("password") or "").strip()
        if not password and em in pw_map:
            password = pw_map[em]
            stats["filled_password"] += 1
        if not password:
            stats["still_empty_password"] += 1
        out_lines.append(
            format_sso_roster_line(row["email"], password, row["sso"]).rstrip("\n")
        )
    stats["rows_out"] = len(out_lines)
    if dry_run:
        return stats
    with _sso_roster_lock:
        tmp = roster.with_suffix(roster.suffix + ".tmp")
        tmp.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
        tmp.replace(roster)
    stats["rewritten"] = True
    log.info(
        "sso roster ensure passwords path=%s in=%s out=%s filled=%s",
        roster,
        stats["lines_in"],
        stats["rows_out"],
        stats["filled_password"],
    )
    return stats


# Back-compat name used by older CLI strings in tests (rewrites to 3-part with pw).
migrate_sso_roster_drop_passwords = migrate_sso_roster_ensure_passwords


def resolve_auth_path(cfg: dict, args: Any | None = None) -> Path:
    """Resolve auth.json path: --auth-file > AUTH_FILE env > cfg _root/auth.json."""
    from grokreg.auth_pool import default_auth_path

    if args is not None and getattr(args, "auth_file", None):
        return Path(args.auth_file)
    env_auth = (os.environ.get("AUTH_FILE") or "").strip()
    if env_auth:
        return Path(env_auth)
    root = Path((cfg or {}).get("_root") or ".")
    return default_auth_path(root)


def existing_pool_emails(auth_path: Path | str) -> set[str]:
    """Lowercase emails already in auth.json."""
    found: set[str] = set()
    path = Path(auth_path)
    if not path.is_file():
        return found
    try:
        from grokreg.auth_pool import list_entries

        for _k, e in list_entries(path, include_disabled=True, include_expired=True):
            em = (e.get("email") or "").strip().lower()
            if em:
                found.add(em)
    except Exception as exc:  # noqa: BLE001
        log.debug("existing_pool_emails: %s", exc)
    return found


def select_mint_todos(
    accounts: list[dict[str, str]],
    *,
    target: str,
    auth_path: Path | str,
    missing_only: bool = False,
    limit: int | None = None,
    newest_first: bool = True,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Select mint candidates from SSO roster (newest-first by default)."""
    target_l = (target or "").strip().lower()
    if target_l in {"", "all", "*"}:
        todos = list(accounts)
    else:
        todos = [
            a
            for a in accounts
            if a.get("email", "").lower() == target_l
            or target_l in a.get("email", "").lower()
        ]

    stats: dict[str, Any] = {
        "candidates_in": len(todos),
        "skipped_existing": 0,
        "newest_first": bool(newest_first),
        "limit": limit,
    }

    if missing_only:
        existing = existing_pool_emails(auth_path)
        kept: list[dict[str, str]] = []
        for a in todos:
            em = (a.get("email") or "").strip().lower()
            if em in existing:
                stats["skipped_existing"] += 1
                continue
            kept.append(a)
        todos = kept

    if newest_first and len(todos) > 1:
        todos = list(reversed(todos))

    if limit is not None and int(limit) > 0:
        before = len(todos)
        todos = todos[: int(limit)]
        stats["truncated_from"] = before
        stats["truncated_to"] = len(todos)

    stats["will_mint"] = len(todos)
    return todos, stats


def recover_sso_roster_from_output(
    output_dir: Path | str = "output",
    *,
    cli_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill sso_roster from account evidence (output/accounts + legacy root).

    Same email with multiple JSON files → **newest mtime wins** (SSO + password).
    Skips rows with error / no SSO. Does not remove existing roster lines.
    """
    out = Path(output_dir)
    roster = Path(cli_path) if cli_path is not None else Path(SSO_ROSTER_FILE)
    stats: dict[str, Any] = {
        "scanned": 0,
        "appended": 0,
        "would_append": 0,
        "skipped_existing": 0,
        "skipped_no_sso": 0,
        "skipped_error": 0,
        "skipped_bad_json": 0,
        "skipped_dup_file": 0,
        "candidates_unique": 0,
        "dry_run": bool(dry_run),
    }

    # Resolve evidence paths: if given .../output, use helper; if .../accounts, glob there;
    # if bare root project, also fine via parent.
    paths: list[Path] = []
    if out.name == ACCOUNT_EVIDENCE_SUBDIR and out.is_dir():
        paths = sorted(out.glob("account_*.json"))
        # also legacy sibling root
        parent = out.parent
        if parent.is_dir():
            for pth in sorted(parent.glob("account_*.json")):
                if pth not in paths:
                    paths.append(pth)
    elif out.is_dir():
        # treat as output root or project-ish
        if (out / ACCOUNT_EVIDENCE_SUBDIR).is_dir() or list(out.glob("account_*.json")):
            if out.name == "output":
                paths = iter_account_evidence_paths(out.parent)
            else:
                paths = iter_account_evidence_paths(out)
        else:
            paths = sorted(out.rglob("account_*.json")) if out.is_dir() else []
            filtered: list[Path] = []
            for pth in paths:
                if pth.parent.name == ACCOUNT_EVIDENCE_SUBDIR or pth.parent.name == "output":
                    filtered.append(pth)
            paths = filtered or paths

    if not paths and not out.is_dir():
        log.warning("recover: output dir missing: %s", out)
        return stats

    # newest first → first valid SSO per email is freshest evidence
    try:
        paths = sorted(paths, key=lambda pth: pth.stat().st_mtime, reverse=True)
    except OSError:
        paths = list(paths)

    existing_emails: set[str] = set()
    if roster.is_file():
        for row in read_sso_roster(roster):
            em = (row.get("email") or "").strip().lower()
            if em:
                existing_emails.add(em)

    seen_in_batch: set[str] = set()  # de-dupe multi-file same email in this run

    for path in paths:
        stats["scanned"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            stats["skipped_bad_json"] += 1
            continue
        if not isinstance(data, dict):
            stats["skipped_bad_json"] += 1
            continue
        email = (data.get("email") or "").strip()
        sso = (data.get("sso") or "").strip()
        em_l = email.lower()
        if data.get("error"):
            stats["skipped_error"] += 1
            continue
        if not email or not sso:
            stats["skipped_no_sso"] += 1
            continue
        if em_l in seen_in_batch:
            stats["skipped_dup_file"] += 1
            continue
        seen_in_batch.add(em_l)
        if em_l in existing_emails:
            stats["skipped_existing"] += 1
            continue
        stats["candidates_unique"] += 1
        if dry_run:
            stats["would_append"] += 1
            existing_emails.add(em_l)
            continue
        if append_sso_roster(data, path=roster):
            stats["appended"] += 1
            existing_emails.add(em_l)
        else:
            stats["skipped_existing"] += 1

    return stats


def summary_error_bucket(err: Any) -> str:
    """Collapse multi-line / long errors into a short bucket key."""
    if not err:
        return "-"
    s = str(err).replace(chr(10), " ").strip()
    low = s.lower()
    if "sso_failed" in low or "sso failed" in low:
        return "sso_failed"
    if "zero_balance" in low or "余额" in s:
        return "captcha_zero_balance"
    if "rate_limit" in low and ("captcha" in low or "getbalance" in low or "capsolver" in low or "yescaptcha" in low):
        return "captcha_rate_limit"
    if "captcha_balance" in low or "captcha" in low or "turnstile" in low:
        return "captcha"
    if "wire type" in low or "parse_error" in low or "grpc_parse" in low:
        return "grpc_parse"
    if "mail_timeout" in low or ("timeout" in low and "mail" in low):
        return "mail_timeout"
    if "proxy" in low or "curl: (" in low or "connect tunnel" in low or "request_error" in low:
        return "proxy"
    if "email_already" in low or "already_in_use" in low:
        return "email_in_use"
    if "exception:" in low:
        head_err = s.split("exception:", 1)[-1].strip()
        head_low = head_err.lower()
        if "wire type" in head_low or "parse_error" in head_low:
            return "grpc_parse"
        if "next-action" in head_low or "js chunk" in head_low:
            return "exception:scrape_action"
        if "验证码" in head_err or "code" in head_low:
            return "exception:code_wait"
        if len(head_err) > 48 or any(ord(c) > 127 for c in head_err[:20]):
            return "exception"
        return "exception:" + head_err[:40]
    if s.startswith("mail:") or ("等待" in s and "验证码" in s):
        return "mail_timeout"
    return (s[:40] + "…") if len(s) > 40 else s


def save_result(cfg: dict, output: Optional[str], result: dict) -> Path:
    """Write register result to output/accounts/account_*.json (+ accounts.jsonl)."""
    if output:
        path = Path(output)
    else:
        out_dir = account_evidence_dir(cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        email = (result.get("email") or "unknown").lower()
        email_safe = email.replace("@", "_at_")
        existing = sorted(out_dir.glob(f"account_{email_safe}_*.json"))
        if existing:
            path = existing[-1]
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = out_dir / f"account_{email_safe}_{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # jsonl next to accounts dir (output/accounts.jsonl) or parent output/
    jsonl_parent = path.parent
    if jsonl_parent.name == ACCOUNT_EVIDENCE_SUBDIR:
        jsonl = jsonl_parent.parent / "accounts.jsonl"
    else:
        jsonl = jsonl_parent / "accounts.jsonl"
    with jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + chr(10))
    return path


def record_register_success(
    cfg: dict,
    result: dict,
    *,
    output: Optional[str] = None,
    roster_path: Path | str | None = None,
) -> dict[str, Any]:
    """Single entry for register dual-write: evidence + SSO roster."""
    path = save_result(cfg, output, result)
    roster_appended = False
    roster_err: str | None = None
    try:
        if roster_path is not None:
            roster_appended = append_sso_roster(result, path=roster_path)
        else:
            roster_appended = append_sso_roster(result)
    except Exception as exc:  # noqa: BLE001
        roster_err = str(exc)
        log.warning("record_register_success roster append failed: %s", exc)
    return {
        "path": path,
        "roster_appended": roster_appended,
        "roster_error": roster_err,
        "email": (result.get("email") or "").strip(),
        "has_sso": bool((result.get("sso") or "").strip()) and not result.get("error"),
    }


def audit_sso_ledgers(
    *,
    cli_path: Path | str | None = None,
    output_dir: Path | str = "output",
    auth_path: Path | str = "auth.json",
) -> dict[str, Any]:
    """Compare three SSO sinks without merging storage."""
    roster = Path(cli_path) if cli_path is not None else Path(SSO_ROSTER_FILE)
    roster_emails: set[str] = set()
    for row in read_sso_roster(roster):
        em = (row.get("email") or "").strip().lower()
        if em:
            roster_emails.add(em)

    output_sso: set[str] = set()
    out = Path(output_dir)
    # evidence paths relative to project if output_dir is "output"
    if out.name == "output" and not out.is_absolute():
        paths = iter_account_evidence_paths(out.parent if out.parent != Path("") else Path("."))
    elif out.is_dir() and out.name == "output":
        paths = iter_account_evidence_paths(out.parent)
    else:
        # absolute/custom: use helper with parent or self
        root = out.parent if out.name in ("output", ACCOUNT_EVIDENCE_SUBDIR) else out
        if out.name == ACCOUNT_EVIDENCE_SUBDIR:
            root = out.parent.parent if out.parent.name == "output" else out.parent
        paths = iter_account_evidence_paths(root)
        if not paths and out.is_dir():
            paths = sorted(out.glob("account_*.json")) + sorted(
                (out / ACCOUNT_EVIDENCE_SUBDIR).glob("account_*.json")
                if (out / ACCOUNT_EVIDENCE_SUBDIR).is_dir()
                else []
            )

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("error"):
            continue
        em = (data.get("email") or "").strip().lower()
        sso = (data.get("sso") or "").strip()
        if em and sso:
            output_sso.add(em)

    auth_emails = existing_pool_emails(auth_path)

    in_output_not_cli = sorted(output_sso - roster_emails)
    in_cli_not_auth = sorted(roster_emails - auth_emails)
    in_auth_not_cli = sorted(auth_emails - roster_emails)
    in_all_three = sorted(roster_emails & output_sso & auth_emails)

    return {
        "cli_emails": sorted(roster_emails),
        "output_sso_emails": sorted(output_sso),
        "auth_emails": sorted(auth_emails),
        "in_output_not_cli": in_output_not_cli,
        "in_cli_not_auth": in_cli_not_auth,
        "in_auth_not_cli": in_auth_not_cli,
        "in_all_three": in_all_three,
        "roster_file": str(roster),
        "counts": {
            "cli": len(roster_emails),
            "output_sso": len(output_sso),
            "auth": len(auth_emails),
            "in_output_not_cli": len(in_output_not_cli),
            "in_cli_not_auth": len(in_cli_not_auth),
            "in_auth_not_cli": len(in_auth_not_cli),
            "in_all_three": len(in_all_three),
        },
    }
