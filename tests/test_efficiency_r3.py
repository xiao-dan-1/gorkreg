"""R3 efficiency: adaptive mail poll + light jitter + captcha poll defaults."""
from __future__ import annotations

import inspect
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from grokreg.backends.captcha.impl import (
    CapSolverSolver,
    TwoCaptchaSolver,
    YesCaptchaSolver,
)
from grokreg.pipeline import register as reg


def test_mail_poll_cloudmail_default_half_second(monkeypatch):
    seen = {}

    class FakeMail:
        def wait_for_xai_code(self, **kwargs):
            seen["interval"] = kwargs.get("interval")
            return "123456"

    class FakeCaptcha:
        def solve_turnstile(self, *a, **k):
            return "cf-tok"

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
    monkeypatch.setattr(
        reg,
        "parse_mail_line",
        lambda raw: SimpleNamespace(
            email="u@test.local",
            password="pw",
            client_id="cid",
            refresh_token="rt",
            raw=raw,
        ),
    )
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
    monkeypatch.setattr(reg, "format_balance_report", lambda info: "ok")
    monkeypatch.setattr(reg, "resolve_yescaptcha_key", lambda *a, **k: None)
    monkeypatch.setattr(reg, "resolve_twocaptcha_key", lambda *a, **k: None)

    opts = reg.RegisterOptions(
        password="PwTest1!aA",
        captcha_backend="auto",
        captcha_balance_check=True,
        mail_backend="cloudmail",
        require_captcha_config=False,
        create_short_body_retries=0,
        captcha_prefetch=False,
    )
    result = reg.register_one(
        cfg={
            "proxy": {"default": "http://127.0.0.1:7890"},
            "yescaptcha": {},
            "twocaptcha": {},
            "browser_turnstile": {},
        },
        raw="u@test.local----pw----cid----rt",
        proxy_override=None,
        opts=opts,
    )
    assert result.get("sso")
    assert seen["interval"] == 0.5


def test_mail_poll_graph_default_two_seconds(monkeypatch):
    seen = {}

    class FakeMail:
        def wait_for_xai_code(self, **kwargs):
            seen["interval"] = kwargs.get("interval")
            return "123456"

    class FakeCaptcha:
        def solve_turnstile(self, *a, **k):
            return "cf-tok"

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
    monkeypatch.setattr(
        reg,
        "parse_mail_line",
        lambda raw: {
            "email": "u@test.local",
            "password": "pw",
            "client_id": "cid",
            "refresh_token": "rt",
            "raw": raw,
        },
    )
    monkeypatch.setattr(reg, "parse_sso_jwt_payload", lambda s: {"session_id": "sess"})
    monkeypatch.setattr(
        reg,
        "check_captcha_balances",
        lambda **k: {
            "ok": True,
            "primary": "capsolver",
            "yes": {"ok": False, "configured": False},
            "capsolver": {"ok": True, "configured": True, "balance": 1},
            "twocaptcha": {"ok": False, "configured": False},
        },
    )
    monkeypatch.setattr(reg, "format_balance_report", lambda info: "ok")
    monkeypatch.setattr(reg, "resolve_yescaptcha_key", lambda *a, **k: None)
    monkeypatch.setattr(reg, "resolve_twocaptcha_key", lambda *a, **k: None)

    opts = reg.RegisterOptions(
        password="PwTest1!aA",
        captcha_backend="auto",
        captcha_balance_check=True,
        mail_backend="graph",
        require_captcha_config=False,
        create_short_body_retries=0,
        captcha_prefetch=False,
    )
    result = reg.register_one(
        cfg={
            "proxy": {"default": "http://127.0.0.1:7890"},
            "yescaptcha": {},
            "twocaptcha": {},
            "browser_turnstile": {},
        },
        raw="u@test.local----pw----cid----rt",
        proxy_override=None,
        opts=opts,
    )
    assert result.get("sso")
    assert seen["interval"] == 2.0


def test_concurrent_jitter_is_light(monkeypatch):
    """Jitter must stay under ~0.4s (was 0.5–2.5 pure wall tax)."""
    sleeps: list[float] = []

    def fake_sleep(sec):
        sleeps.append(float(sec))

    monkeypatch.setattr(time, "sleep", fake_sleep)
    # also patch register's time.sleep import target
    import grokreg.pipeline.register as regmod

    monkeypatch.setattr(regmod.time, "sleep", fake_sleep)

    class FakeMail:
        def wait_for_xai_code(self, **kwargs):
            return "123456"

    class FakeCaptcha:
        def solve_turnstile(self, *a, **k):
            return "cf-tok"

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
    monkeypatch.setattr(
        reg,
        "parse_mail_line",
        lambda raw: SimpleNamespace(
            email="u@test.local",
            password="pw",
            client_id="cid",
            refresh_token="rt",
            raw=raw,
        ),
    )
    monkeypatch.setattr(reg, "parse_sso_jwt_payload", lambda s: {"session_id": "sess"})
    monkeypatch.setattr(
        reg,
        "check_captcha_balances",
        lambda **k: {
            "ok": True,
            "primary": "capsolver",
            "yes": {"ok": False, "configured": False},
            "capsolver": {"ok": True, "configured": True, "balance": 1},
            "twocaptcha": {"ok": False, "configured": False},
        },
    )
    monkeypatch.setattr(reg, "format_balance_report", lambda info: "ok")
    monkeypatch.setattr(reg, "resolve_yescaptcha_key", lambda *a, **k: None)
    monkeypatch.setattr(reg, "resolve_twocaptcha_key", lambda *a, **k: None)

    opts = reg.RegisterOptions(
        password="PwTest1!aA",
        captcha_backend="auto",
        captcha_balance_check=True,
        mail_backend="cloudmail",
        require_captcha_config=False,
        create_short_body_retries=0,
        captcha_prefetch=False,
        concurrent_jitter=True,
    )
    result = reg.register_one(
        cfg={
            "proxy": {"default": "http://127.0.0.1:7890"},
            "yescaptcha": {},
            "twocaptcha": {},
            "browser_turnstile": {},
        },
        raw="u@test.local----pw----cid----rt",
        proxy_override=None,
        opts=opts,
    )
    assert result.get("sso")
    assert sleeps, "expected at least one jitter sleep"
    assert all(0.05 <= s <= 0.35 for s in sleeps), sleeps


def test_captcha_default_poll_interval_is_1_5():
    yes = YesCaptchaSolver("k")
    assert yes._poll_interval == 1.5
    cap = CapSolverSolver("k")
    assert cap._poll_interval == 1.5
    tc = TwoCaptchaSolver("k")
    assert tc._poll_interval == 1.5


def test_poll_token_reuses_session():
    src = inspect.getsource(__import__("grokreg.oauth.device", fromlist=["_poll_token"])._poll_token)
    # owned Session or caller-provided session= (mint reuses TLS)
    assert "session" in src and ("cr.Session" in src or "owned" in src)
    assert src.count("cr.Session(") == 1
