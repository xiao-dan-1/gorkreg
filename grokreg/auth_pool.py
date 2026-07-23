"""Multi-account OIDC/CPA credential pool (auth.json).

Inspired by Grok-Register-Tool's auth.json model:
  key = issuer::client_id::sub
  list / pick / upsert / status; import from xai pack is optional ops
  refresh_entry: RT from auth.json only (ledger-first)

Keeps per-file CPA exports for CLIProxyAPI compatibility, while giving
one place to list/select/refresh the whole pool.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .oauth import CLIENT_ID, BASE_URL
from .errors import AuthError
from .token_clock import apply_token_expiry, b64_jwt_claims, parse_entry_exp

# Back-compat name used by older call sites / tests
_parse_exp = parse_entry_exp

ISSUER = "https://auth.x.ai"
AUTH_KEY_PREFIX = f"{ISSUER}::{CLIENT_ID}"
DEFAULT_AUTH_FILE = "auth.json"
TOKEN_REFRESH_SKEW = 300  # refresh if < 5 min left

_lock = threading.RLock()
_file_lock_depth = threading.local()

# Cross-process mutual exclusion for auth.json (multi CLI windows / jobs).
# Companion lock file: <auth.json>.lock
_FILE_LOCK_TIMEOUT_SEC = 60.0


def _lock_path(path: Path) -> Path:
    return Path(path).with_suffix(Path(path).suffix + ".lock")


def _try_acquire_file_lock(lock_file: Path) -> object | None:
    """Return an open lock fd if acquired; None if busy. Caller must release."""
    import os
    import sys

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                os.close(fd)
                return None
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return None
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        return fd
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        return None


def _release_file_lock(fd: object | None) -> None:
    if fd is None:
        return
    import os
    import sys

    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)  # type: ignore[arg-type]
        except OSError:
            pass


class _AuthFileLock:
    """Thread + cross-process lock around a critical section on auth.json."""

    def __init__(self, path: Path | str, *, timeout: float = _FILE_LOCK_TIMEOUT_SEC):
        self.path = Path(path)
        self.timeout = float(timeout)
        self._fd: object | None = None

    def __enter__(self) -> "_AuthFileLock":
        _lock.acquire()
        depth = getattr(_file_lock_depth, "n", 0)
        _file_lock_depth.n = depth + 1
        if depth > 0:
            # re-entrant same thread (e.g. save_pool under upsert)
            self._fd = None
            self._nested = True
            return self
        self._nested = False
        lock_file = _lock_path(self.path)
        deadline = time.time() + self.timeout
        while True:
            fd = _try_acquire_file_lock(lock_file)
            if fd is not None:
                self._fd = fd
                return self
            if time.time() >= deadline:
                _lock.release()
                raise AuthPoolError(
                    f"timeout acquiring auth pool lock: {lock_file} "
                    f"(another process may be writing {self.path.name})"
                )
            time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if not getattr(self, "_nested", False):
                _release_file_lock(self._fd)
        finally:
            self._fd = None
            depth = getattr(_file_lock_depth, "n", 1) - 1
            _file_lock_depth.n = max(0, depth)
            _lock.release()


class AuthPoolError(AuthError):
    """Credential pool errors."""

    code = "auth_pool"


def default_auth_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root else Path.cwd()
    return base / DEFAULT_AUTH_FILE



def make_key(sub: str | None = None, email: str | None = None) -> str:
    if sub:
        return f"{AUTH_KEY_PREFIX}::{sub}"
    if email:
        return f"{AUTH_KEY_PREFIX}::email:{email.lower()}"
    raise AuthPoolError("need sub or email for auth key")


def cpa_to_entry(pack: dict[str, Any], *, cpa_path: str | Path | None = None) -> tuple[str, dict[str, Any]]:
    """Convert a CPA file payload into (pool_key, entry)."""
    email = (pack.get("email") or "").strip()
    at = pack.get("access_token") or pack.get("key") or ""
    claims = b64_jwt_claims(at)
    sub = (pack.get("sub") or claims.get("sub") or "").strip()
    exp = parse_entry_exp(pack)
    if not exp and claims.get("exp"):
        exp = float(claims["exp"])

    key = make_key(sub=sub or None, email=email or None)
    entry = {
        "email": email,
        "access_token": at,
        "refresh_token": pack.get("refresh_token") or "",
        "id_token": pack.get("id_token") or "",
        "sub": sub,
        "expires_at": exp,
        "expires_in": pack.get("expires_in"),
        "base_url": pack.get("base_url") or BASE_URL,
        "token_endpoint": pack.get("token_endpoint") or f"{ISSUER}/oauth2/token",
        "auth_kind": pack.get("auth_kind") or "oauth",
        "type": pack.get("type") or "xai",
        "disabled": bool(pack.get("disabled")),
        "headers": pack.get("headers") or {},
        "last_refresh": pack.get("last_refresh") or pack.get("refreshed_at") or "",
        "cpa_path": str(cpa_path) if cpa_path else "",
        # legacy field name kept empty for hard-cut clarity
        # readers: entry.get("cpa_path") or entry.get("cpa_path"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return key, entry


def load_pool(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise AuthPoolError(f"failed to read {path}: {e}") from e
    if not isinstance(data, dict):
        raise AuthPoolError(f"unexpected auth.json format: {path}")
    return data


def save_pool(path: Path | str, data: dict[str, Any]) -> Path:
    """Atomic write of the whole pool.

    Compact JSON (no indent) — high-j mint rewrites large auth.json often;
    pretty-print was pure I/O tax. Always acquires _AuthFileLock (re-entrant).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _AuthFileLock(path):
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    return path


def upsert(path: Path | str, entry: dict[str, Any], *, key: str | None = None) -> str:
    """Insert/update one account. Returns pool key.

    Cross-process safe: serializes read-modify-write via auth.json.lock so two
    CLI processes cannot clobber each other.
    """
    email = (entry.get("email") or "").strip()
    sub = (entry.get("sub") or "").strip()
    if not key:
        key = make_key(sub=sub or None, email=email or None)
    with _AuthFileLock(path):
        data = load_pool(path)
        # drop older keys for same email
        if email:
            for k in list(data.keys()):
                if k == key:
                    continue
                v = data.get(k)
                if isinstance(v, dict) and (v.get("email") or "").lower() == email.lower():
                    del data[k]
        entry = dict(entry)
        entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data[key] = entry
        save_pool(path, data)
    return key



def _load_json_object(raw: str, *, path: Path | str | None = None) -> dict[str, Any]:
    """Parse first JSON object; tolerate trailing Extra data (historical double-write)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data, _end = json.JSONDecoder().raw_decode(raw)
        logging.warning("json repaired via raw_decode: %s", path or "?")
    if not isinstance(data, dict):
        raise AuthPoolError(f"expected JSON object: {path or '?'}")
    return data

def upsert_from_cpa(path: Path | str, cpa_path: Path | str) -> str:
    cpa_path = Path(cpa_path)
    raw = cpa_path.read_text(encoding="utf-8")
    pack = _load_json_object(raw, path=cpa_path)
    key, entry = cpa_to_entry(pack, cpa_path=cpa_path)
    return upsert(path, entry, key=key)


def import_cpa_dir(
    auth_path: Path | str,
    cpa_dir: Path | str,
    *,
    pattern: str = "xai-*.json",
) -> dict[str, int]:
    """Import all CPA files into auth.json. Returns stats."""
    cpa_dir = Path(cpa_dir)
    ok = skip = fail = 0
    for p in sorted(cpa_dir.glob(pattern)):
        if "-protocol" in p.name or "-backup" in p.name:
            skip += 1
            continue
        try:
            upsert_from_cpa(auth_path, p)
            ok += 1
        except Exception as e:
            fail += 1
            logging.warning("import fail %s: %s", p.name, e)
    return {"ok": ok, "skip": skip, "fail": fail}


def list_entries(
    path: Path | str,
    *,
    include_disabled: bool = False,
    include_expired: bool = True,
) -> list[tuple[str, dict[str, Any]]]:
    data = load_pool(path)
    now = time.time()
    out: list[tuple[str, dict[str, Any]]] = []
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if not (entry.get("access_token") or entry.get("key") or entry.get("refresh_token")):
            continue
        if entry.get("disabled") and not include_disabled:
            continue
        exp = parse_entry_exp(entry)
        if not include_expired and exp and exp <= now:
            if not entry.get("refresh_token"):
                continue
        out.append((key, entry))
    out.sort(key=lambda kv: parse_entry_exp(kv[1]) or 0.0, reverse=True)
    return out


def status_row(
    key: str,
    entry: dict[str, Any],
    *,
    skew_sec: float | None = None,
) -> dict[str, Any]:
    """Health of one auth.json entry. Ledger is source of truth (not cpa_export).

    ``state`` = credential clock only (fresh / needs_refresh / expired / …).
    ``probe_class`` = last chat/quota probe result (e.g. quota_exhausted); never
    overwrites token-lifetime state. Optional ``entry.probe`` is written by probe.
    """
    now = time.time()
    skew = TOKEN_REFRESH_SKEW if skew_sec is None else max(0.0, float(skew_sec))
    exp = parse_entry_exp(entry)
    left = exp - now if exp else None
    has_at = bool(entry.get("access_token") or entry.get("key"))
    has_rt = bool(entry.get("refresh_token"))
    disabled = bool(entry.get("disabled"))

    probe = entry.get("probe") if isinstance(entry.get("probe"), dict) else {}
    probe_class = str(probe.get("class") or "").strip().lower() or None

    if disabled:
        state = "disabled"
        needs = False
    elif not has_at and not has_rt:
        state = "empty"
        needs = False
    elif not has_at and has_rt:
        state = "no_at"
        needs = True
    elif left is None or exp is None or exp <= 0:
        state = "needs_refresh" if has_rt else "no_at"
        needs = has_rt
    elif left <= 0:
        state = "expired"
        needs = has_rt
    elif left <= skew:
        state = "needs_refresh"
        needs = has_rt
    else:
        state = "fresh"
        needs = False

    return {
        "key": key,
        "email": entry.get("email") or "",
        "sub": (entry.get("sub") or "")[:12],
        "has_at": has_at,
        "has_rt": has_rt,
        "expires_at": exp,
        "left_h": round(left / 3600, 2) if left is not None else None,
        "left_sec": round(left, 1) if left is not None else None,
        "fresh": state == "fresh",
        "needs_refresh": needs,
        "state": state,
        "disabled": disabled,
        "probe_class": probe_class,
        "cpa_path": entry.get("cpa_path") or entry.get("cpa_path") or "",
        "last_refresh": entry.get("last_refresh") or entry.get("refreshed_at") or "",
    }


def set_probe_result(
    path: Path | str,
    email: str,
    *,
    classification: str,
    mode: str = "",
    code: str = "",
    reason: str = "",
    free_usage_tokens: Any = None,
) -> bool:
    """Merge optional ``probe`` summary into auth.json entry (no tokens touched).

    Returns True if an entry for email was updated. Safe under auth.json lock.
    """
    em = (email or "").strip()
    if not em:
        return False
    cls = (classification or "").strip().lower()
    if not cls:
        return False
    blob: dict[str, Any] = {
        "class": cls,
        "mode": (mode or "").strip().lower(),
        "at": int(time.time()),
    }
    if code:
        blob["code"] = str(code)[:200]
    if reason:
        blob["reason"] = str(reason)[:240]
    if isinstance(free_usage_tokens, dict) and free_usage_tokens:
        # keep small ints only
        clean: dict[str, Any] = {}
        for k in ("used", "limit", "actual", "tokens_used", "tokens_limit"):
            if k in free_usage_tokens and free_usage_tokens[k] is not None:
                try:
                    clean[k] = int(free_usage_tokens[k])
                except (TypeError, ValueError):
                    pass
        if clean:
            blob["tokens"] = clean

    em_l = em.lower()
    with _AuthFileLock(path):
        data = load_pool(path)
        key = None
        entry = None
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            if (v.get("email") or "").strip().lower() == em_l:
                key, entry = k, v
                break
        if key is None or entry is None:
            return False
        entry2 = dict(entry)
        entry2["probe"] = blob
        entry2["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data[key] = entry2
        save_pool(path, data)
    return True


def pick(
    path: Path | str,
    *,
    email: str | None = None,
    prefer_fresh: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Pick one account: by email, or freshest live token."""
    entries = list_entries(path, include_expired=True)
    if not entries:
        raise AuthPoolError("auth pool empty")
    if email:
        em = email.lower()
        for k, e in entries:
            if (e.get("email") or "").lower() == em or em in (e.get("email") or "").lower():
                return k, e
        raise AuthPoolError(f"not found: {email}")
    now = time.time()
    live = [(k, e) for k, e in entries if (parse_entry_exp(e) or 0) > now + TOKEN_REFRESH_SKEW]
    pool = live if (prefer_fresh and live) else entries
    return pool[0]


def get_token(path: Path | str, email: str | None = None) -> str:
    _, entry = pick(path, email=email)
    tok = entry.get("access_token") or entry.get("key") or ""
    if not tok:
        raise AuthPoolError("no access_token")
    return tok


def refresh_entry(
    auth_path: Path | str,
    key: str,
    entry: dict[str, Any],
    *,
    proxy: str,
    probe_mode: str = "none",
    probe_chat: bool | None = None,
) -> dict[str, Any]:
    """Refresh one ledger entry from auth.json RT only.

    Never reads or writes cpa_export. For xAI packs use ``--export cpa_files``.
    """
    from .oauth import refresh_tokens

    email = (entry.get("email") or "").strip()
    rt = (entry.get("refresh_token") or "").strip()
    atok = (entry.get("access_token") or entry.get("key") or "").strip()
    if not rt:
        return {"ok": False, "email": email or "?", "error": "no refresh_token"}

    r = refresh_tokens(
        email=email,
        refresh_token=rt,
        access_token=atok,
        proxy=proxy,
        probe_mode=probe_mode,
        probe_chat=probe_chat,
    )
    if not r.get("ok"):
        return r

    new_at = r.get("access_token") or ""
    new_rt = r.get("refresh_token") or rt
    entry2 = dict(entry)
    entry2["access_token"] = new_at
    entry2["key"] = new_at
    entry2["refresh_token"] = new_rt
    if r.get("id_token"):
        entry2["id_token"] = r["id_token"]
    # Keep expires_at (primary for status_row) in sync with JWT exp.
    exp_unix: float | None = None
    exp_in = r.get("expires_in")
    if r.get("exp_new"):
        exp_unix = float(int(r["exp_new"]))
    elif exp_in is not None:
        exp_unix = time.time() + int(exp_in)
    if exp_unix is not None:
        entry2 = apply_token_expiry(
            entry2,
            exp_unix=exp_unix,
            expires_in=int(exp_in) if exp_in is not None else None,
        )
    entry2["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry2["disabled"] = False
    upsert(auth_path, entry2, key=key)

    r["auth_path"] = str(auth_path)
    return r



def summarize(
    path: Path | str,
    *,
    skew_sec: float | None = None,
) -> dict[str, int]:
    rows = [
        status_row(k, e, skew_sec=skew_sec)
        for k, e in list_entries(path, include_disabled=True, include_expired=True)
    ]
    now_fresh = sum(1 for r in rows if r.get("state") == "fresh")
    needs = sum(1 for r in rows if r.get("needs_refresh"))
    expired = sum(1 for r in rows if r.get("state") == "expired")
    disabled = sum(1 for r in rows if r.get("disabled"))
    quota_exhausted = sum(
        1 for r in rows if (r.get("probe_class") or "") == "quota_exhausted"
    )
    return {
        "total": len(rows),
        "fresh": now_fresh,
        "needs_refresh": needs,
        "expired": expired,
        "disabled": disabled,
        "quota_exhausted": quota_exhausted,
        "with_rt": sum(1 for r in rows if r.get("has_rt")),
    }
