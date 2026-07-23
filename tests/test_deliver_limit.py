"""Deliver: export/upload honor limit."""
from __future__ import annotations

import inspect

from client.task_manager import TaskManager


def test_start_export_signature_has_limit():
    sig = inspect.signature(TaskManager.start_export)
    assert "limit" in sig.parameters


def test_start_upload_signature_has_limit():
    sig = inspect.signature(TaskManager.start_upload)
    assert "limit" in sig.parameters


def test_export_auth_pool_accepts_limit():
    from grokreg.backends.export.factory import export_auth_pool

    sig = inspect.signature(export_auth_pool)
    assert "limit" in sig.parameters


def test_deliver_ui_has_limit_fields():
    from pathlib import Path

    html = Path("client/static/index.html").read_text(encoding="utf-8")
    assert 'id="export-limit"' in html
    assert 'id="up-limit"' in html
    assert 'id="export-email"' in html
    js = Path("client/static/app.js").read_text(encoding="utf-8")
    assert "export-limit" in js
    assert "up-limit" in js
