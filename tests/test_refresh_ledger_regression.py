"""Real regressions for ledger-first RT→AT refresh.

Catches the two bugs we hit in ops:
1) CLI still passing cpa_dir into refresh_entry → TypeError at runtime
2) refresh writing only ``expired`` ISO while status_row prefers ``expires_at``
   → left_h stuck on the pre-refresh deadline

No network. refresh_tokens is monkeypatched.
"""
from __future__ import annotations

import ast
import inspect
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _cmd_refresh_source() -> str:
    src = (ROOT / "grokreg" / "ops" / "credential_cmds.py").read_text(encoding="utf-8")
    i = src.find("def _cmd_refresh")
    assert i > 0, "_cmd_refresh missing"
    # next top-level def (column 0) ends the function
    j = i + 1
    while True:
        j = src.find("\ndef ", j)
        if j < 0:
            return src[i:]
        # only stop at module-level defs
        if src[j + 1 : j + 5] == "def " and (j == 0 or src[j - 1] == "\n"):
            # check indentation of "def" — after \n
            return src[i:j]
        j += 1


def test_refresh_entry_signature_has_no_cpa_sidecar():
    from grokreg.auth_pool import refresh_entry

    params = inspect.signature(refresh_entry).parameters
    assert "cpa_dir" not in params
    assert "also_write_cpa" not in params
    assert "out_dir" not in params
    # required surface
    assert "proxy" in params
    assert "probe_mode" in params


def test_cli_refresh_entry_call_kwargs_match_signature():
    """AST: every refresh_entry(...) keyword in _cmd_refresh must be a real param.

    This is the regression for:
      TypeError: refresh_entry() got an unexpected keyword argument 'cpa_dir'
    String greps miss reintroductions via aliases; AST + signature does not.
    """
    from grokreg.auth_pool import refresh_entry

    allowed = set(inspect.signature(refresh_entry).parameters)
    body = _cmd_refresh_source()
    tree = ast.parse(body)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        if name == "refresh_entry":
            calls.append(node)
    assert calls, "expected refresh_entry call inside _cmd_refresh"

    banned = {"cpa_dir", "also_write_cpa", "out_dir", "cpa_path", "write_cpa"}
    for call in calls:
        for kw in call.keywords:
            if kw.arg is None:
                continue  # **kwargs unpack — rare; still forbid known banned names in constants
            assert kw.arg in allowed, f"illegal kw {kw.arg!r} not in refresh_entry{inspect.signature(refresh_entry)}"
            assert kw.arg not in banned, f"banned sidecar kw reintroduced: {kw.arg}"


def test_cli_refresh_has_no_cpa_out_dir_binding():
    """Dead out_dir=cpa_export was the source of the illegal cpa_dir= pass-through."""
    body = _cmd_refresh_source()
    # allow comments mentioning cpa_dir; forbid the assign that fed the bug
    assert "out_dir = Path(args.mint_out_dir" not in body
    assert "cpa_dir=" not in body


def test_refresh_ok_syncs_expires_at_status_and_tokens(tmp_path, monkeypatch):
    """Happy path: AT/RT rotate; expires_at is primary and status_row tracks it."""
    import grokreg.auth_pool as ap
    import grokreg.oauth as cpa_mod

    auth = tmp_path / "auth.json"
    email = "sync@example.com"
    old_exp = 1_700_000_000.0
    new_exp = 1_800_000_000
    key = ap.upsert(
        auth,
        {
            "email": email,
            "access_token": "old_at",
            "refresh_token": "rt_old",
            "expires_at": old_exp,
            "expires_in": 21600,
            "type": "xai",
            "auth_kind": "oauth",
        },
    )

    def _fake(**_kw):
        return {
            "ok": True,
            "email": email,
            "access_token": "new_at",
            "refresh_token": "rt_new",
            "id_token": "id_new",
            "exp_old": int(old_exp),
            "exp_new": new_exp,
            "expires_in": 21600,
        }

    monkeypatch.setattr(cpa_mod, "refresh_tokens", _fake)
    entry = ap.load_pool(auth)[key]
    r = ap.refresh_entry(auth, key, entry, proxy="http://127.0.0.1:9", probe_mode="none")
    assert r["ok"] is True

    saved = ap.load_pool(auth)[key]
    assert saved["access_token"] == "new_at"
    assert saved["key"] == "new_at"
    assert saved["refresh_token"] == "rt_new"
    assert saved["id_token"] == "id_new"
    assert float(saved["expires_at"]) == float(new_exp)
    assert saved["expires_in"] == 21600
    assert isinstance(saved.get("expired"), str) and "T" in saved["expired"]
    assert saved.get("last_refresh")

    # _parse_exp prefers expires_at — this is the bug surface
    assert ap._parse_exp(saved) == float(new_exp)
    row = ap.status_row(key, saved, skew_sec=300)
    assert row["expires_at"] == float(new_exp)
    assert row["state"] == "fresh"
    # must NOT still report life relative to old_exp
    assert row["left_h"] is not None
    assert row["left_h"] > 0
    # rough: new_exp is year-ish 2027; left_h huge
    assert row["left_h"] > 1000


def test_refresh_ok_expires_in_only_updates_expires_at(tmp_path, monkeypatch):
    """When server omits exp_new, expires_in alone must still advance expires_at."""
    import grokreg.auth_pool as ap
    import grokreg.oauth as cpa_mod

    auth = tmp_path / "auth.json"
    email = "ttl-only@example.com"
    old_exp = time.time() - 3600  # already stale
    key = ap.upsert(
        auth,
        {
            "email": email,
            "access_token": "old_at",
            "refresh_token": "rt",
            "expires_at": old_exp,
            "type": "xai",
            "auth_kind": "oauth",
        },
    )
    ttl = 21600

    def _fake(**_kw):
        return {
            "ok": True,
            "email": email,
            "access_token": "new_at",
            "refresh_token": "rt",
            "expires_in": ttl,
            # no exp_new / exp_old
        }

    monkeypatch.setattr(cpa_mod, "refresh_tokens", _fake)
    before = time.time()
    entry = ap.load_pool(auth)[key]
    r = ap.refresh_entry(auth, key, entry, proxy="http://127.0.0.1:9", probe_mode="none")
    after = time.time()
    assert r["ok"] is True
    saved = ap.load_pool(auth)[key]
    exp = float(saved["expires_at"])
    assert before + ttl - 2 <= exp <= after + ttl + 2
    assert saved["expires_in"] == ttl
    row = ap.status_row(key, saved, skew_sec=300)
    assert row["state"] == "fresh"
    assert abs(row["left_h"] - ttl / 3600) < 0.05


def test_refresh_fail_does_not_clobber_ledger(tmp_path, monkeypatch):
    import grokreg.auth_pool as ap
    import grokreg.oauth as cpa_mod

    auth = tmp_path / "auth.json"
    email = "fail@example.com"
    old_exp = 1_777_000_000.0
    key = ap.upsert(
        auth,
        {
            "email": email,
            "access_token": "keep_at",
            "refresh_token": "keep_rt",
            "expires_at": old_exp,
            "expires_in": 21600,
            "type": "xai",
            "auth_kind": "oauth",
        },
    )

    def _fake(**_kw):
        return {"ok": False, "email": email, "error": "invalid_grant revoked"}

    monkeypatch.setattr(cpa_mod, "refresh_tokens", _fake)
    entry = ap.load_pool(auth)[key]
    r = ap.refresh_entry(auth, key, entry, proxy="http://127.0.0.1:9", probe_mode="none")
    assert r.get("ok") is False

    saved = ap.load_pool(auth)[key]
    assert saved["access_token"] == "keep_at"
    assert saved["refresh_token"] == "keep_rt"
    assert float(saved["expires_at"]) == old_exp


def test_refresh_no_rt_short_circuits_without_network(tmp_path, monkeypatch):
    import grokreg.auth_pool as ap
    import grokreg.oauth as cpa_mod

    called = {"n": 0}

    def _boom(**_kw):
        called["n"] += 1
        raise AssertionError("refresh_tokens must not be called without RT")

    monkeypatch.setattr(cpa_mod, "refresh_tokens", _boom)
    auth = tmp_path / "auth.json"
    key = ap.upsert(
        auth,
        {
            "email": "nort@example.com",
            "access_token": "at",
            "refresh_token": "",
            "expires_at": time.time() + 1000,
            "type": "xai",
            "auth_kind": "oauth",
        },
    )
    entry = ap.load_pool(auth)[key]
    r = ap.refresh_entry(auth, key, entry, proxy="http://127.0.0.1:9", probe_mode="none")
    assert r["ok"] is False
    assert "refresh_token" in (r.get("error") or "").lower() or "no refresh" in (r.get("error") or "").lower()
    assert called["n"] == 0


def test_parse_exp_prefers_expires_at_over_expired_field():
    """Documents the field priority that made the stale-left_h bug possible."""
    from grokreg.auth_pool import _parse_exp

    primary = 1_800_000_000.0
    entry = {
        "expires_at": primary,
        "expired": "2020-01-01T00:00:00Z",  # would be wrong if preferred
    }
    assert _parse_exp(entry) == primary


def test_stale_expires_at_beats_fresh_expired_iso_until_refresh_syncs(tmp_path, monkeypatch):
    """Pre-fix symptom: expired ISO updated, expires_at stale → status stuck.

    Simulate the broken write shape, assert status is wrong; then run fixed
    refresh_entry and assert status recovers. Locks both the symptom and the fix.
    """
    import grokreg.auth_pool as ap
    import grokreg.oauth as cpa_mod

    auth = tmp_path / "auth.json"
    email = "stale-primary@example.com"
    stale = time.time() + 600  # 10 min left if trusted
    real_new = time.time() + 21600
    key = ap.upsert(
        auth,
        {
            "email": email,
            "access_token": "at",
            "refresh_token": "rt",
            "expires_at": stale,
            # post-broken-refresh shape: ISO advanced, primary not
            "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(real_new)),
            "type": "xai",
            "auth_kind": "oauth",
        },
    )
    entry = ap.load_pool(auth)[key]
    row_broken = ap.status_row(key, entry, skew_sec=300)
    # primary wins → reports ~0.17h not ~6h
    assert row_broken["left_h"] is not None
    assert row_broken["left_h"] < 1.0

    def _fake(**_kw):
        return {
            "ok": True,
            "email": email,
            "access_token": "at2",
            "refresh_token": "rt2",
            "exp_new": int(real_new),
            "expires_in": 21600,
        }

    monkeypatch.setattr(cpa_mod, "refresh_tokens", _fake)
    r = ap.refresh_entry(auth, key, entry, proxy="http://127.0.0.1:9", probe_mode="none")
    assert r["ok"] is True
    saved = ap.load_pool(auth)[key]
    row = ap.status_row(key, saved, skew_sec=300)
    assert float(saved["expires_at"]) == pytest.approx(float(int(real_new)), abs=1.0)
    assert row["left_h"] is not None
    assert row["left_h"] > 5.0
