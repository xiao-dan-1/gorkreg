"""Mint efficiency R3: compact auth upsert + device/code on shared session."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock


def test_upsert_writes_compact_json(tmp_path: Path):
    """auth.json write should be compact (no indent=2) for large-pool I/O."""
    from grokreg.auth_pool import load_pool, upsert

    path = tmp_path / "auth.json"
    # seed a few entries
    for i in range(5):
        upsert(
            path,
            {
                "email": f"u{i}@x.com",
                "refresh_token": "r" * 20,
                "access_token": "a" * 20,
                "type": "xai",
            },
        )
    raw = path.read_text(encoding="utf-8")
    # compact: should not look like pretty-printed multi-indent blocks
    assert "\n  \"" not in raw and '": {' not in raw or raw.count("\n") < 30
    # still valid
    data = load_pool(path)
    assert len(data) == 5


def test_upsert_single_lock_write_fast_enough(tmp_path: Path):
    """Writing ~200 entries repeatedly should stay local-fast (no multi-MB pretty)."""
    from grokreg.auth_pool import upsert

    path = tmp_path / "auth.json"
    # build pool
    for i in range(100):
        upsert(
            path,
            {
                "email": f"u{i}@x.com",
                "refresh_token": "r" * 80,
                "access_token": "a" * 80,
                "type": "xai",
            },
        )
    t0 = time.perf_counter()
    for i in range(20):
        upsert(
            path,
            {
                "email": f"new{i}@x.com",
                "refresh_token": "r" * 80,
                "access_token": "a" * 80,
                "type": "xai",
            },
        )
    elapsed = time.perf_counter() - t0
    # 20 upserts on ~100 entry pool should be << 2s on local disk
    assert elapsed < 2.0, f"upsert too slow: {elapsed:.3f}s"


def test_device_code_uses_session_post_when_provided(monkeypatch):
    from grokreg.oauth import constants as C
    from grokreg.oauth import device as dev

    C.clear_device_code_throttle()
    posts = {"n": 0}

    class Sess:
        def post(self, url, **k):
            posts["n"] += 1
            assert "device/code" in str(url)
            return MagicMock(
                status_code=200,
                text='{"device_code":"dc","user_code":"UC","interval":1}',
                json=lambda: {
                    "device_code": "dc",
                    "user_code": "UC",
                    "interval": 1,
                },
            )

    body = dev._device_code("http://127.0.0.1:9", session=Sess())
    assert body["device_code"] == "dc"
    assert posts["n"] == 1


def test_mint_passes_session_to_device_code(monkeypatch):
    import importlib
    import sys
    import types

    mint_mod = importlib.import_module("grokreg.oauth.mint")
    C = importlib.import_module("grokreg.oauth.constants")
    C.clear_device_code_throttle()
    seen = {}

    def fake_dc(proxy, max_attempts=6, session=None):
        seen["session"] = session
        return {"user_code": "UC", "device_code": "DC", "interval": 0.3}

    def fake_poll(proxy, device_code, timeout_sec=90, interval_sec=None, session=None):
        return {
            "ok": True,
            "token": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
            },
        }

    class FakeResp:
        def __init__(self, *, status=200, headers=None, text="", url=""):
            self.status_code = status
            self.headers = headers or {}
            self.text = text
            self.url = url

    class FakeSession:
        def __init__(self, *a, **k):
            self.cookies = MagicMock()

        def get(self, url, **k):
            return FakeResp(
                text='<form action="https://auth.x.ai/oauth2/device/approve"><input name="a" value="1"></form>',
                url=url,
            )

        def post(self, url, **k):
            if "verify" in str(url):
                return FakeResp(
                    status=303,
                    headers={"Location": "https://accounts.x.ai/oauth2/consent?x=1"},
                )
            return FakeResp(status=303, headers={"Location": "https://x/done"})

        def close(self):
            pass

    monkeypatch.setattr(mint_mod, "_device_code", fake_dc)
    monkeypatch.setattr(mint_mod, "_poll_token", fake_poll)
    monkeypatch.setattr(mint_mod, "set_sso", lambda *a, **k: None)
    monkeypatch.setattr(mint_mod, "_post_write_probe", lambda *a, **k: {})
    monkeypatch.setattr(mint_mod, "_resolve_probe_mode", lambda *a, **k: "none")

    fake_cr = types.ModuleType("curl_cffi")
    fake_req = types.ModuleType("curl_cffi.requests")
    fake_req.Session = FakeSession
    fake_cr.requests = fake_req
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_cr)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_req)

    import grokreg.backends.export.xai_pack.schema as schema
    import grokreg.backends.export as exp

    monkeypatch.setattr(
        schema,
        "build_xai_auth",
        lambda **k: {
            "email": k.get("email"),
            "access_token": "at",
            "refresh_token": "rt",
            "type": "xai",
        },
    )
    monkeypatch.setattr(
        exp,
        "publish_credentials",
        lambda *a, **k: {"pool_key": "pk", "path": None, "paths": []},
    )

    r = mint_mod.mint(
        "a@x.com",
        "sso",
        proxy="http://127.0.0.1:7890",
        auth_path=None,
        packs=[],
        probe_mode="none",
    )
    assert r.get("ok") is True
    assert seen.get("session") is not None
