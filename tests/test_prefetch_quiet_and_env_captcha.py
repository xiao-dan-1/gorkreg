"""A: quiet turnstile prefetch fallback; B: env-check shows CAPTCHA_BACKEND + Cap."""
from __future__ import annotations

import logging
from types import SimpleNamespace


def test_prefetch_failure_logs_warning_without_traceback(caplog):
    """TASK_NOT_FOUND style prefetch fail → warning one-liner, no exc_info spam."""
    from grokreg.pipeline import register as reg

    # unit: the except branch behavior via a tiny helper we will add
    assert hasattr(reg, "_log_prefetch_fallback")
    with caplog.at_level(logging.WARNING):
        reg._log_prefetch_fallback(
            RuntimeError("CapSolver getTaskResult error: ERROR_TASK_NOT_FOUND: task data has expired")
        )
    text = " ".join(r.message for r in caplog.records)
    assert "prefetch" in text.lower() or "Turnstile" in text
    assert "TASK_NOT_FOUND" in text or "expired" in text.lower()
    # no traceback dumped via exc_info
    for r in caplog.records:
        assert r.exc_info is None or r.exc_info is False


def test_env_check_includes_capsolver_and_backend(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAPSOLVER_API_KEY", "CAP-TEST-KEY-XXXXXXXX")
    monkeypatch.setenv("CAPTCHA_BACKEND", "capsolver")
    monkeypatch.delenv("YESCAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_PROXY", "http://127.0.0.1:7890")

    cfg = {
        "_root": str(tmp_path),
        "proxy": {
            "default": "http://127.0.0.1:7890",
            "dynamic": {"chain_via": "http://127.0.0.1:7890", "enabled": False, "template": ""},
        },
        "cpa": {},
        "captcha": {"backend": "capsolver"},
    }

    from grokreg.ops import env_cmds

    # avoid live balance network
    def fake_bal(**k):
        return {
            "ok": True,
            "primary": "capsolver",
            "yes": {"configured": False},
            "capsolver": {"configured": True, "ok": True, "balance": 5.0, "probed": True},
            "twocaptcha": {"configured": False},
        }

    monkeypatch.setattr(
        "grokreg.backends.captcha.balance.check_captcha_balances",
        fake_bal,
    )
    monkeypatch.setattr(
        "grokreg.backends.captcha.balance.format_balance_report",
        lambda info: "captcha-balance ok primary=capsolver Cap=5.0 Yes=n/a 2C=n/a",
    )

    env_cmds._cmd_env_check(cfg)
    out = capsys.readouterr().out
    assert "CAPSOLVER_API_KEY" in out
    assert "ready_for_register: yes" in out
    assert "capsolver" in out.lower()
    # must not claim only Yes/2C when Cap is the backend
    assert "need YESCAPTCHA_API_KEY or TWOCAPTCHA_API_KEY" not in out
