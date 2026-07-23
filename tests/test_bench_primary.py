"""bench primary_factor prefers stage cost sum, not unstable mode of top_stage."""
from __future__ import annotations

from grokreg.bench import build_run_record, top_factor


def test_top_factor_picks_max_stage():
    stage, sec = top_factor({"scrape": 0.0, "turnstile": 11.7, "wait_code": 1.2, "sso": 3.1})
    assert stage == "turnstile"
    assert sec == 11.7


def test_primary_factor_uses_stage_sum_not_mode():
    """Two accounts top=sso, two top=turnstile but turnstile sum larger → primary turnstile."""
    results = [
        {
            "email": "a@x",
            "sso": "1",
            "elapsed_sec": 12,
            "timings_sec": {"turnstile": 2.4, "wait_code": 1.2, "sso": 3.3},
        },
        {
            "email": "b@x",
            "sso": "1",
            "elapsed_sec": 16,
            "timings_sec": {"turnstile": 2.2, "wait_code": 1.2, "sso": 4.7},
        },
        {
            "email": "c@x",
            "sso": "1",
            "elapsed_sec": 17,
            "timings_sec": {"turnstile": 6.5, "wait_code": 1.4, "sso": 3.5},
        },
        {
            "email": "d@x",
            "sso": "1",
            "elapsed_sec": 21,
            "timings_sec": {"turnstile": 11.8, "wait_code": 1.2, "sso": 3.1},
        },
    ]
    rec = build_run_record(
        results=results,
        jobs=2,
        mail_backend="cloudmail",
        wall_sec=39.0,
        ok=4,
        fail=0,
    )
    assert "turnstile" in str(rec["primary_factor"]).lower()
    # stage share should list turnstile as largest
    share = rec["stage_share_pct"]
    assert share.get("turnstile", 0) >= share.get("sso", 0)


def test_primary_factor_fail_batch_uses_fail_bucket():
    results = [
        {
            "email": "f@x",
            "sso": "",
            "error": "mail timeout",
            "error_code": "mail_timeout",
            "elapsed_sec": 120,
            "timings_sec": {"wait_code": 120},
        }
    ]
    rec = build_run_record(
        results=results,
        jobs=1,
        mail_backend="cloudmail",
        wall_sec=120,
        ok=0,
        fail=1,
    )
    assert "mail" in str(rec["primary_factor"]).lower() or "fail" in str(rec["primary_factor"]).lower()
