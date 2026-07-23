"""mint(): rate_limited on device/verify feeds adaptive throttle + retry."""
from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

from grokreg.oauth import constants as C

# oauth/__init__ may export mint() and shadow the submodule name — load explicitly
mint_mod = importlib.import_module("grokreg.oauth.mint")


def test_mint_verify_rate_limited_retries_then_ok(monkeypatch):
    C.clear_device_code_throttle()
    calls = {"dc": 0, "get": 0, "post": 0}

    def fake_dc(proxy, max_attempts=6, session=None):
        calls["dc"] += 1
        return {
            "user_code": f"UC{calls['dc']}",
            "device_code": f"DC{calls['dc']}",
            "interval": 1,
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
            calls["get"] += 1
            if "consent" in str(url):
                return FakeResp(
                    status=200,
                    text=(
                        '<form action="https://auth.x.ai/oauth2/device/approve">'
                        '<input name="user_code" value="UC">'
                        '<input name="action" value="">'
                        "</form>"
                    ),
                    url=url,
                )
            return FakeResp(
                status=200,
                text='<html><form action="/oauth2/device/verify" method="post"></form></html>',
                url=url,
            )

        def post(self, url, **k):
            calls["post"] += 1
            if "verify" in str(url):
                if calls["post"] == 1:
                    return FakeResp(
                        status=303,
                        headers={
                            "Location": "https://accounts.x.ai/oauth2/device?error=rate_limited"
                        },
                    )
                return FakeResp(
                    status=303,
                    headers={"Location": "https://accounts.x.ai/oauth2/consent?x=1"},
                )
            return FakeResp(
                status=303,
                headers={"Location": "https://accounts.x.ai/oauth2/device/done"},
            )

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
    monkeypatch.setattr(mint_mod.time, "sleep", lambda s: None)

    fake_curl = types.ModuleType("curl_cffi")
    fake_requests = types.ModuleType("curl_cffi.requests")
    fake_requests.Session = FakeSession
    fake_curl.requests = fake_requests
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_curl)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_requests)

    if hasattr(mint_mod, "_post_write_probe"):
        monkeypatch.setattr(
            mint_mod, "_post_write_probe", lambda *a, **k: {"ok": True, "mode": "none"}
        )
    if hasattr(mint_mod, "_resolve_probe_mode"):
        monkeypatch.setattr(mint_mod, "_resolve_probe_mode", lambda *a, **k: "none")

    result = mint_mod.mint(
        "t@test.local",
        "sso.jwt.token",
        proxy="http://127.0.0.1:10808",
        auth_path=None,
        packs=None,
        probe_mode="none",
    )
    assert calls["dc"] >= 2, calls
    assert C._device_code_min_interval() > 0 or calls["post"] >= 2
    assert result is not None
