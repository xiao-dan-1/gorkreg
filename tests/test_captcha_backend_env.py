"""CAPTCHA_BACKEND env overlay + normalize (keep auto)."""
from __future__ import annotations

import argparse

from grokreg.config import normalize_captcha_backend, _apply_captcha_env
from grokreg.ops.register_cmds import _resolve_captcha_backend


def test_normalize_aliases_and_auto():
    assert normalize_captcha_backend("auto") == "auto"
    assert normalize_captcha_backend("yes") == "yescaptcha"
    assert normalize_captcha_backend("yc") == "yescaptcha"
    assert normalize_captcha_backend("cap") == "capsolver"
    assert normalize_captcha_backend("cs") == "capsolver"
    assert normalize_captcha_backend("capsolver") == "capsolver"
    assert normalize_captcha_backend("2captcha") == "twocaptcha"
    assert normalize_captcha_backend("tc") == "twocaptcha"
    assert normalize_captcha_backend("") == "auto"
    assert normalize_captcha_backend(None) == "auto"
    assert normalize_captcha_backend("nope") == "auto"


def test_apply_captcha_env_sets_backend(monkeypatch):
    cfg: dict = {"captcha": {"backend": "auto"}}
    monkeypatch.delenv("CAPTCHA_BACKEND", raising=False)
    monkeypatch.delenv("GROK_CAPTCHA_BACKEND", raising=False)
    monkeypatch.setenv("CAPTCHA_BACKEND", "capsolver")
    _apply_captcha_env(cfg)
    assert cfg["captcha"]["backend"] == "capsolver"


def test_apply_captcha_env_explicit_auto(monkeypatch):
    cfg: dict = {"captcha": {"backend": "yescaptcha"}}
    monkeypatch.setenv("CAPTCHA_BACKEND", "auto")
    monkeypatch.delenv("GROK_CAPTCHA_BACKEND", raising=False)
    _apply_captcha_env(cfg)
    assert cfg["captcha"]["backend"] == "auto"


def test_apply_captcha_env_blank_keeps_yaml(monkeypatch):
    cfg: dict = {"captcha": {"backend": "yescaptcha"}}
    monkeypatch.setenv("CAPTCHA_BACKEND", "")
    monkeypatch.delenv("GROK_CAPTCHA_BACKEND", raising=False)
    _apply_captcha_env(cfg)
    assert cfg["captcha"]["backend"] == "yescaptcha"


def test_resolve_cli_wins_over_cfg():
    cfg = {"captcha": {"backend": "capsolver"}}
    args = argparse.Namespace(captcha_backend="yescaptcha")
    assert _resolve_captcha_backend(cfg, args) == "yescaptcha"


def test_resolve_cfg_when_cli_none():
    cfg = {"captcha": {"backend": "capsolver"}}
    args = argparse.Namespace(captcha_backend=None)
    assert _resolve_captcha_backend(cfg, args) == "capsolver"


def test_resolve_capsolver_not_dropped():
    """Historical bug: resolve only accepted yes/2c/auto and dropped capsolver."""
    cfg = {"captcha": {"backend": "capsolver"}}
    args = argparse.Namespace(captcha_backend="")
    assert _resolve_captcha_backend(cfg, args) == "capsolver"
