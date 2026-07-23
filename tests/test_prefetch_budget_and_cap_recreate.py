"""Cap prefetch join budget + CapSolver TASK_NOT_FOUND recreate + prewarm retry."""
from __future__ import annotations

import logging
from concurrent.futures import Future, TimeoutError as FuturesTimeout
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_is_task_not_found_error():
    from grokreg.backends.captcha.impl import _is_task_expired_error

    assert _is_task_expired_error(
        "CapSolver getTaskResult error: ERROR_TASK_NOT_FOUND: task data has expired"
    )
    assert _is_task_expired_error("ERROR_TASK_NOT_FOUND")
    assert _is_task_expired_error("task data has expired")
    assert not _is_task_expired_error("ERROR_ZERO_BALANCE")
    assert not _is_task_expired_error("timeout")


def test_capsolver_recreates_task_on_not_found(monkeypatch):
    from grokreg.backends.captcha import impl as impl_mod

    posts: list[str] = []
    states = {"create": 0}

    def fake_post(self, path, payload):
        posts.append(path)
        if path == "/createTask":
            states["create"] += 1
            return {"errorId": 0, "taskId": f"t{states['create']}"}
        if path == "/getTaskResult":
            tid = payload.get("taskId")
            if tid == "t1":
                return {
                    "errorId": 1,
                    "errorCode": "ERROR_TASK_NOT_FOUND",
                    "errorDescription": "task data has expired",
                }
            return {
                "errorId": 0,
                "status": "ready",
                "solution": {"token": "tok-ok"},
            }
        raise AssertionError(path)

    monkeypatch.setattr(impl_mod.CapSolverSolver, "_post_json", fake_post)
    monkeypatch.setattr(impl_mod.CapSolverSolver, "_progress", lambda *a, **k: None)

    s = impl_mod.CapSolverSolver("k", timeout=30, poll_interval=0.01)
    tok = s.solve_turnstile("https://x.ai/sign-up", "sitekey")
    assert tok == "tok-ok"
    assert states["create"] == 2  # original + recreate
    assert posts.count("/createTask") == 2


def test_join_prefetch_respects_budget(monkeypatch):
    """If prefetch already ran past join budget, cancel and call sync solver."""
    from grokreg.pipeline import register as reg

    calls = {"sync": 0, "cancel": 0}

    class FakeFut:
        def result(self, timeout=None):
            raise AssertionError("must not wait on over-budget future")

        def cancel(self):
            calls["cancel"] += 1
            return True

    def sync():
        calls["sync"] += 1
        return "sync-token"

    tok, meta = reg._join_turnstile_prefetch(
        pref_future=FakeFut(),
        pref_t0=0.0,  # epoch → age huge
        now=1000.0,
        max_join_s=45.0,
        sync_solve=sync,
    )
    assert tok == "sync-token"
    assert calls["sync"] == 1
    assert calls["cancel"] == 1
    assert meta.get("prefetch") is False
    assert meta.get("reason") == "join_budget"


def test_join_prefetch_timeout_falls_back_to_sync():
    from grokreg.pipeline import register as reg

    calls = {"sync": 0}

    class SlowFut:
        def result(self, timeout=None):
            raise FuturesTimeout()

        def cancel(self):
            return True

    def sync():
        calls["sync"] += 1
        return "sync-tok"

    tok, meta = reg._join_turnstile_prefetch(
        pref_future=SlowFut(),
        pref_t0=100.0,
        now=110.0,
        max_join_s=45.0,
        sync_solve=sync,
    )
    assert tok == "sync-tok"
    assert calls["sync"] == 1
    assert meta.get("reason") in {"join_timeout", "prefetch_error", "join_budget"}


def test_prewarm_retries_once(monkeypatch):
    from scripts import prod_cloudmail_batch as batch

    n = {"i": 0}

    class BoomClient:
        def __init__(self, *a, **k):
            pass

        def load_signup_page(self, **k):
            n["i"] += 1
            if n["i"] == 1:
                raise RuntimeError("curl: (35) TLS")
            return {
                "next_action": "act",
                "turnstile_sitekey": "sk",
                "scrape_cache": "miss",
                "http_status": 200,
            }

        _next_action_id = "act"
        _next_router_state_tree = "tree"
        turnstile_sitekey = "sk"

    monkeypatch.setattr(batch, "GrokAuthClient", BoomClient, raising=False)
    # patch import path used inside function
    import grokreg.client as client_mod

    monkeypatch.setattr(client_mod, "GrokAuthClient", BoomClient)
    monkeypatch.setattr(
        "grokreg.proxyutil.resolve_proxy",
        lambda *a, **k: SimpleNamespace(session_url="http://127.0.0.1:7890"),
    )
    monkeypatch.setattr(
        batch.scrape_cache,
        "put",
        lambda **k: None,
    )

    out = batch._prewarm_scrape({}, proxy_override=None, ttl=600.0)
    assert out["ok"] is True
    assert n["i"] == 2
