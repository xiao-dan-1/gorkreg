"""Billing payload normalize + ratelimit header → Grok quota shape.

Pure parse/merge helpers used by probe_quota HTTP paths.
No network I/O here.
"""
from __future__ import annotations

from typing import Any


def grok_quota_from_ratelimit_headers(limits: dict[str, Any] | None) -> dict[str, Any]:
    """Map x-ratelimit-* headers to sub2api Grok usage shape.

    sub2api GET /admin/accounts/{id}/usage (platform=grok) exposes:
      grok_quota_snapshot_state: unknown_until_first_response | observed
      grok_request_quota: {limit, remaining}
      grok_token_quota:  {limit, remaining}
    (five_hour / seven_day stay null for Grok — not Claude windows.)
    """
    lim = {str(k).lower(): str(v) for k, v in (limits or {}).items()}

    def _pick(*names: str) -> str | None:
        for n in names:
            if n in lim and lim[n] not in ("", "None"):
                return lim[n]
        return None

    req_lim = _pick("x-ratelimit-limit-requests", "x-ratelimit-limit-request")
    req_rem = _pick("x-ratelimit-remaining-requests", "x-ratelimit-remaining-request")
    tok_lim = _pick("x-ratelimit-limit-tokens", "x-ratelimit-limit-token")
    tok_rem = _pick("x-ratelimit-remaining-tokens", "x-ratelimit-remaining-token")

    def _pair(limit_s: str | None, rem_s: str | None) -> dict[str, Any] | None:
        if limit_s is None and rem_s is None:
            return None
        out: dict[str, Any] = {}
        try:
            if limit_s is not None:
                out["limit"] = int(float(limit_s))
        except ValueError:
            out["limit"] = limit_s
        try:
            if rem_s is not None:
                out["remaining"] = int(float(rem_s))
        except ValueError:
            out["remaining"] = rem_s
        return out or None

    req = _pair(req_lim, req_rem)
    tok = _pair(tok_lim, tok_rem)
    observed = bool(req or tok)
    return {
        "grok_quota_snapshot_state": "observed" if observed else "unknown_until_first_response",
        "grok_request_quota": req,
        "grok_token_quota": tok,
        # explicit nulls — matches sub2api Grok (no Claude 5h/7d bars)
        "five_hour": None,
        "seven_day": None,
    }


def _billing_num(value: Any) -> float:
    """Parse billing numbers that may be plain float or {\"val\": N}."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    if isinstance(value, dict):
        if "val" in value:
            return _billing_num(value.get("val"))
        for k in ("value", "amount", "total"):
            if k in value:
                return _billing_num(value.get(k))
    return 0.0


def _billing_first(root: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in root:
            return root[k]
    return None


def parse_billing_payload(data: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize GET /v1/billing JSON (grok2api-compatible field aliases)."""
    root = data if isinstance(data, dict) else {}
    if isinstance(root.get("config"), dict):
        root = root["config"]
    monthly = _billing_num(_billing_first(root, "monthlyLimit", "monthly_limit"))
    used = _billing_num(_billing_first(root, "used", "totalUsed", "includedUsed"))
    on_cap = _billing_num(_billing_first(root, "onDemandCap", "on_demand_cap", "maxAmountPerMonth"))
    on_used = _billing_num(_billing_first(root, "onDemandUsed", "on_demand_used"))
    prepaid = _billing_num(_billing_first(root, "prepaidBalance", "prepaid_balance"))
    credit_pct = _billing_num(_billing_first(root, "creditUsagePercent", "credit_usage_percent"))
    if credit_pct == 0:
        if on_cap > 0:
            credit_pct = on_used / on_cap * 100.0
        elif monthly > 0:
            credit_pct = used / monthly * 100.0
    remaining = None
    if monthly > 0:
        remaining = max(0.0, monthly - used)
    period_type = period_start = period_end = ""
    cur = root.get("currentPeriod")
    if isinstance(cur, dict):
        period_type = str(cur.get("type") or "")
        period_start = str(cur.get("start") or "")
        period_end = str(cur.get("end") or "")
    if not period_start:
        period_start = str(_billing_first(root, "billingPeriodStart", "billing_period_start") or "")
    if not period_end:
        period_end = str(_billing_first(root, "billingPeriodEnd", "billing_period_end") or "")
    plan_code = str(_billing_first(root, "planCode", "plan_code", "subscriptionTier", "tier") or "")
    plan_name = str(_billing_first(root, "planName", "plan_name") or "")
    return {
        "plan_code": plan_code,
        "plan_name": plan_name,
        "monthly_limit": monthly,
        "used": used,
        "remaining": remaining,
        "on_demand_cap": on_cap,
        "on_demand_used": on_used,
        "prepaid_balance": prepaid,
        "credit_usage_percent": round(credit_pct, 4),
        "is_unified": bool(_billing_first(root, "isUnifiedBillingUser", "is_unified_billing_user")),
        "top_up_method": str(_billing_first(root, "topUpMethod", "top_up_method") or ""),
        "period_type": period_type,
        "period_start": period_start,
        "period_end": period_end,
        "billing_period_start": str(_billing_first(root, "billingPeriodStart", "billing_period_start") or ""),
        "billing_period_end": str(_billing_first(root, "billingPeriodEnd", "billing_period_end") or ""),
    }


def merge_billing_snapshots(monthly: dict[str, Any], credits: dict[str, Any]) -> dict[str, Any]:
    """Merge plain /billing with ?format=credits; keep BOTH month and week views.

    xAI:
      GET /billing              → calendar month (monthlyLimit / billingPeriod*)
      GET /billing?format=credits → currentPeriod often USAGE_PERIOD_TYPE_WEEKLY
    """
    m = dict(monthly or {})
    c = dict(credits or {})
    out = dict(m)

    # Prefer plan/on-demand from credits when present
    if not out.get("plan_code") and c.get("plan_code"):
        out["plan_code"] = c["plan_code"]
    if not out.get("plan_name") and c.get("plan_name"):
        out["plan_name"] = c["plan_name"]
    for k in (
        "on_demand_cap",
        "on_demand_used",
        "prepaid_balance",
        "credit_usage_percent",
        "is_unified",
        "top_up_method",
    ):
        if k in c and c.get(k) not in (None, ""):
            out[k] = c[k]

    # Explicit dual windows (do not overwrite month with week)
    out["month_period_start"] = (
        m.get("billing_period_start")
        or m.get("period_start")
        or ""
    )
    out["month_period_end"] = (
        m.get("billing_period_end")
        or m.get("period_end")
        or ""
    )
    # week/credits period: prefer currentPeriod on credits payload
    out["week_period_type"] = c.get("period_type") or ""
    out["week_period_start"] = c.get("period_start") or ""
    out["week_period_end"] = c.get("period_end") or ""
    # if credits only put dates in billing_period_* (some builds)
    if not out["week_period_start"]:
        out["week_period_start"] = c.get("billing_period_start") or ""
    if not out["week_period_end"]:
        out["week_period_end"] = c.get("billing_period_end") or ""

    # Backward-compat single period_* : prefer WEEKLY when present (old table used this)
    if out.get("week_period_type") or out.get("week_period_end"):
        out["period_type"] = out.get("week_period_type") or out.get("period_type") or ""
        out["period_start"] = out.get("week_period_start") or out.get("period_start") or ""
        out["period_end"] = out.get("week_period_end") or out.get("period_end") or ""
    else:
        out["period_type"] = m.get("period_type") or ""
        out["period_start"] = out["month_period_start"]
        out["period_end"] = out["month_period_end"]

    out["billing_period_start"] = out["month_period_start"]
    out["billing_period_end"] = out["month_period_end"]
    return out
