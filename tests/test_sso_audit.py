"""SSO three-sink audit: sso_roster · output · auth.json."""
from __future__ import annotations

import json
from pathlib import Path

from grokreg.ops.ledger_ops import audit_sso_ledgers, append_sso_roster


def _write_account(out: Path, email: str, sso: str, error=None):
    out.mkdir(parents=True, exist_ok=True)
    safe = email.replace("@", "_at_")
    (out / f"account_{safe}_t.json").write_text(
        json.dumps({"email": email, "password": "p", "sso": sso, "error": error}),
        encoding="utf-8",
    )


def test_audit_sso_ledgers_sets(tmp_path: Path):
    cli = tmp_path / "sso_roster.txt"
    out = tmp_path / "output"
    auth = tmp_path / "auth.json"

    # cli has a@ + b@
    append_sso_roster(
        {"email": "a@ex.com", "password": "pa", "sso": "sso-a"}, path=cli
    )
    append_sso_roster(
        {"email": "b@ex.com", "password": "pb", "sso": "sso-b"}, path=cli
    )
    # output has b@ + c@ (c not in cli)
    _write_account(out, "b@ex.com", "sso-b")
    _write_account(out, "c@ex.com", "sso-c")
    _write_account(out, "bad@ex.com", "sso-x", error="sso_failed")

    # auth has a@ only
    auth.write_text(
        json.dumps(
            {
                "k1": {
                    "email": "a@ex.com",
                    "access_token": "at",
                    "refresh_token": "rt",
                }
            }
        ),
        encoding="utf-8",
    )

    stats = audit_sso_ledgers(cli_path=cli, output_dir=out, auth_path=auth)
    assert "a@ex.com" in stats["cli_emails"]
    assert "c@ex.com" in stats["output_sso_emails"]
    assert "c@ex.com" in stats["in_output_not_cli"]  # recover candidate
    assert "b@ex.com" in stats["in_cli_not_auth"]  # mint candidate
    assert "a@ex.com" not in stats["in_cli_not_auth"]
    assert "bad@ex.com" not in stats["output_sso_emails"]
    assert stats["counts"]["cli"] == 2
    assert stats["counts"]["auth"] == 1
