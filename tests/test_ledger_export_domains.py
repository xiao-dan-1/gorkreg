"""Ledger domains + export emails."""
from pathlib import Path

from client.services import export_auth_emails, list_auth_ledger


def test_list_auth_ledger_has_domains_and_gap():
    d = list_auth_ledger(limit=5, offset=0)
    assert d.get("ok")
    assert "domains_top" in d
    assert "mint_gap" in d
    assert isinstance(d["domains_top"], list)


def test_export_emails_no_tokens():
    d = export_auth_emails(max_n=20)
    assert d.get("ok")
    text = d.get("text") or ""
    assert "access_token" not in text
    assert "refresh_token" not in text
    if d.get("count"):
        assert "@" in text


def test_html_export_and_domains():
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    assert "ledger-domains" in html
    assert "exportLedgerEmails" in html
    assert "ledger-stat-gap" in html
