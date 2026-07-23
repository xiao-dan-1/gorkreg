"""SSO roster role: email----password----sso; evidence under output/accounts/."""
from __future__ import annotations

from pathlib import Path

from grokreg.ops import ledger_ops
from grokreg.ops.ledger_ops import (
    SSO_ROSTER_FILE,
    append_sso_roster,
    read_sso_roster,
)


def test_sso_roster_file_constant():
    assert SSO_ROSTER_FILE == "sso_roster.txt"


def test_no_accounts_cli_symbols():
    assert not hasattr(ledger_ops, "read_accounts_cli")
    assert not hasattr(ledger_ops, "append_accounts_cli")


def test_read_sso_roster_parses_lines(tmp_path: Path):
    p = tmp_path / "sso_roster.txt"
    p.write_text(
        "# comment\n"
        "a@ex.com----pwa----sso-a\n"
        "bad-line\n"
        "b@ex.com----sso-b\n",
        encoding="utf-8",
    )
    rows = read_sso_roster(p)
    assert len(rows) == 2
    assert rows[0]["email"] == "a@ex.com" and rows[0]["password"] == "pwa"
    assert rows[1]["email"] == "b@ex.com" and rows[1]["password"] == ""


def test_append_sso_roster_writes(tmp_path: Path):
    p = tmp_path / "roster.txt"
    ok = append_sso_roster(
        {"email": "c@ex.com", "password": "p", "sso": "sso-c"}, path=p
    )
    assert ok is True
    assert p.read_text(encoding="utf-8").strip() == "c@ex.com----p----sso-c"
    ok2 = append_sso_roster(
        {"email": "c@ex.com", "password": "p", "sso": "sso-c"}, path=p
    )
    assert ok2 is False


def test_module_doc_states_roles():
    doc = ledger_ops.__doc__ or ""
    assert "sso_roster" in doc
    assert "auth.json" in doc
    assert "accounts_cli" not in doc
    assert "password" in doc
