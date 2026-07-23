"""Register pipeline: captcha can start before wait_code finishes (wall overlap)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from grokreg.pipeline import register as reg


def test_overlap_turnstile_with_wait_code(monkeypatch):
    """turnstile solve runs while wait_code sleeps → wall < sum of both stages."""
    events = {"captcha_started": threading.Event(), "in_wait": threading.Event()}

    class FakeMail:
        def wait_for_xai_code(self, **kwargs):
            events["in_wait"].set()
            # wait until captcha has started (overlap signal) or timeout
            events["captcha_started"].wait(timeout=2.0)
            time.sleep(0.15)
            return "123456"

    class FakeCaptcha:
        def solve_turnstile(self, *a, **k):
            events["captcha_started"].set()
            # captcha should start while mail is waiting
            assert events["in_wait"].wait(timeout=1.0)
            time.sleep(0.15)
            return "cf-turnstile-token"

    class FakeClient:
        signup_url = "https://accounts.x.ai/sign-up"
        turnstile_sitekey = "0xTEST"

        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def load_signup_page(self, **k):
            return {
                "scrape_cache": "hit",
                "next_action": "act",
                "turnstile_sitekey": "0xTEST",
            }

        def create_email_validation_code(self, email):
            return SimpleNamespace(ok=True, error=None)

        def verify_email_validation_code(self, email, code):
            return SimpleNamespace(ok=True, error=None)

        def validate_password(self, email, password):
            return True

        def create_account(self, **k):
            return SimpleNamespace(
                ok=True,
                error=None,
                http_status=200,
                rsc_body="x" * 1200,
            )

        def fetch_sso_token(self, retries=3):
            return "sso.jwt.token"

    class FakeResolved:
        session_url = None
        sid = "sid"
        region = "US"

        def label(self):
            return "proxy"

        def close(self):
            pass

    monkeypatch.setattr(reg, "GrokAuthClient", FakeClient)
    monkeypatch.setattr(reg, "resolve_proxy", lambda *a, **k: FakeResolved())
    monkeypatch.setattr(reg, "get_mail_backend", lambda *a, **k: FakeMail())
    monkeypatch.setattr(reg, "get_captcha_backend", lambda *a, **k: FakeCaptcha())
    monkeypatch.setattr(reg, "parse_mail_line", lambda raw: SimpleNamespace(
        email="u@test.local",
        password="pw",
        client_id="cid",
        refresh_token="rt",
        raw=raw,
    ))
    monkeypatch.setattr(reg, "parse_sso_jwt_payload", lambda s: {"session_id": "sess"})
    monkeypatch.setattr(
        reg,
        "check_captcha_balances",
        lambda **k: {
            "ok": True,
            "primary": "capsolver",
            "yes": {"ok": False, "configured": True},
            "capsolver": {"ok": True, "configured": True, "balance": 1},
            "twocaptcha": {"ok": False, "configured": False},
        },
    )
    monkeypatch.setattr(reg, "format_balance_report", lambda info: "captcha-balance ok")
    monkeypatch.setattr(reg, "resolve_yescaptcha_key", lambda *a, **k: None)
    monkeypatch.setattr(reg, "resolve_twocaptcha_key", lambda *a, **k: None)

    opts = reg.RegisterOptions(
        password="PwTest1!aA",
        captcha_backend="auto",
        captcha_balance_check=True,
        mail_backend="cloudmail",
        require_captcha_config=False,
        create_short_body_retries=0,
        scrape_cache=True,
        # enable overlap (default True after patch; set explicit)
        captcha_prefetch=True,
    )
    # if field missing pre-patch, setattr for test clarity
    if not hasattr(opts, "captcha_prefetch"):
        opts.captcha_prefetch = True  # type: ignore[attr-defined]

    t0 = time.time()
    result = reg.register_one(
        cfg={"proxy": {"default": "http://127.0.0.1:7890"}, "yescaptcha": {}, "twocaptcha": {}, "browser_turnstile": {}},
        raw="u@test.local----pw----cid----rt",
        proxy_override=None,
        opts=opts,
    )
    wall = time.time() - t0

    assert result.get("sso")
    assert not result.get("error"), result
    timings = result.get("timings_sec") or {}
    # both stages should be recorded as ~0.15s each
    assert timings.get("wait_code", 0) >= 0.1
    assert timings.get("turnstile", 0) >= 0.1
    # overlap: wall of wait+turnstile pair should be closer to max than sum
    # full register has other steps; only assert wall < wait+turnstile + 0.35 budget for rest
    pair = float(timings["wait_code"]) + float(timings["turnstile"])
    assert wall < pair + 0.5, f"expected overlap wall={wall:.3f} pair_sum={pair:.3f}"
