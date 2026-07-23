"""Token / account expiry helpers — single place for ledger exp semantics.

All credential lifecycle code should use these instead of ad-hoc field picks.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from .timeutil import parse_to_epoch


def b64_jwt_claims(token: str) -> dict[str, Any]:
    if not token or "." not in token:
        return {}
    try:
        part = token.split(".")[1]
        pad = "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part + pad))
    except Exception:
        return {}


def access_token_of(entry: dict[str, Any]) -> str:
    return str(entry.get("access_token") or entry.get("key") or "").strip()


def refresh_token_of(entry: dict[str, Any]) -> str:
    return str(entry.get("refresh_token") or "").strip()


def parse_entry_exp(entry: dict[str, Any]) -> float:
    """Unix exp for a ledger/CPA-like dict.

    Priority: expires_at → expired/expires → JWT exp on access token.
    """
    exp = entry.get("expires_at")
    ts = parse_to_epoch(exp)
    if ts is not None and ts > 0:
        return ts
    for key in ("expired", "expires"):
        ts = parse_to_epoch(entry.get(key))
        if ts is not None and ts > 0:
            return ts
    claims = b64_jwt_claims(access_token_of(entry))
    if claims.get("exp"):
        try:
            return float(claims["exp"])
        except (TypeError, ValueError):
            pass
    return 0.0


def apply_token_expiry(
    entry: dict[str, Any],
    *,
    exp_unix: float | None = None,
    expires_in: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Mutate a copy with consistent expires_at + expired ISO + expires_in."""
    out = dict(entry)
    now = time.time() if now is None else float(now)
    if exp_unix is None and expires_in is not None:
        exp_unix = now + int(expires_in)
    if exp_unix is None:
        return out
    exp_unix = float(exp_unix)
    out["expires_at"] = exp_unix
    out["expired"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(exp_unix)))
    if expires_in is not None:
        out["expires_in"] = int(expires_in)
    else:
        out["expires_in"] = max(0, int(exp_unix) - int(now))
    return out


def seconds_left(entry: dict[str, Any], *, now: float | None = None, skew_sec: float = 0) -> float:
    now = time.time() if now is None else float(now)
    exp = parse_entry_exp(entry)
    if exp <= 0:
        return 0.0
    return max(0.0, exp - now - float(skew_sec))
