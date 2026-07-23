"""Register: captcha prefetch starts after scrape (overlaps create_code + wait)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from grokreg.pipeline import register as reg


def test_prefetch_overlaps_create_code_and_wait(monkeypatch):
    order: list[str] = []
    lock = threading.Lock()

    def mark(name: str):
        with lock:
            order.append(name)

    class FakeMail:
        def wait_for_xai_code(self, **kwargs):
            mark("wait_start")
            time.sleep(0.12)
            mark("wait_end")
            return "123456"

    class FakeCaptcha:
        def solve_turnstile(self, *a, **k):
            mark("captcha_start")
            time.sleep(0.18)
            mark("captcha_end")
            return "cf-tok"

    class FakeClient:
        signup_url = "https://accounts.x.ai/sign-up"
        turnstile_sitekey = "0xTEST"

        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def load_signup_page(self, **k):
            mark("scrape")
            return {
                "scrape_cache": "hit",
                "next_action": "act",
                "turnstile_sitekey": "0xTEST",
            }

        def create_email_validation_code(self, email):
            mark("create_code_start")
            time.sleep(0.08)
            mark("create_code_end")
            return SimpleNamespace(ok=True, error=None)

        def verify_email_validation_code(self, email, code):
            mark("verify")
            return SimpleNamespace(ok=True, error=None)

        def validate_password(self, email, password):
            return True

        def create_account(self, **k):
            mark("create_account")
            return SimpleNamespace(ok=True, error=None, http_status=200, rsc_body="x" * 1200)

        def fetch_sso_token(self, retries=3):
            mark("sso")
            return "sso.jwt"

    class FakeResolved:
        session_url = None
        sid = "sid"
        region = "US"

        def label(self):
            return "p"

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
            email="u@test.local", password="pw", client_id="c", refresh_token="r", raw=raw
        ),
    )
    monkeypatch.setattr(reg, "parse_sso_jwt_payload", lambda s: {"session_id": "s"})
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
    monkeypatch.setattr(reg, "resolve_capsolver_key", lambda *a, **k: "CAP")

    opts = reg.RegisterOptions(
        password="PwTest1!aA",
        captcha_backend="auto",
        captcha_balance_check=True,
        captcha_prefetch=True,
        mail_backend="cloudmail",
        require_captcha_config=False,
        create_short_body_retries=0,
    )
    t0 = time.time()
    result = reg.register_one(
        cfg={
            "proxy": {"default": "http://127.0.0.1:7890"},
            "yescaptcha": {},
            "twocaptcha": {},
            "capsolver": {"api_key": "CAP"},
            "browser_turnstile": {},
        },
        raw="u@test.local----pw----c----r",
        proxy_override=None,
        opts=opts,
    )
    wall = time.time() - t0

    assert result.get("sso") and not result.get("error"), result
    assert "captcha_start" in order
    # captcha must start before wait ends (overlap create+wait)
    assert order.index("captcha_start") < order.index("wait_end")
    # ideally starts before or during create_code
    assert order.index("captcha_start") <= order.index("create_code_end")
    # wall < sum of captcha + create + wait
    assert wall < 0.18 + 0.08 + 0.12 + 0.15, (wall, order)
