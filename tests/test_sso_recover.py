"""Recover sso_roster SSO lines from output account evidence."""
from __future__ import annotations

import json
from pathlib import Path

from grokreg.ops.ledger_ops import recover_sso_roster_from_output


def _write_account(path: Path, email: str, sso: str, password: str = "pw", error=None):
    path.write_text(
        json.dumps(
            {
                "email": email,
                "password": password,
                "sso": sso,
                "error": error,
            }
        ),
        encoding="utf-8",
    )


def test_recover_from_output_appends_missing(tmp_path: Path):
    out = tmp_path / "output" / "accounts"
    out.mkdir(parents=True)
    _write_account(out / "account_a.json", "a@ex.com", "sso-a", "pwa")
    _write_account(out / "account_b.json", "b@ex.com", "sso-b", "pwb")
    _write_account(out / "account_bad.json", "bad@ex.com", "sso-x", error="sso_failed")
    _write_account(out / "account_nosso.json", "n@ex.com", "")

    cli = tmp_path / "sso_roster.txt"
    cli.write_text("a@ex.com----old----sso-old\n", encoding="utf-8")

    stats = recover_sso_roster_from_output(tmp_path / "output", cli_path=cli)
    assert stats["scanned"] >= 4
    assert stats["appended"] == 1  # only b missing
    assert stats["skipped_existing"] == 1  # a already present
    assert stats["skipped_no_sso"] >= 1
    assert stats["skipped_error"] >= 1

    text = cli.read_text(encoding="utf-8")
    assert "b@ex.com----pwb----sso-b" in text
    assert text.count("a@ex.com----") == 1


def test_recover_dry_run_no_write(tmp_path: Path):
    out = tmp_path / "output" / "accounts"
    out.mkdir(parents=True)
    _write_account(out / "account_c.json", "c@ex.com", "sso-c")
    cli = tmp_path / "sso_roster.txt"
    stats = recover_sso_roster_from_output(tmp_path / "output", cli_path=cli, dry_run=True)
    assert stats["would_append"] == 1
    assert stats["appended"] == 0
    assert not cli.is_file() or not cli.read_text(encoding="utf-8").strip()


def test_recover_prefers_newest_file_same_email(tmp_path, monkeypatch):
    """Same email multi-file: newest mtime SSO is appended once."""
    import time
    from grokreg.ops.ledger_ops import recover_sso_roster_from_output

    out = tmp_path / "output" / "accounts"
    out.mkdir(parents=True)
    old = {
        "email": "dup@x.com",
        "password": "oldpw",
        "sso": "old-sso-token-value",
    }
    new = {
        "email": "dup@x.com",
        "password": "newpw",
        "sso": "new-sso-token-value",
    }
    p_old = out / "account_dup_at_x.com_1.json"
    p_new = out / "account_dup_at_x.com_2.json"
    p_old.write_text(__import__("json").dumps(old), encoding="utf-8")
    time.sleep(0.05)
    p_new.write_text(__import__("json").dumps(new), encoding="utf-8")
    # ensure mtime order
    import os
    os.utime(p_old, (1, 1))
    os.utime(p_new, (100, 100))

    roster = tmp_path / "sso_roster.txt"
    roster.write_text("", encoding="utf-8")
    stats = recover_sso_roster_from_output(tmp_path / "output", cli_path=roster)
    assert stats["appended"] == 1
    assert stats.get("skipped_dup_file", 0) == 1
    line = roster.read_text(encoding="utf-8").strip()
    assert "new-sso-token-value" in line
    assert "old-sso-token-value" not in line
