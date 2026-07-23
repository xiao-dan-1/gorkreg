"""Ledger 2.5 UI structure."""
from pathlib import Path


def test_ledger_html_hero_and_columns():
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    assert "ledger-hero" in html
    assert "只看异常" in html
    assert "需保活" in html
    assert "left_h" not in html or "剩余" in html
    assert "凭证列表" in html


def test_ledger_js_helpers():
    main = Path("client/static/js/main.js").read_text(encoding="utf-8")
    assert "formatLeftH" in main
    assert "paintLedgerHero" in main
    assert "copyLedgerEmail" in main
    assert "stateLabelZh" in main


def test_ledger_sort_exception_first():
    src = Path("client/services.py").read_text(encoding="utf-8")
    assert "exception-first" in src or "tier = 0" in src
