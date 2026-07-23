"""Mint: direct device/approve hot path (skip consent HTML / CF)."""
from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

from grokreg.oauth import constants as C

mint_mod = importlib.import_module("grokreg.oauth.mint")


def _wire_publish(monkeypatch):
    import grokreg.backends.export as exp
    import grokreg.backends.export.xai_pack.schema as schema

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


def _base_fakes(monkeypatch, FakeSession):
    C.clear_device_code_throttle()

    def fake_dc(proxy, max_attempts=6, session=None):
        return {"user_code": "UC1", "device_code": "DC1", "interval": 0.3}

    monkeypatch.setattr(mint_mod, "_device_code", fake_dc)
    monkeypatch.setattr(mint_mod, "set_sso", lambda *a, **k: None)
    monkeypatch.setattr(
        mint_mod,
        "_poll_token",
        lambda *a, **k: {
            "ok": True,
            "token": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
            },
        },
    )
    monkeypatch.setattr(mint_mod, "_post_write_probe", lambda *a, **k: {})
    monkeypatch.setattr(mint_mod, "_resolve_probe_mode", lambda *a, **k: "none")
    monkeypatch.setattr(mint_mod.time, "sleep", lambda s: None)

    fake_cr = types.ModuleType("curl_cffi")
    fake_req = types.ModuleType("curl_cffi.requests")
    fake_req.Session = FakeSession
    fake_cr.requests = fake_req
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_cr)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_req)
    _wire_publish(monkeypatch)


class _Resp:
    def __init__(self, *, status=200, headers=None, text="", url=""):
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self.url = url


def test_mint_direct_approve_skips_consent_get(monkeypatch):
    """Hot path: verify → approve(done); never GET consent HTML."""
    calls = {"get": 0, "post": [], "approve_body": None}

    class FakeSession:
        def __init__(self, *a, **k):
            self.cookies = MagicMock()

        def get(self, url, **k):
            calls["get"] += 1
            return _Resp(
                status=403,
                text="<title>Attention Required! | Cloudflare</title>",
                url=url,
            )

        def post(self, url, **k):
            u = str(url)
            calls["post"].append(u)
            if "verify" in u:
                return _Resp(
                    status=303,
                    headers={
                        "Location": "https://accounts.x.ai/oauth2/device/consent?user_code=UC1"
                    },
                )
            if "approve" in u:
                calls["approve_body"] = k.get("data")
                return _Resp(
                    status=303,
                    headers={"Location": "https://accounts.x.ai/oauth2/device/done"},
                )
            return _Resp(status=400)

        def close(self):
            pass

    _base_fakes(monkeypatch, FakeSession)
    r = mint_mod.mint(
        "a@x.com",
        "sso",
        proxy="http://127.0.0.1:7890",
        auth_path=None,
        packs=[],
        probe_mode="none",
    )
    assert r.get("ok") is True
    assert calls["get"] == 0, "consent GET must be skipped on hot path"
    assert any("approve" in p for p in calls["post"])
    body = calls["approve_body"] or {}
    assert body.get("action") == "allow"
    assert body.get("user_code") == "UC1"


def test_mint_approve_fallback_form_when_direct_misses(monkeypatch):
    """If direct approve misses done, scrape consent form and re-POST."""
    calls = {"get": 0, "approve_n": 0}

    class FakeSession:
        def __init__(self, *a, **k):
            self.cookies = MagicMock()

        def get(self, url, **k):
            calls["get"] += 1
            assert "consent" in str(url)
            return _Resp(
                status=200,
                text=(
                    '<form action="https://auth.x.ai/oauth2/device/approve">'
                    '<input name="user_code" value="UC1">'
                    '<input name="csrf" value="tok">'
                    "</form>"
                ),
                url=url,
            )

        def post(self, url, **k):
            u = str(url)
            if "verify" in u:
                return _Resp(
                    status=303,
                    headers={"Location": "https://accounts.x.ai/oauth2/consent?x=1"},
                )
            if "approve" in u:
                calls["approve_n"] += 1
                # first (direct) fails; second (form) succeeds
                if calls["approve_n"] == 1:
                    return _Resp(status=400, text="nope")
                return _Resp(
                    status=303,
                    headers={"Location": "https://accounts.x.ai/oauth2/device/done"},
                )
            return _Resp(status=400)

        def close(self):
            pass

    _base_fakes(monkeypatch, FakeSession)
    r = mint_mod.mint(
        "a@x.com",
        "sso",
        proxy="http://127.0.0.1:7890",
        auth_path=None,
        packs=[],
        probe_mode="none",
    )
    assert r.get("ok") is True
    assert calls["get"] == 1
    assert calls["approve_n"] == 2


def test_mint_approve_redirect_error_when_no_done(monkeypatch):
    from grokreg.errors import MintError

    class FakeSession:
        def __init__(self, *a, **k):
            self.cookies = MagicMock()

        def get(self, url, **k):
            return _Resp(
                status=403,
                text="<title>Attention Required! | Cloudflare</title> cloudflare challenge",
                url=url,
            )

        def post(self, url, **k):
            if "verify" in str(url):
                return _Resp(
                    status=303,
                    headers={"Location": "https://accounts.x.ai/oauth2/consent?x=1"},
                )
            # approve never done; consent form missing → mint_consent_form
            return _Resp(status=403, text="blocked")

        def close(self):
            pass

    _base_fakes(monkeypatch, FakeSession)
    try:
        mint_mod.mint(
            "a@x.com",
            "sso",
            proxy="http://127.0.0.1:7890",
            auth_path=None,
            packs=[],
            probe_mode="none",
        )
        raise AssertionError("expected MintError")
    except MintError as e:
        assert e.code == "mint_consent_form"
        msg = str(e)
        assert "cf=1" in msg or "Cloudflare" in msg
        assert "status=403" in msg
