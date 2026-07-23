"""Unit pins for probe_billing pure helpers (split from probe_quota)."""
from __future__ import annotations

from grokreg.oauth.probe_billing import (
    grok_quota_from_ratelimit_headers,
    merge_billing_snapshots,
    parse_billing_payload,
)
from grokreg.oauth import (
    grok_quota_from_ratelimit_headers as pub_gq,
    merge_billing_snapshots as pub_merge,
    parse_billing_payload as pub_parse,
)
from grokreg.oauth.probe_quota import (
    grok_quota_from_ratelimit_headers as re_gq,
    merge_billing_snapshots as re_merge,
    parse_billing_payload as re_parse,
)


def test_parse_billing_payload_aliases_and_nested_config():
    raw = {
        "config": {
            "monthlyLimit": 100,
            "used": 40,
            "onDemandCap": 50,
            "onDemandUsed": 10,
            "planCode": "free",
            "planName": "Free",
            "billingPeriodStart": "2026-07-01",
            "billingPeriodEnd": "2026-07-31",
            "isUnifiedBillingUser": False,
        }
    }
    b = parse_billing_payload(raw)
    assert b["monthly_limit"] == 100.0
    assert b["used"] == 40.0
    assert b["remaining"] == 60.0
    assert b["on_demand_cap"] == 50.0
    assert b["on_demand_used"] == 10.0
    assert b["credit_usage_percent"] == 20.0  # 10/50
    assert b["plan_code"] == "free"
    assert b["billing_period_start"] == "2026-07-01"
    assert b["billing_period_end"] == "2026-07-31"


def test_parse_billing_val_wrapper():
    b = parse_billing_payload({"monthlyLimit": {"val": 10}, "used": {"val": 3}})
    assert b["monthly_limit"] == 10.0
    assert b["used"] == 3.0
    assert b["remaining"] == 7.0


def test_merge_keeps_month_and_week_windows():
    monthly = parse_billing_payload(
        {
            "monthlyLimit": 0,
            "used": 0,
            "billingPeriodStart": "2026-07-01T00:00:00Z",
            "billingPeriodEnd": "2026-07-31T23:59:59Z",
        }
    )
    credits = parse_billing_payload(
        {
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "start": "2026-07-14T00:00:00Z",
                "end": "2026-07-21T00:00:00Z",
            },
            "creditUsagePercent": 0,
        }
    )
    m = merge_billing_snapshots(monthly, credits)
    assert m["month_period_start"].startswith("2026-07-01")
    assert m["month_period_end"].startswith("2026-07-31")
    assert "WEEKLY" in m["week_period_type"]
    assert m["week_period_start"].startswith("2026-07-14")
    assert m["billing_period_start"] == m["month_period_start"]
    # period_* prefers weekly for backward-compat table
    assert m["period_start"] == m["week_period_start"]


def test_grok_quota_from_headers_observed():
    gq = grok_quota_from_ratelimit_headers(
        {
            "x-ratelimit-limit-requests": "21",
            "x-ratelimit-remaining-requests": "20",
            "x-ratelimit-limit-tokens": "1000000",
            "x-ratelimit-remaining-tokens": "999984",
        }
    )
    assert gq["grok_quota_snapshot_state"] == "observed"
    assert gq["grok_request_quota"] == {"limit": 21, "remaining": 20}
    assert gq["grok_token_quota"]["limit"] == 1_000_000
    assert gq["five_hour"] is None and gq["seven_day"] is None


def test_grok_quota_unknown_when_empty():
    gq = grok_quota_from_ratelimit_headers({})
    assert gq["grok_quota_snapshot_state"] == "unknown_until_first_response"
    assert gq["grok_request_quota"] is None


def test_public_and_reexport_same_callables():
    assert pub_parse is parse_billing_payload
    assert pub_merge is merge_billing_snapshots
    assert pub_gq is grok_quota_from_ratelimit_headers
    # probe_quota re-exports for historical imports
    assert re_parse is parse_billing_payload
    assert re_merge is merge_billing_snapshots
    assert re_gq is grok_quota_from_ratelimit_headers


def test_oauth_package_has_probe_billing():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (root / "grokreg" / "oauth" / "probe_billing.py").is_file()
