"""Read-only CPA file health (export-layer; not OAuth lifecycle)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...token_clock import b64_jwt_claims, parse_entry_exp

DEFAULT_REFRESH_SKEW_SEC = 300  # 5 min


def _parse_exp_unix(data: dict[str, Any]) -> float:
    return parse_entry_exp(data)


def inspect_cpa_file(
    cpa_path: Path,
    *,
    skew_sec: float = DEFAULT_REFRESH_SKEW_SEC,
    now: float | None = None,
) -> dict[str, Any]:
    """Read-only health of one CPA json. Never prints/returns raw tokens."""
    now = time.time() if now is None else now
    path = Path(cpa_path)
    row: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "email": "",
        "ok_file": False,
        "has_at": False,
        "has_rt": False,
        "disabled": False,
        "exp": 0.0,
        "left_sec": None,
        "left_h": None,
        "state": "missing",
        "needs_refresh": False,
        "error": None,
        "last_refresh": "",
        "expires_in": None,
    }
    if not path.is_file():
        row["error"] = "file not found"
        return row
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        row["state"] = "broken"
        row["error"] = str(e)
        return row
    if not isinstance(data, dict):
        row["state"] = "broken"
        row["error"] = "not a json object"
        return row

    row["ok_file"] = True
    row["email"] = (data.get("email") or path.stem.replace("xai-", "", 1) or "").strip()
    row["has_at"] = bool(data.get("access_token") or data.get("key"))
    row["has_rt"] = bool(data.get("refresh_token"))
    row["disabled"] = bool(data.get("disabled"))
    row["last_refresh"] = str(data.get("last_refresh") or data.get("refreshed_at") or "")
    row["expires_in"] = data.get("expires_in")

    if row["disabled"]:
        row["state"] = "disabled"
        row["needs_refresh"] = False
        return row
    if not row["has_at"]:
        row["state"] = "no_at"
        row["needs_refresh"] = bool(row["has_rt"])
        row["error"] = "no access_token"
        return row

    exp = _parse_exp_unix(data)
    row["exp"] = exp
    if exp > 0:
        left = exp - now
        row["left_sec"] = round(left, 1)
        row["left_h"] = round(left / 3600, 2)
    else:
        row["state"] = "needs_refresh" if row["has_rt"] else "no_at"
        row["needs_refresh"] = bool(row["has_rt"])
        row["error"] = "no exp claim"
        return row

    left = exp - now
    if left <= 0:
        row["state"] = "expired"
        row["needs_refresh"] = bool(row["has_rt"])
        if not row["has_rt"]:
            row["error"] = "expired and no refresh_token"
    elif left <= skew_sec:
        row["state"] = "needs_refresh"
        row["needs_refresh"] = bool(row["has_rt"])
        if not row["has_rt"]:
            row["error"] = "near-expiry and no refresh_token"
    else:
        row["state"] = "fresh"
        row["needs_refresh"] = False
        if not row["has_rt"]:
            row["state"] = "no_rt"
    return row

def scan_cpa_dir(
    auth_dir: Path | str,
    *,
    only: str | None = None,
    skew_sec: float = DEFAULT_REFRESH_SKEW_SEC,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Inspect all xai-*.json under auth_dir (sorted). only=email/all/None."""
    d = Path(auth_dir)
    files = sorted(d.glob("xai-*.json")) if d.is_dir() else []
    if only and str(only).strip().lower() not in {"", "all", "*"}:
        q = str(only).strip().lower()
        files = [p for p in files if q in p.name.lower() or q in p.stem.lower()]
    now = time.time() if now is None else now
    return [inspect_cpa_file(p, skew_sec=skew_sec, now=now) for p in files]

def summarize_cpa_health(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts from inspect/scan rows."""
    from collections import Counter

    c = Counter(r.get("state") or "?" for r in rows)
    return {
        "total": len(rows),
        "fresh": c.get("fresh", 0),
        "needs_refresh": sum(1 for r in rows if r.get("needs_refresh")),
        "expired": c.get("expired", 0),
        "no_rt": c.get("no_rt", 0),
        "no_at": c.get("no_at", 0),
        "disabled": c.get("disabled", 0),
        "broken": c.get("broken", 0) + c.get("missing", 0),
        "by_state": dict(c),
    }

