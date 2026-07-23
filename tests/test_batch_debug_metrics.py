"""Batch debug aggregation + overview surface."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_safe_row_fields_in_source():
    src = Path("scripts/prod_cloudmail_batch.py").read_text(encoding="utf-8")
    assert "captcha_est_solves" in src
    assert "_batch_debug" in src
    assert "fail_list" in src
    assert "proxy_retried" in src
    assert "no traceback" in src


def test_recent_batches_exposes_debug():
    from client.services import _recent_batches

    rows = _recent_batches(3)
    assert rows
    b = rows[0]
    # historical n1000 batch should backfill
    assert "ok" in b and "fail" in b
    assert b.get("thr") is not None or b.get("wall_s") is not None
    # backfill or native debug
    assert (
        b.get("prefetch_ok") is not None
        or b.get("captcha_est") is not None
        or b.get("fail_buckets") is not None
    )


def test_overview_has_debug_card():
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    assert "ov-batch-debug" in html
    main = Path("client/static/js/main.js").read_text(encoding="utf-8")
    assert "paintBatchDebug" in main
    assert "captcha_est" in main
