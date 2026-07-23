# -*- coding: utf-8 -*-
"""Tests for auto-refresh bot (no real network refresh)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import client.refresh_bot as rb


def test_clamp_interval_and_jobs():
    c = rb._clamp_config(
        {
            "enabled": True,
            "interval_min": 1,
            "jobs": 99,
            "limit": -3,
        }
    )
    assert c["interval_min"] >= 5
    assert c["jobs"] <= 8
    assert c["limit"] == 0
    assert c["enabled"] is True


def test_save_load_roundtrip(tmp_path, monkeypatch):
    store = tmp_path / "refresh_bot.json"
    monkeypatch.setattr(rb, "STORE_PATH", store)
    bot = rb.RefreshBot()
    bot.save_config(enabled=False, interval_min=15, jobs=3, limit=50)
    assert store.is_file()
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["interval_min"] == 15
    assert data["jobs"] == 3
    assert data["limit"] == 50
    assert data["enabled"] is False


def test_tick_skips_when_no_needs(monkeypatch, tmp_path):
    store = tmp_path / "refresh_bot.json"
    monkeypatch.setattr(rb, "STORE_PATH", store)
    bot = rb.RefreshBot()
    bot.save_config(enabled=True, only_if_needs=True, skip_if_busy=True)

    monkeypatch.setattr(
        rb,
        "_auth_needs",
        lambda: {
            "total": 10,
            "fresh": 10,
            "needs_refresh": 0,
            "expired": 0,
            "quota_exhausted": 0,
        },
    )

    class FakeMgr:
        def get_status(self):
            return {"running": False, "kind": ""}

        def start_refresh(self, **kw):
            raise AssertionError("should not start refresh")

        def append_log(self, *a, **k):
            pass

    monkeypatch.setattr("client.task_manager.manager", FakeMgr(), raising=False)
    # also patch import path inside tick
    import client.task_manager as tm

    monkeypatch.setattr(tm, "manager", FakeMgr())

    out = bot.tick(source="test")
    assert out["action"] == "skipped_no_needs"


def test_tick_skips_when_busy(monkeypatch, tmp_path):
    store = tmp_path / "refresh_bot.json"
    monkeypatch.setattr(rb, "STORE_PATH", store)
    bot = rb.RefreshBot()
    bot.save_config(enabled=True, only_if_needs=True, skip_if_busy=True)
    monkeypatch.setattr(
        rb,
        "_auth_needs",
        lambda: {
            "total": 10,
            "fresh": 5,
            "needs_refresh": 3,
            "expired": 0,
            "quota_exhausted": 0,
        },
    )

    class BusyMgr:
        def get_status(self):
            return {"running": True, "kind": "register"}

        def start_refresh(self, **kw):
            raise AssertionError("busy should skip")

        def append_log(self, *a, **k):
            pass

    import client.task_manager as tm

    monkeypatch.setattr(tm, "manager", BusyMgr())
    out = bot.tick(source="test")
    assert out["action"] == "skipped_busy"


def test_tick_runs_refresh(monkeypatch, tmp_path):
    store = tmp_path / "refresh_bot.json"
    monkeypatch.setattr(rb, "STORE_PATH", store)
    bot = rb.RefreshBot()
    bot.save_config(
        enabled=True,
        only_if_needs=True,
        skip_if_busy=True,
        jobs=2,
        limit=10,
        needs_only=True,
        remint_on_revoke=True,
    )
    monkeypatch.setattr(
        rb,
        "_auth_needs",
        lambda: {
            "total": 10,
            "fresh": 5,
            "needs_refresh": 3,
            "expired": 0,
            "quota_exhausted": 0,
        },
    )
    called = {}

    class OkMgr:
        def get_status(self):
            return {"running": False, "kind": ""}

        def start_refresh(self, **kw):
            called.update(kw)
            return {"ok": True, "run_id": "deadbeef"}

        def append_log(self, *a, **k):
            pass

    import client.task_manager as tm

    monkeypatch.setattr(tm, "manager", OkMgr())
    out = bot.tick(source="test")
    assert out["action"] == "ran"
    assert called.get("jobs") == 2
    assert called.get("limit") == 10
    assert called.get("needs_only") is True


def test_ledger_html_has_bot_card():
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    assert "ledger-bot-card" in html
    assert "bot-enabled" in html
    assert "saveRefreshBot" in html

def test_frontend_refresh_bot_body_is_object_not_double_stringify():
    """api() already JSON.stringifies; save/run-once must pass objects."""
    main = Path("client/static/js/main.js").read_text(encoding="utf-8")
    app = Path("client/static/app.js").read_text(encoding="utf-8")
    for src, label in ((main, "main.js"), (app, "app.js")):
        assert 'body: JSON.stringify(body)' not in src, label
        assert 'api("/api/refresh-bot", { method: "POST", body: body })' in src, label
        assert 'api("/api/refresh-bot/run-once", { method: "POST", body: {} })' in src, label

