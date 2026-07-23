"""B-model: credential state vs probe_class are separate."""
from __future__ import annotations

import json

from grokreg.auth_pool import set_probe_result, status_row


def test_quota_does_not_overwrite_fresh_state():
    entry = {
        "email": "a@x.com",
        "access_token": "at",
        "refresh_token": "rt",
        "expires_at": 9999999999,
        "probe": {
            "class": "quota_exhausted",
            "mode": "chat",
            "at": 1700000000,
        },
    }
    row = status_row("k1", entry)
    assert row["state"] == "fresh"
    assert row["probe_class"] == "quota_exhausted"
    assert row["needs_refresh"] is False


def test_needs_and_quota_can_coexist():
    entry = {
        "email": "b@x.com",
        "access_token": "at",
        "refresh_token": "rt",
        # already expired
        "expires_at": 1,
        "probe": {"class": "quota_exhausted", "mode": "chat", "at": 1},
    }
    row = status_row("k2", entry)
    assert row["state"] == "expired"
    assert row["probe_class"] == "quota_exhausted"
    assert row["needs_refresh"] is True


def test_set_probe_and_status_split(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "k": {
                    "email": "c@x.com",
                    "access_token": "SECRET_AT",
                    "refresh_token": "SECRET_RT",
                    "expires_at": 9999999999,
                }
            }
        ),
        encoding="utf-8",
    )
    assert set_probe_result(auth, "c@x.com", classification="quota_exhausted", mode="chat")
    data = json.loads(auth.read_text(encoding="utf-8"))
    entry = next(iter(data.values()))
    assert entry["access_token"] == "SECRET_AT"
    row = status_row("k", entry)
    assert row["state"] == "fresh"
    assert row["probe_class"] == "quota_exhausted"


def test_ledger_filter_probe_independent(tmp_path, monkeypatch):
    from client import services as svc

    monkeypatch.setattr(svc, "ROOT", tmp_path)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "k1": {
                    "email": "q@x.com",
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_at": 9999999999,
                    "probe": {"class": "quota_exhausted", "mode": "chat", "at": 1},
                },
                "k2": {
                    "email": "ok@x.com",
                    "access_token": "at2",
                    "refresh_token": "rt2",
                    "expires_at": 9999999999,
                    "probe": {"class": "healthy", "mode": "chat", "at": 1},
                },
            }
        ),
        encoding="utf-8",
    )
    out = svc.list_auth_ledger(limit=50, filter_probe="quota_exhausted")
    emails = {r["email"] for r in out["items"]}
    assert emails == {"q@x.com"}
    assert out["items"][0]["state"] == "fresh"
    assert out["items"][0]["probe_class"] == "quota_exhausted"
