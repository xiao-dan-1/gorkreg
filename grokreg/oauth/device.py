"""Device-code OAuth helpers (device/code + token poll)."""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..errors import MintError
from . import constants as C
from .constants import (
    CLIENT_ID,
    DEVICE_CODE_URL,
    SCOPES,
    TOKEN_URL,
    _DEVICE_CODE_LOCK,
    _device_code_min_interval,
)


def _retry_after_seconds(err: Any, fallback: float) -> float:
    """Parse Retry-After (seconds). Add small jitter. Works for HTTPError or resp."""
    try:
        hdrs = getattr(err, "headers", None) or {}
        raw = None
        if hasattr(hdrs, "get"):
            raw = hdrs.get("Retry-After") or hdrs.get("retry-after")
        if raw is None and isinstance(hdrs, dict):
            raw = hdrs.get("Retry-After") or hdrs.get("retry-after")
        if raw is not None:
            s = str(raw).strip()
            if s.isdigit():
                base = float(s)
            else:
                base = fallback
            base = max(0.2, min(30.0, base))
            return base + random.uniform(0.0, 0.25)
    except Exception:
        pass
    return fallback + random.uniform(0.0, min(0.35, fallback * 0.15))


def _device_code(
    proxy: str,
    *,
    max_attempts: int = 6,
    session: Any | None = None,
) -> dict[str, Any]:
    """Request device code; optional spacing (env/adaptive) + 429 backoff.

    If ``session`` (curl_cffi) is provided, POST via that session (TLS reuse
    with mint). Else urllib + ProxyHandler (standalone / tests).

    Lock only gates min-interval; HTTP runs unlocked so high -j can issue
    device/code in parallel when interval=0.
    """
    data_form = {"client_id": CLIENT_ID, "scope": SCOPES}
    data_bytes = urllib.parse.urlencode(data_form).encode()
    last_err: Exception | None = None
    backoff = 1.0

    # urllib fallback opener (built once)
    opener = None
    if session is None:
        ph = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(ph)

    for attempt in range(1, max_attempts + 1):
        with _DEVICE_CODE_LOCK:
            now = time.time()
            wait = _device_code_min_interval() - (now - C._device_code_last_ts)
            if wait > 0:
                time.sleep(wait)
            C._device_code_last_ts = time.time()

        try:
            if session is not None:
                resp = session.post(
                    DEVICE_CODE_URL,
                    data=data_form,
                    headers={
                        "content-type": "application/x-www-form-urlencoded",
                        "accept": "application/json",
                    },
                    timeout=30,
                )
                code = int(getattr(resp, "status_code", 0) or 0)
                if code == 429 or code >= 500:
                    if code == 429:
                        C.note_device_code_429()
                    exp = min(12.0, 1.2 * (2 ** (attempt - 1)))
                    backoff = (
                        _retry_after_seconds(resp, exp) if code == 429 else exp
                    )
                    logging.warning(
                        "device/code HTTP %s attempt=%s/%s backoff=%.1fs adaptive_iv=%.2f",
                        code,
                        attempt,
                        max_attempts,
                        backoff,
                        C._device_code_min_interval(),
                    )
                    time.sleep(backoff)
                    continue
                if code != 200:
                    raise MintError(
                        f"device/code HTTP {code}",
                        code="mint_device_code",
                    )
                body = resp.json() if getattr(resp, "text", None) else {}
                if not isinstance(body, dict):
                    body = json.loads(resp.text or "{}")
                C.note_device_code_success()
                return body

            # urllib path
            req = urllib.request.Request(
                DEVICE_CODE_URL,
                data=data_bytes,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            assert opener is not None
            with opener.open(req, timeout=30) as r:
                body = json.loads(r.read())
            C.note_device_code_success()
            return body
        except urllib.error.HTTPError as e:
            last_err = e
            code = int(getattr(e, "code", 0) or 0)
            if code == 429 or code >= 500:
                if code == 429:
                    C.note_device_code_429()
                exp = min(12.0, 1.2 * (2 ** (attempt - 1)))
                backoff = _retry_after_seconds(e, exp) if code == 429 else exp
                logging.warning(
                    "device/code HTTP %s attempt=%s/%s backoff=%.1fs adaptive_iv=%.2f",
                    code,
                    attempt,
                    max_attempts,
                    backoff,
                    C._device_code_min_interval(),
                )
            else:
                raise MintError(
                    f"device/code HTTP {code}: {e}",
                    code="mint_device_code",
                ) from e
        except MintError:
            raise
        except Exception as e:
            last_err = e
            backoff = min(10.0, 1.1 * attempt)
            logging.warning(
                "device/code error attempt=%s/%s: %s; backoff=%.1fs",
                attempt,
                max_attempts,
                e,
                backoff,
            )
        time.sleep(backoff)

    raise MintError(
        f"device/code failed after {max_attempts} attempts: {last_err}",
        code="mint_device_code",
    ) from last_err


def _poll_token_interval(interval_sec: float | None = None) -> float:
    """Initial token poll sleep (seconds). RFC 8628 interval preferred."""
    import os

    if interval_sec is not None:
        try:
            return max(0.2, float(interval_sec))
        except (TypeError, ValueError):
            pass
    raw = (os.environ.get("GROK_TOKEN_POLL_INTERVAL") or "").strip()
    if not raw:
        return 0.8
    try:
        return max(0.2, float(raw))
    except ValueError:
        return 0.8


def _poll_token(
    proxy: str,
    device_code: str,
    timeout_sec: int = 120,
    *,
    interval_sec: float | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Poll token endpoint. ``session`` reuses mint TLS when provided."""
    from curl_cffi import requests as cr

    deadline = time.time() + timeout_sec
    interval = _poll_token_interval(interval_sec)
    owned = session is None
    s = session or cr.Session(
        impersonate="chrome131", proxies={"http": proxy, "https": proxy}
    )
    try:
        while time.time() < deadline:
            resp = s.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": CLIENT_ID,
                },
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "accept": "application/json",
                },
                timeout=45,
            )
            body = resp.json() if resp.text else {}
            err = (body.get("error") or "").lower()
            if resp.status_code == 200 and body.get("access_token"):
                return {"ok": True, "token": body}
            if err == "authorization_pending":
                time.sleep(interval)
                continue
            if err == "slow_down":
                interval = min(10.0, interval + 0.8)
                time.sleep(interval)
                continue
            if resp.status_code == 429:
                wait = _retry_after_seconds(resp, interval)
                wait = max(0.3, min(8.0, wait))
                time.sleep(wait)
                continue
            return {"ok": False, "error": err, "body": body}
        return {"ok": False, "error": "timeout"}
    finally:
        if owned:
            try:
                s.close()
            except Exception:
                pass
