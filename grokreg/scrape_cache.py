"""Process-local cache of **public** signup-page metadata.

Caches ONLY (not account identity):
  - next_action (Next.js server action id)
  - router_state_tree
  - turnstile sitekey

Does NOT cache: cookies, SSO, tokens, email, password, proxy sid.

Design (conservative):
  - short TTL (default 600s)
  - batch/process shared; thread-safe
  - any create next-action style failure → invalidate
  - force refresh always allowed
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 600.0

_lock = threading.Lock()
_entry: dict[str, Any] | None = None
_stats = {"hit": 0, "miss": 0, "store": 0, "invalidate": 0}


def _valid(entry: dict[str, Any] | None, *, now: float, ttl: float) -> bool:
    if not entry:
        return False
    if float(ttl) <= 0:
        return False
    exp = float(entry.get("expires_at") or 0)
    if now >= exp:
        return False
    action = (entry.get("next_action") or "").strip()
    tree = (entry.get("router_state_tree") or "").strip()
    if not action or not tree:
        return False
    return True


def get(*, ttl: float = DEFAULT_TTL_SEC) -> dict[str, Any] | None:
    """Return a shallow copy of cached meta if still fresh, else None."""
    global _entry
    now = time.time()
    with _lock:
        if not _valid(_entry, now=now, ttl=ttl):
            if _entry is not None and now >= float((_entry or {}).get("expires_at") or 0):
                _entry = None
            _stats["miss"] += 1
            return None
        _stats["hit"] += 1
        assert _entry is not None
        return {
            "next_action": _entry["next_action"],
            "router_state_tree": _entry["router_state_tree"],
            "turnstile_sitekey": _entry.get("turnstile_sitekey") or "",
            "cached_at": _entry.get("cached_at"),
            "expires_at": _entry.get("expires_at"),
            "from_cache": True,
        }


def put(
    *,
    next_action: str,
    router_state_tree: str,
    turnstile_sitekey: str = "",
    ttl: float = DEFAULT_TTL_SEC,
) -> None:
    """Store public page meta. Ignores empty action/tree."""
    global _entry
    action = (next_action or "").strip()
    tree = (router_state_tree or "").strip()
    if not action or not tree:
        return
    now = time.time()
    ttl = max(30.0, float(ttl or DEFAULT_TTL_SEC))
    with _lock:
        _entry = {
            "next_action": action,
            "router_state_tree": tree,
            "turnstile_sitekey": (turnstile_sitekey or "").strip(),
            "cached_at": now,
            "expires_at": now + ttl,
        }
        _stats["store"] += 1
    log.info(
        "scrape-cache store action=%s… ttl=%.0fs sitekey=%s",
        action[:16],
        ttl,
        (turnstile_sitekey or "")[:20] or "-",
    )


def invalidate(reason: str = "") -> None:
    """Drop cache (frontend deploy / next_action error)."""
    global _entry
    with _lock:
        had = _entry is not None
        _entry = None
        if had:
            _stats["invalidate"] += 1
    if had:
        log.info("scrape-cache invalidate reason=%s", reason or "-")


def clear() -> None:
    invalidate("clear")


def stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def should_invalidate_error(err: str | None) -> bool:
    """True if error likely means stale next-action / page meta."""
    if not err:
        return False
    low = str(err).lower()
    keys = (
        "next_action",
        "next-action",
        "router-state",
        "router_state",
        "action_id",
        "digest",
        "failed to find",
        "unexpected action",
    )
    return any(k in low for k in keys)
