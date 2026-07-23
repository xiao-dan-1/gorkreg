"""Local mail skip marks — dead/hold mailboxes that must not re-enter batch.

Store: data/mail_marks.json (under project data/, already gitignored).
Batch loads marks and skips those emails. Permanent mail_auth failures can
auto-mark so the next run never burns another 120s wait.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .config import ROOT

log = logging.getLogger(__name__)

DEFAULT_MARKS_PATH = ROOT / "data" / "mail_marks.json"
_VERSION = 1

# statuses that batch will skip
SKIP_STATUSES = frozenset({"dead", "hold", "skip", "block"})


def marks_path(root: Path | None = None) -> Path:
    if root is None:
        return DEFAULT_MARKS_PATH
    return Path(root) / "data" / "mail_marks.json"


def _empty() -> dict[str, Any]:
    return {"version": _VERSION, "marks": {}}


def load_marks(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return {email_lower: mark_dict}."""
    p = path or DEFAULT_MARKS_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("mail_marks load failed %s: %s", p, e)
        return {}
    marks = data.get("marks") if isinstance(data, dict) else None
    if not isinstance(marks, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in marks.items():
        em = str(k or "").strip().lower()
        if not em or "@" not in em:
            continue
        if isinstance(v, dict):
            out[em] = v
        elif isinstance(v, str):
            out[em] = {"status": "dead", "reason": v}
        else:
            out[em] = {"status": "dead", "reason": str(v)}
    return out


def save_marks(marks: dict[str, dict[str, Any]], path: Path | None = None) -> Path:
    p = path or DEFAULT_MARKS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    # stable key order
    ordered = {k: marks[k] for k in sorted(marks)}
    payload = {"version": _VERSION, "marks": ordered}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def get_mark(email: str, path: Path | None = None) -> dict[str, Any] | None:
    return load_marks(path).get((email or "").strip().lower())


def is_skipped(email: str, path: Path | None = None) -> bool:
    m = get_mark(email, path)
    if not m:
        return False
    st = str(m.get("status") or "dead").strip().lower()
    return st in SKIP_STATUSES


def mark_email(
    email: str,
    *,
    reason: str = "",
    code: str = "",
    status: str = "dead",
    path: Path | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Upsert a mark. Returns the stored mark."""
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        raise ValueError(f"invalid email for mark: {email!r}")
    marks = load_marks(path)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prev = marks.get(em) or {}
    entry = {
        "status": (status or "dead").strip().lower() or "dead",
        "reason": (reason or prev.get("reason") or "").strip(),
        "code": (code or prev.get("code") or "").strip(),
        "source": source,
        "updated_at": now,
    }
    if prev.get("created_at"):
        entry["created_at"] = prev["created_at"]
    else:
        entry["created_at"] = now
    marks[em] = entry
    save_marks(marks, path)
    log.info("mail_mark %s status=%s code=%s reason=%s", em, entry["status"], entry["code"], entry["reason"][:120])
    return entry


def unmark_email(email: str, path: Path | None = None) -> bool:
    em = (email or "").strip().lower()
    marks = load_marks(path)
    if em not in marks:
        return False
    del marks[em]
    save_marks(marks, path)
    log.info("mail_unmark %s", em)
    return True


def skipped_set(path: Path | None = None) -> set[str]:
    return {em for em, m in load_marks(path).items() if str(m.get("status") or "dead").lower() in SKIP_STATUSES}


def should_auto_mark(error_code: str | None, error: str | None = None, retryable: bool | None = None) -> bool:
    """Permanent mailbox failures worth never retrying in batch.

    mail_timeout is intentionally excluded — first timeout is observational;
    use apply_timeout_strike() so only consecutive timeouts become hold/dead.
    """
    code = (error_code or "").strip().lower()
    # never treat soft wait-timeout as permanent here
    if is_mail_timeout(error_code, error):
        return False
    if code in {"mail_auth", "mail_token"}:
        return True
    if retryable is False and code.startswith("mail") and code not in {"mail_timeout", "mail"}:
        return True
    low = f"{error_code or ''} {error or ''}".lower()
    markers = (
        "aadsts70000",
        "invalid_grant",
        "service abuse",
        "refresh token has been revoked",
        "mailbox_disabled",
    )
    return any(m in low for m in markers)


def is_mail_timeout(error_code: str | None, error: str | None = None) -> bool:
    """True for OTP wait timeouts (not mail_auth)."""
    code = (error_code or "").strip().lower()
    if "mail_timeout" in code:
        return True
    low = f"{error_code or ''} {error or ''}".lower()
    if "mail_timeout" in low:
        return True
    # common phrasing from Graph/IMAP waiters
    if "timeout" in low and ("验证码" in (error or "") or "otp" in low or "mail" in low):
        # exclude auth timeouts that look permanent
        if any(x in low for x in ("aadsts", "invalid_grant", "mail_auth", "refresh token")):
            return False
        return "mail" in code or "mail" in low or "验证码" in (error or "")
    return False


def apply_timeout_strike(
    email: str,
    *,
    reason: str = "",
    path: Path | None = None,
    source: str = "auto_batch",
    hold_after: int = 2,
    dead_after: int = 3,
) -> dict[str, Any]:
    """Count consecutive mail_timeout failures.

    strikes=1 → status=watch (still eligible for batch; not in SKIP_STATUSES)
    strikes>=hold_after (default 2) → hold (batch skips)
    strikes>=dead_after (default 3) → dead

    Successful registration should clear via clear_timeout_strikes().
    """
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        raise ValueError(f"invalid email for timeout strike: {email!r}")
    hold_after = max(1, int(hold_after or 2))
    dead_after = max(hold_after, int(dead_after or 3))

    marks = load_marks(path)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prev = marks.get(em) or {}
    # if already permanent dead for other reasons, keep dead but still bump counter
    prev_status = str(prev.get("status") or "").strip().lower()
    strikes = int(prev.get("timeout_strikes") or 0) + 1

    if strikes >= dead_after:
        status = "dead"
    elif strikes >= hold_after:
        status = "hold"
    else:
        status = "watch"

    # never downgrade an existing dead mark
    if prev_status == "dead":
        status = "dead"

    reason_s = (reason or prev.get("reason") or f"mail_timeout x{strikes}").strip()
    if f"x{strikes}" not in reason_s and "mail_timeout" in reason_s.lower():
        reason_s = f"{reason_s} (strike {strikes})"
    elif "mail_timeout" not in reason_s.lower():
        reason_s = f"mail_timeout x{strikes}: {reason_s}".strip(": ")

    entry = {
        "status": status,
        "reason": reason_s[:240],
        "code": "mail_timeout",
        "source": source,
        "timeout_strikes": strikes,
        "updated_at": now,
        "created_at": prev.get("created_at") or now,
    }
    marks[em] = entry
    save_marks(marks, path)
    log.info(
        "mail_timeout_strike %s strikes=%s status=%s",
        em,
        strikes,
        status,
    )
    return entry


def clear_timeout_strikes(email: str, path: Path | None = None) -> bool:
    """On successful register: drop watch/hold that only tracked mail_timeout."""
    em = (email or "").strip().lower()
    if not em:
        return False
    marks = load_marks(path)
    m = marks.get(em)
    if not m:
        return False
    code = str(m.get("code") or "").lower()
    st = str(m.get("status") or "").lower()
    # only auto-clear soft timeout tracking, never wipe mail_auth dead
    if code != "mail_timeout" and "mail_timeout" not in str(m.get("reason") or "").lower():
        return False
    if st == "dead" and int(m.get("timeout_strikes") or 0) == 0:
        return False
    if st in {"watch", "hold"} or (st == "dead" and code == "mail_timeout"):
        # successful account: remove mark entirely so it won't be skipped as "dead mail"
        # (success means mailbox worked; dead-from-timeout was a false permanent)
        if st in {"watch", "hold"}:
            del marks[em]
            save_marks(marks, path)
            log.info("mail_timeout_strike_clear %s", em)
            return True
    return False


def note_batch_result(
    email: str,
    *,
    ok: bool,
    error_code: str | None = None,
    error: str | None = None,
    retryable: bool | None = None,
    path: Path | None = None,
    source: str = "auto_batch",
) -> dict[str, Any] | None:
    """Apply mark policy after one register attempt. Returns mark entry if changed."""
    em = (email or "").strip()
    if not em or "@" not in em:
        return None
    if ok:
        clear_timeout_strikes(em, path=path)
        return None
    if should_auto_mark(error_code, error, retryable):
        return mark_email(
            em,
            reason=str(error or error_code or "permanent mail failure")[:200],
            code=str(error_code or "mail"),
            status="dead",
            path=path,
            source=source,
        )
    if is_mail_timeout(error_code, error):
        return apply_timeout_strike(
            em,
            reason=str(error or error_code or "mail_timeout")[:200],
            path=path,
            source=source,
        )
    return None
