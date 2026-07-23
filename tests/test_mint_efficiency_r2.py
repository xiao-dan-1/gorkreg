"""Mint efficiency R2: session reuse, Retry-After, jittered 429 backoff."""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock


def test_poll_token_reuses_passed_session(monkeypatch):
    """Passing session= avoids creating a second curl Session (TLS tax)."""
    from grokreg.oauth import device as dev

    created = {"n": 0}
    posts = {"n": 0}

    class FakeS:
        def __init__(self, *a, **k):
            created["n"] += 1

        def post(self, *a, **k):
            posts["n"] += 1
            return MagicMock(
                status_code=200,
                text='{"access_token":"at","refresh_token":"rt","expires_in":1}',
                json=lambda: {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_in": 1,
                },
            )

        def close(self):
            pass

    import sys
    import types

    fake_cr = types.ModuleType("curl_cffi")
    fake_req = types.ModuleType("curl_cffi.requests")
    fake_req.Session = FakeS
    fake_cr.requests = fake_req
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_cr)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_req)

    owned = FakeS()
    assert created["n"] == 1
    r = dev._poll_token(
        "http://127.0.0.1:9",
        "dc",
        timeout_sec=10,
        interval_sec=0.2,
        session=owned,
    )
    assert r.get("ok") is True
    # no additional Session() when session= provided
    assert created["n"] == 1, f"extra Session created: {created['n']}"
    assert posts["n"] >= 1


def test_poll_token_closes_only_owned_session(monkeypatch):
    from grokreg.oauth import device as dev

    closed = {"owned": 0, "external": 0}

    class Owned:
        def __init__(self, *a, **k):
            pass

        def post(self, *a, **k):
            return MagicMock(
                status_code=200,
                text='{"access_token":"a","refresh_token":"r"}',
                json=lambda: {"access_token": "a", "refresh_token": "r"},
            )

        def close(self):
            closed["owned"] += 1

    class External:
        def post(self, *a, **k):
            return MagicMock(
                status_code=200,
                text='{"access_token":"a","refresh_token":"r"}',
                json=lambda: {"access_token": "a", "refresh_token": "r"},
            )

        def close(self):
            closed["external"] += 1

    import sys
    import types

    fake_cr = types.ModuleType("curl_cffi")
    fake_req = types.ModuleType("curl_cffi.requests")
    fake_req.Session = Owned
    fake_cr.requests = fake_req
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_cr)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_req)

    ext = External()
    dev._poll_token("p", "dc", timeout_sec=5, session=ext)
    assert closed["external"] == 0  # caller owns external session

    dev._poll_token("p", "dc", timeout_sec=5)  # owned path
    assert closed["owned"] == 1


def test_device_code_honors_retry_after_header(monkeypatch):
    """HTTP 429 Retry-After drives backoff (RFC / CDN common pattern)."""
    from grokreg.oauth import constants as C
    from grokreg.oauth import device as dev
    import urllib.error

    C.clear_device_code_throttle()
    sleeps: list[float] = []
    monkeypatch.setattr(dev.time, "sleep", lambda s: sleeps.append(float(s)))
    monkeypatch.delenv("GROK_DEVICE_CODE_MIN_INTERVAL", raising=False)

    n = {"i": 0}

    class FakeResp:
        def __init__(self, body: bytes):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=30):
        n["i"] += 1
        if n["i"] == 1:
            err = urllib.error.HTTPError(
                url="http://x", code=429, msg="rl", hdrs=None, fp=None
            )
            # headers-like
            err.headers = {"Retry-After": "2"}
            raise err
        return FakeResp(b'{"device_code":"dc","user_code":"UC","interval":1}')

    class Opener:
        def open(self, req, timeout=30):
            return fake_open(req, timeout=timeout)

    monkeypatch.setattr(dev.urllib.request, "build_opener", lambda *a, **k: Opener())
    monkeypatch.setattr(
        dev.urllib.request, "ProxyHandler", lambda *a, **k: object()
    )

    body = dev._device_code("http://127.0.0.1:9", max_attempts=3)
    assert body.get("device_code") == "dc"
    # first sleep should be ~2s from Retry-After (allow small jitter range 2..2.5)
    assert sleeps, "expected backoff sleep"
    assert 2.0 <= sleeps[0] <= 2.6, sleeps


def test_mint_passes_session_into_poll_token(monkeypatch):
    mint_mod = importlib.import_module("grokreg.oauth.mint")
    C = importlib.import_module("grokreg.oauth.constants")
    C.clear_device_code_throttle()

    poll_kw = {}

    def fake_dc(proxy, max_attempts=6, session=None):
        return {"user_code": "UC", "device_code": "DC", "interval": 0.3}

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
                text=(
                    '<form action="https://auth.x.ai/oauth2/device/approve">'
                    '<input name="user_code" value="UC"></form>'
                ),
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

    def fake_poll(proxy, device_code, timeout_sec=90, interval_sec=None, session=None):
        poll_kw["session"] = session
        poll_kw["interval_sec"] = interval_sec
        return {
            "ok": True,
            "token": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
            },
        }

    monkeypatch.setattr(mint_mod, "_device_code", fake_dc)
    monkeypatch.setattr(mint_mod, "set_sso", lambda *a, **k: None)
    monkeypatch.setattr(mint_mod, "_poll_token", fake_poll)
    monkeypatch.setattr(mint_mod, "_post_write_probe", lambda *a, **k: {})
    monkeypatch.setattr(mint_mod, "_resolve_probe_mode", lambda *a, **k: "none")

    import sys
    import types

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
    assert poll_kw.get("session") is not None, "mint must pass curl session to poll"
    assert poll_kw.get("interval_sec") == 0.3
