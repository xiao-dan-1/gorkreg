"""Adaptive device/code spacing after HTTP 429 (token-bucket style)."""
from __future__ import annotations

import time

from grokreg.oauth import constants as C
from grokreg.oauth import device as dev


def test_note_429_raises_adaptive_interval(monkeypatch):
    monkeypatch.delenv("GROK_DEVICE_CODE_MIN_INTERVAL", raising=False)
    C.clear_device_code_throttle()
    assert C._device_code_min_interval() == 0.0
    C.note_device_code_429()
    iv = C._device_code_min_interval()
    assert iv >= 0.25, iv
    # second 429 raises further (capped)
    C.note_device_code_429()
    iv2 = C._device_code_min_interval()
    assert iv2 >= iv
    assert iv2 <= 2.0


def test_note_success_decays_throttle(monkeypatch):
    monkeypatch.delenv("GROK_DEVICE_CODE_MIN_INTERVAL", raising=False)
    C.clear_device_code_throttle()
    C.note_device_code_429()
    C.note_device_code_429()
    high = C._device_code_min_interval()
    for _ in range(20):
        C.note_device_code_success()
    low = C._device_code_min_interval()
    assert low < high or low == 0.0


def test_device_code_uses_adaptive_on_429(monkeypatch):
    """After 429 response, adaptive interval becomes positive."""
    monkeypatch.delenv("GROK_DEVICE_CODE_MIN_INTERVAL", raising=False)
    C.clear_device_code_throttle()

    calls = {"n": 0}

    class FakeResp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeOpener:
        def open(self, req, timeout=30):
            calls["n"] += 1
            if calls["n"] == 1:
                import urllib.error

                raise urllib.error.HTTPError(
                    url="https://auth.x.ai/oauth2/device/code",
                    code=429,
                    msg="rate",
                    hdrs=None,
                    fp=None,
                )
            return FakeResp(b'{"device_code":"dc","user_code":"UC","interval":1}')

    class FakeBuild:
        def __call__(self, *a, **k):
            return FakeOpener()

    monkeypatch.setattr(dev.urllib.request, "build_opener", FakeBuild())
    monkeypatch.setattr(dev.urllib.request, "ProxyHandler", lambda *a, **k: object())
    # speed up sleep
    sleeps = []
    monkeypatch.setattr(dev.time, "sleep", lambda s: sleeps.append(s))

    body = dev._device_code("http://127.0.0.1:10808", max_attempts=3)
    assert body.get("device_code") == "dc"
    assert C._device_code_min_interval() > 0
    assert any(s >= 1.0 for s in sleeps)  # 429 backoff
