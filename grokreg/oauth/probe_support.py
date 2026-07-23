"""Post-mint/refresh alive probes (models/chat) and CLI headers."""
from __future__ import annotations

import logging
from typing import Any

from .constants import BASE_URL, _cpa_tools_import


def _cli_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-grok-client-version": "0.2.93",
        "x-xai-token-auth": "xai-grok-cli",
        "x-authenticateresponse": "authenticate-response",
        "x-grok-client-identifier": "grok-shell",
        "User-Agent": "grok-shell/0.2.93",
    }

def _probe_chat(access_token: str, proxy: str) -> dict[str, Any]:
    """Probe official cli-chat-proxy. Return ok/model/text/status/error (raw on fail)."""
    try:
        probe = _cpa_tools_import("probe")
        c = probe.probe_mini_response(access_token, base_url=BASE_URL, proxy=proxy, timeout=90)
        ok = bool(c.get("ok"))
        err = "" if ok else str(c.get("error") or c.get("text") or "probe_failed")[:400]
        return {
            "ok": ok,
            "model": c.get("model"),
            "text": c.get("text"),
            "status": c.get("status"),
            "error": err,
        }
    except Exception as e:
        return {
            "ok": False,
            "model": None,
            "text": None,
            "status": 0,
            "error": str(e)[:400],
        }

def _probe_models(access_token: str, proxy: str) -> dict[str, Any]:
    """Fast alive check: GET /v1/models (much less soft-gate than chat)."""
    from curl_cffi import requests as cr

    try:
        s = cr.Session(impersonate="chrome131", proxies={"http": proxy, "https": proxy})
        resp = s.get(
            BASE_URL + "/models",
            headers=_cli_headers(access_token),
            timeout=20,
        )
        body = resp.json() if resp.status_code == 200 else {}
        ids = [x.get("id") for x in (body.get("data") or []) if isinstance(x, dict)]
        ok = resp.status_code == 200
        model = None
        for mid in ids:
            if mid and "grok" in str(mid).lower():
                model = mid
                break
        if model is None and ids:
            model = ids[0]
        err = "" if ok else (resp.text or f"HTTP {resp.status_code}")[:400]
        return {
            "ok": ok,
            "model": model,
            "text": f"models={len(ids)}" if ok else None,
            "status": resp.status_code,
            "error": err,
            "model_ids": ids,
        }
    except Exception as e:
        return {
            "ok": False,
            "model": None,
            "text": None,
            "status": 0,
            "error": str(e)[:400],
            "model_ids": [],
        }

def _resolve_probe_mode(probe_mode: str | None = None, probe_chat: bool | None = None) -> str:
    """Default models (parallel-safe). chat opt-in; probe_chat=False => none."""
    if probe_chat is False:
        return "none"
    mode = (probe_mode or "models").strip().lower()
    if mode in ("0", "off", "false", "no", "none", "skip"):
        return "none"
    if mode in ("chat", "models"):
        return mode
    return "models"

def _post_write_probe(access_token: str, proxy: str, mode: str) -> dict[str, Any]:
    """Post mint/refresh probe; chat_* keys stay for CLI tally."""
    mode = _resolve_probe_mode(mode)
    empty = {
        "ok": None,
        "model": None,
        "text": None,
        "status": None,
        "error": None,
        "probe_mode": mode,
    }
    if mode == "none":
        return empty
    r = _probe_chat(access_token, proxy) if mode == "chat" else _probe_models(access_token, proxy)
    r["probe_mode"] = mode
    if r.get("ok"):
        logging.info("probe(%s): ok=True model=%s text=%s", mode, r.get("model"), r.get("text"))
    else:
        logging.warning(
            "probe(%s): ok=False status=%s err=%s",
            mode,
            r.get("status"),
            (r.get("error") or "-")[:300],
        )
    return r

