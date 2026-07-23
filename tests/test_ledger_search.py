"""Ledger email search must find existing accounts."""
from __future__ import annotations

import json


def test_ledger_search_substring_and_tokens(tmp_path, monkeypatch):
    from client import services as svc

    monkeypatch.setattr(svc, "ROOT", tmp_path)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "k1": {
                    "email": "alice.smith@bj01.xdauv.xyz",
                    "access_token": "at1",
                    "refresh_token": "rt1",
                    "expires_at": 9999999999,
                },
                "k2": {
                    "email": "bob@outlook.com",
                    "access_token": "at2",
                    "refresh_token": "rt2",
                    "expires_at": 9999999999,
                },
            }
        ),
        encoding="utf-8",
    )
    # full
    out = svc.list_auth_ledger(q="alice.smith@bj01.xdauv.xyz")
    assert out["matched"] == 1
    assert out["items"][0]["email"].startswith("alice")
    # local part
    out = svc.list_auth_ledger(q="alice")
    assert out["matched"] == 1
    # domain fragment
    out = svc.list_auth_ledger(q="bj01")
    assert out["matched"] == 1
    # multi-token AND
    out = svc.list_auth_ledger(q="alice bj01")
    assert out["matched"] == 1
    out = svc.list_auth_ledger(q="alice outlook")
    assert out["matched"] == 0
    # case insensitive
    out = svc.list_auth_ledger(q="BOB")
    assert out["matched"] == 1
