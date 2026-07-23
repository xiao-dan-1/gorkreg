"""Chat probe classification — aligned with ywddd/grok-inspection.

Free-tier "used up" is **not** bare HTTP 429 and **not** billing zeros.
It is only structured free-usage exhaustion in the error body/code.

Real upstream shape::

    {"code":"subscription:free-usage-exhausted",
     "error":"You've used all the included free usage for model ..."}
"""
from __future__ import annotations

import json
import re
from typing import Any

# Stable labels (export-friendly; match inspection vocabulary where it matters).
CLASS_HEALTHY = "healthy"
CLASS_QUOTA_EXHAUSTED = "quota_exhausted"
CLASS_RATE_LIMITED = "rate_limited"
CLASS_REAUTH = "reauth"
CLASS_PERMISSION_DENIED = "permission_denied"
CLASS_MODEL_UNAVAILABLE = "model_unavailable"
CLASS_PROBE_ERROR = "probe_error"
CLASS_UNKNOWN = "unknown"

_FREE_USAGE_MARKERS = (
    "free-usage-exhausted",
    "used all the included free usage",
    "included free usage has been exhausted",
)

# Optional detail from free-usage message: tokens (actual/limit): 2012994/2000000
_TOKENS_PAIR_RE = re.compile(
    r"tokens\s*\([^)]*\)\s*:\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)


def _lower(value: str | None) -> str:
    return (value or "").strip().lower()


def _contains_any(text: str, *needles: str) -> bool:
    blob = _lower(text)
    return any(n and n in blob for n in needles)


def extract_probe_error(body: str | bytes | None) -> dict[str, str]:
    """Pull compact code/message from JSON or plain error body (no full dump)."""
    if body is None:
        return {"code": "", "message": ""}
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    else:
        text = str(body)
    text = text.strip()
    if not text:
        return {"code": "", "message": ""}

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"code": "", "message": _truncate(text, 400)}

    if not isinstance(data, dict):
        return {"code": "", "message": _truncate(text, 400)}

    code = _as_str(data.get("code"))
    message = ""
    err = data.get("error")
    if isinstance(err, dict):
        if not code:
            code = _as_str(err.get("code"))
        message = _as_str(err.get("message")) or _as_str(err.get("error"))
    elif isinstance(err, str):
        message = err.strip()
    if not message:
        message = _as_str(data.get("message"))
    return {"code": code, "message": _truncate(message, 400)}


def is_free_usage_exhausted(code: str | None = None, message: str | None = None) -> bool:
    """True only for Grok free-tier exhaustion (inspection-compatible).

    Bare 429 / generic rate-limit text must return False.
    """
    blob = f"{_lower(code)} {_lower(message)}"
    return _contains_any(blob, *_FREE_USAGE_MARKERS)


def parse_free_usage_token_pair(message: str | None) -> dict[str, int] | None:
    """If message embeds tokens (actual/limit): a/b, return {used, limit}."""
    if not message:
        return None
    m = _TOKENS_PAIR_RE.search(message)
    if not m:
        return None
    try:
        used, lim = int(m.group(1)), int(m.group(2))
    except ValueError:
        return None
    return {"used": used, "limit": lim}


def classify_chat_probe(
    status: int,
    *,
    code: str = "",
    message: str = "",
    request_error: str = "",
) -> dict[str, Any]:
    """Classify one chat/responses attempt. Pure; no I/O.

    Returns::
        classification, free_usage_exhausted, usage_exhausted,
        error_code, error_message, reason, free_usage_tokens (optional)
    """
    code = (code or "").strip()
    message = (message or "").strip()
    req_err = (request_error or "").strip()
    blob = f"{_lower(code)} {_lower(message)} {_lower(req_err)}"

    free = is_free_usage_exhausted(code, message) or is_free_usage_exhausted(
        code, req_err
    )
    tok_pair = parse_free_usage_token_pair(message) or parse_free_usage_token_pair(
        req_err
    )

    base: dict[str, Any] = {
        "classification": CLASS_UNKNOWN,
        "free_usage_exhausted": bool(free),
        "usage_exhausted": bool(free),  # free path only; paid billing is separate
        "error_code": code or None,
        "error_message": message or (req_err[:400] if req_err else None),
        "reason": "",
        "free_usage_tokens": tok_pair,
    }

    # Auth first (same order as inspection).
    if status == 401 or _contains_any(
        blob,
        "token is expired",
        "token has been invalidated",
        "invalid_grant",
        "unauthorized",
    ):
        base.update(
            {
                "classification": CLASS_REAUTH,
                "reason": "认证已过期或失效",
                "usage_exhausted": False,
                "free_usage_exhausted": False,
            }
        )
        return base

    if free:
        base.update(
            {
                "classification": CLASS_QUOTA_EXHAUSTED,
                "reason": "免费额度用尽 (free-usage-exhausted)",
                "usage_exhausted": True,
                "free_usage_exhausted": True,
            }
        )
        return base

    # Bare 429 / temporary throttle — NOT quota exhausted.
    if status == 429:
        base.update(
            {
                "classification": CLASS_RATE_LIMITED,
                "reason": "临时限流 (HTTP 429)，稍后重试",
                "usage_exhausted": False,
            }
        )
        return base

    if status in (402, 403) or _contains_any(
        blob,
        "permission-denied",
        "chat endpoint is denied",
        "deactivated",
        "suspended",
        "banned",
    ):
        base.update(
            {
                "classification": CLASS_PERMISSION_DENIED,
                "reason": f"对话权限被拒绝 (HTTP {status})" if status else "对话权限被拒绝",
                "usage_exhausted": False,
            }
        )
        return base

    if status == 404 or _contains_any(
        blob, "not-found", "does not exist", "no access to it"
    ):
        base.update(
            {
                "classification": CLASS_MODEL_UNAVAILABLE,
                "reason": "测试模型不可用",
                "usage_exhausted": False,
            }
        )
        return base

    if 200 <= status < 300:
        base.update(
            {
                "classification": CLASS_HEALTHY,
                "reason": "对话测试成功",
                "error_code": None,
                "error_message": None,
                "usage_exhausted": False,
                "free_usage_exhausted": False,
            }
        )
        return base

    if req_err or status > 0:
        reason = req_err or (f"探测失败 (HTTP {status})" if status else "探测失败")
        base.update(
            {
                "classification": CLASS_PROBE_ERROR,
                "reason": reason[:200],
                "usage_exhausted": False,
            }
        )
        return base

    base["reason"] = "无法可靠分类"
    return base


def classify_from_http_body(status: int, body: str | bytes | None) -> dict[str, Any]:
    """Convenience: parse body then classify."""
    err = extract_probe_error(body)
    return classify_chat_probe(
        status, code=err.get("code") or "", message=err.get("message") or ""
    )


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _truncate(value: str, max_len: int) -> str:
    value = value.strip()
    if max_len <= 0 or len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


__all__ = [
    "CLASS_HEALTHY",
    "CLASS_QUOTA_EXHAUSTED",
    "CLASS_RATE_LIMITED",
    "CLASS_REAUTH",
    "CLASS_PERMISSION_DENIED",
    "CLASS_MODEL_UNAVAILABLE",
    "CLASS_PROBE_ERROR",
    "CLASS_UNKNOWN",
    "extract_probe_error",
    "is_free_usage_exhausted",
    "parse_free_usage_token_pair",
    "classify_chat_probe",
    "classify_from_http_body",
]
