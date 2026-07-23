"""RT→AT refresh (ledger-first). No file-path cpa refresh API."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .constants import CLIENT_ID, TOKEN_URL
from .probe_support import _post_write_probe, _resolve_probe_mode


def refresh_tokens(
    *,
    email: str,
    refresh_token: str,
    access_token: str | None = None,
    proxy: str,
    probe_mode: str = "none",
    probe_chat: bool | None = None,
) -> dict[str, Any]:
    """RT→AT using tokens only (ledger-first). Does not read/write cpa_export."""
    import base64
    from curl_cffi import requests as cr

    email = (email or "").strip()
    rt = (refresh_token or "").strip()
    if not rt:
        return {"ok": False, "email": email or "?", "error": "no refresh_token"}

    at_old = (access_token or "").strip()
    exp_old = None
    if at_old and "." in at_old:
        try:
            pad = "=" * (-len(at_old.split(".")[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(at_old.split(".")[1] + pad))
            exp_old = claims.get("exp")
        except Exception:
            pass

    s = cr.Session(impersonate="chrome131", proxies={"http": proxy, "https": proxy})
    resp = s.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": CLIENT_ID,
        },
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        },
        timeout=45,
    )
    if resp.status_code != 200:
        err = (resp.text or "")[:300] if resp.text else f"HTTP {resp.status_code}"
        return {
            "ok": False,
            "email": email or "?",
            "error": f"refresh rejected: {err}",
            "status": resp.status_code,
        }

    td = resp.json() if resp.content else {}
    new_at = (td.get("access_token") or "").strip()
    new_rt = (td.get("refresh_token") or "").strip() or rt
    if not new_at:
        return {"ok": False, "email": email or "?", "error": "missing access_token"}

    exp_new = None
    expires_in = td.get("expires_in")
    if new_at and "." in new_at:
        try:
            pad = "=" * (-len(new_at.split(".")[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(new_at.split(".")[1] + pad))
            exp_new = claims.get("exp")
            if expires_in is None and claims.get("exp") and claims.get("iat"):
                expires_in = int(claims["exp"]) - int(claims["iat"])
        except Exception:
            pass

    mode = _resolve_probe_mode(probe_mode, probe_chat)
    probe = _post_write_probe(new_at, proxy, mode)

    return {
        "ok": True,
        "email": email or "?",
        "access_token": new_at,
        "refresh_token": new_rt,
        "id_token": td.get("id_token"),
        "expires_in": expires_in or 21600,
        "exp_old": exp_old,
        "exp_new": exp_new,
        "probe_mode": mode,
        "chat_ok": probe.get("ok"),
        "chat_model": probe.get("model"),
        "chat_text": probe.get("text"),
        "chat_error": probe.get("error") or "",
        "chat_status": probe.get("status"),
    }
