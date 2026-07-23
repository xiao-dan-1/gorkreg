"""Quota / billing / models probe against cli-chat-proxy (HTTP orchestration).

Billing parse helpers live in probe_billing.py (pure, no network).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .constants import BASE_URL
from .probe_billing import (
    grok_quota_from_ratelimit_headers,
    merge_billing_snapshots,
    parse_billing_payload,
)
from .probe_classify import (
    CLASS_PROBE_ERROR,
    classify_chat_probe,
    classify_from_http_body,
)
from .probe_support import _cli_headers

# Re-export for historical `from grokreg.oauth.probe_quota import parse_billing_payload`
__all__ = [
    "probe_quota",
    "grok_quota_from_ratelimit_headers",
    "parse_billing_payload",
    "merge_billing_snapshots",
]


def _ratelimit_headers(resp: Any) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()}


def probe_quota(
    cpa_path: Path | None = None,
    *,
    email: str | None = None,
    access_token: str | None = None,
    proxy: str,
    mode: str = "chat",
    timeout: float = 45.0,
    retries: int = 2,
) -> dict[str, Any]:
    """Probe token health / billing / rate limits (ledger-first capable).

    Prefer: access_token= from auth.json (CLI --probe-quota).
    Legacy: cpa_path= xai-*.json under cpa_export (export layer only).

    mode:
      - models:  GET /v1/models (fast alive; no quota numbers)
      - billing: GET /v1/billing + ?format=credits — month + week windows
      - chat:    light chat/completions → x-ratelimit-* + free-usage classify
      - quota:   billing then chat
      - both:    models first, then chat if models ok

    Chat classification (grok-inspection aligned):
      healthy | quota_exhausted | rate_limited | reauth | …
      Bare HTTP 429 ≠ free usage exhausted; only free-usage body/code counts.
    """
    from curl_cffi import requests as cr

    mode = (mode or "chat").strip().lower()
    if mode not in ("models", "chat", "both", "billing", "quota"):
        mode = "chat"
    retries = max(0, int(retries))
    timeout = float(timeout)

    tok = (access_token or "").strip()
    em = (email or "").strip()
    if not tok and cpa_path is not None:
        p = Path(cpa_path)
        if not p.exists():
            return {"ok": False, "email": em or "?", "mode": mode, "error": f"not found: {p}"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = p.read_text(encoding="utf-8")
            data, _ = json.JSONDecoder().raw_decode(raw)
        if not isinstance(data, dict):
            return {"ok": False, "email": em or "?", "mode": mode, "error": "cpa not object"}
        em = em or str(data.get("email") or p.stem)
        tok = str(data.get("access_token") or "").strip()
    if not tok:
        return {
            "ok": False,
            "email": em or "?",
            "mode": mode,
            "error": "no access_token (ledger or cpa file)",
        }
    email = em or "?"

    hdrs = _cli_headers(tok)
    s = cr.Session(impersonate="chrome131", proxies={"http": proxy, "https": proxy})
    out: dict[str, Any] = {
        "ok": False,
        "email": email,
        "mode": mode,
        "status": 0,
        "limits": {},
        "usage": {},
        "billing": {},
        # sub2api Grok /usage alignment (filled after chat headers)
        "grok_quota_snapshot_state": "unknown_until_first_response",
        "grok_request_quota": None,
        "grok_token_quota": None,
        "five_hour": None,
        "seven_day": None,
        "models_ok": None,
        "has_grok_45": None,
        "model_ids": [],
        "elapsed_sec": 0.0,
        "attempts": 0,
        "error": None,
        # inspection-aligned free-usage / chat verdict
        "classification": None,
        "free_usage_exhausted": None,
        "usage_exhausted": None,
        "error_code": None,
        "error_message": None,
        "reason": None,
        "free_usage_tokens": None,
    }

    def _do_models() -> dict[str, Any]:
        t0 = time.time()
        last_err = None
        for attempt in range(retries + 1):
            out["attempts"] = attempt + 1
            try:
                resp = s.get(BASE_URL + "/models", headers=hdrs, timeout=min(timeout, 20.0))
                body = resp.json() if resp.status_code == 200 else {}
                ids = [x.get("id") for x in (body.get("data") or []) if isinstance(x, dict)]
                ok = resp.status_code == 200
                return {
                    "ok": ok,
                    "status": resp.status_code,
                    "models_ok": ok,
                    "has_grok_45": any(i == "grok-4.5" for i in ids),
                    "model_ids": ids,
                    "elapsed_sec": round(time.time() - t0, 2),
                    "error": None if ok else f"HTTP {resp.status_code}",
                }
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < retries:
                    time.sleep(1.0 + attempt)
        return {
            "ok": False,
            "status": 0,
            "models_ok": False,
            "has_grok_45": False,
            "model_ids": [],
            "elapsed_sec": round(time.time() - t0, 2),
            "error": f"timeout/err: {last_err}",
        }

    def _do_billing() -> dict[str, Any]:
        """GET /billing + ?format=credits; expose month + week windows."""
        t0 = time.time()
        last_err = None
        for attempt in range(retries + 1):
            out["attempts"] = attempt + 1
            try:
                r1 = s.get(BASE_URL + "/billing", headers=hdrs, timeout=min(timeout, 30.0))
                if r1.status_code != 200:
                    return {
                        "ok": False,
                        "status": r1.status_code,
                        "billing": {},
                        "elapsed_sec": round(time.time() - t0, 2),
                        "error": f"billing HTTP {r1.status_code}",
                    }
                monthly = parse_billing_payload(r1.json() if r1.text else {})
                credits: dict[str, Any] = {}
                try:
                    r2 = s.get(
                        BASE_URL + "/billing?format=credits",
                        headers=hdrs,
                        timeout=min(timeout, 30.0),
                    )
                    if r2.status_code == 200:
                        credits = parse_billing_payload(r2.json() if r2.text else {})
                except Exception:  # noqa: BLE001
                    credits = {}
                billing = merge_billing_snapshots(monthly, credits)
                return {
                    "ok": True,
                    "status": 200,
                    "billing": billing,
                    "elapsed_sec": round(time.time() - t0, 2),
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < retries:
                    time.sleep(1.0 + attempt)
        return {
            "ok": False,
            "status": 0,
            "billing": {},
            "elapsed_sec": round(time.time() - t0, 2),
            "error": f"timeout/err: {last_err}",
        }

    def _do_chat() -> dict[str, Any]:
        # Cap generation: free model still reasons; max_tokens + low effort cuts e2e.
        payload = {
            "model": "grok-4.5",
            "stream": False,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "ok"}],
            "reasoning": {"effort": "low"},
        }
        t0 = time.time()
        last_err = None
        last_cls: dict[str, Any] | None = None
        for attempt in range(retries + 1):
            out["attempts"] = attempt + 1
            try:
                resp = s.post(
                    BASE_URL + "/chat/completions",
                    json=payload,
                    headers=hdrs,
                    timeout=timeout,
                )
                limits = _ratelimit_headers(resp)
                body_text = resp.text or ""
                ok = resp.status_code == 200
                usage: dict[str, Any] = {}
                if ok:
                    try:
                        usage = (resp.json() or {}).get("usage", {}) or {}
                    except Exception:  # noqa: BLE001
                        usage = {}
                    if not isinstance(usage, dict):
                        usage = {}
                    cls = classify_chat_probe(resp.status_code)
                else:
                    cls = classify_from_http_body(resp.status_code, body_text)
                last_cls = cls
                # Bare 429: one short retry (inspection does this; not free-usage).
                if (
                    resp.status_code == 429
                    and not cls.get("free_usage_exhausted")
                    and attempt < retries
                ):
                    time.sleep(0.35 + attempt * 0.2)
                    continue
                err_s = None
                if not ok:
                    code = cls.get("error_code") or ""
                    msg = cls.get("error_message") or ""
                    if code or msg:
                        err_s = f"HTTP {resp.status_code} {code}: {msg}".strip()[:240]
                    else:
                        err_s = f"HTTP {resp.status_code}"
                return {
                    "ok": ok,
                    "status": resp.status_code,
                    "limits": limits,
                    "usage": usage,
                    "elapsed_sec": round(time.time() - t0, 2),
                    "error": err_s,
                    "classification": cls.get("classification"),
                    "free_usage_exhausted": cls.get("free_usage_exhausted"),
                    "usage_exhausted": cls.get("usage_exhausted"),
                    "error_code": cls.get("error_code"),
                    "error_message": cls.get("error_message"),
                    "reason": cls.get("reason"),
                    "free_usage_tokens": cls.get("free_usage_tokens"),
                }
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                last_cls = classify_chat_probe(
                    0, network_error=f"{type(exc).__name__}: {exc}"
                )
                if attempt < retries:
                    time.sleep(1.5 + attempt)
        cls = last_cls or classify_chat_probe(
            0, network_error=f"timeout/err: {last_err}"
        )
        return {
            "ok": False,
            "status": 0,
            "limits": {},
            "usage": {},
            "elapsed_sec": round(time.time() - t0, 2),
            "error": f"timeout/err: {last_err}",
            "classification": cls.get("classification") or CLASS_PROBE_ERROR,
            "free_usage_exhausted": bool(cls.get("free_usage_exhausted")),
            "usage_exhausted": bool(cls.get("usage_exhausted")),
            "error_code": cls.get("error_code"),
            "error_message": cls.get("error_message"),
            "reason": cls.get("reason") or f"timeout/err: {last_err}",
            "free_usage_tokens": cls.get("free_usage_tokens"),
        }

    def _attach_grok_quota(limits: dict[str, Any] | None) -> None:
        gq = grok_quota_from_ratelimit_headers(limits)
        out.update(gq)

    _CHAT_CLASS_KEYS = (
        "classification",
        "free_usage_exhausted",
        "usage_exhausted",
        "error_code",
        "error_message",
        "reason",
        "free_usage_tokens",
    )

    def _merge_chat(c: dict[str, Any]) -> None:
        out["status"] = c.get("status", 0)
        out["limits"] = c.get("limits") or {}
        out["usage"] = c.get("usage") or {}
        for k in _CHAT_CLASS_KEYS:
            if k in c:
                out[k] = c.get(k)
        _attach_grok_quota(out["limits"])
        out["ok"] = bool(c.get("ok"))
        out["error"] = c.get("error")

    if mode == "billing":
        b = _do_billing()
        out["status"] = b.get("status", 0)
        out["billing"] = b.get("billing") or {}
        out["elapsed_sec"] = b.get("elapsed_sec", 0.0)
        out["ok"] = bool(b.get("ok"))
        out["error"] = b.get("error")
        return out

    if mode == "quota":
        b = _do_billing()
        out["billing"] = b.get("billing") or {}
        sec = float(b.get("elapsed_sec") or 0.0)
        if not b.get("ok"):
            out["status"] = b.get("status", 0)
            out["elapsed_sec"] = sec
            out["ok"] = False
            out["error"] = b.get("error") or "billing failed"
            return out
        c = _do_chat()
        _merge_chat(c)
        out["elapsed_sec"] = round(sec + float(c.get("elapsed_sec") or 0.0), 2)
        return out

    if mode in ("models", "both"):
        m = _do_models()
        out.update({k: m[k] for k in m if k != "ok"})
        out["models_ok"] = m.get("models_ok")
        if mode == "models":
            out["ok"] = bool(m.get("ok"))
            out["error"] = m.get("error")
            return out
        if not m.get("ok"):
            out["ok"] = False
            out["error"] = m.get("error") or "models probe failed"
            return out

    c = _do_chat()
    _merge_chat(c)
    out["elapsed_sec"] = c.get("elapsed_sec", 0.0)
    return out
