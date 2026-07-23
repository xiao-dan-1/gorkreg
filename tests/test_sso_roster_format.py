"""SSO roster: email----password----sso; account evidence under output/accounts/."""
from __future__ import annotations

import json
from pathlib import Path

from grokreg.ops.ledger_ops import (
    ACCOUNT_EVIDENCE_SUBDIR,
    SSO_ROSTER_FILE,
    account_evidence_dir,
    append_sso_roster,
    format_sso_roster_line,
    iter_account_evidence_paths,
    migrate_account_evidence_to_subdir,
    migrate_sso_roster_ensure_passwords,
    parse_sso_roster_line,
    read_sso_roster,
    record_register_success,
    recover_sso_roster_from_output,
    save_result,
)


def test_parse_two_and_three_part():
    r2 = parse_sso_roster_line("a@x.com----sso-a")
    assert r2 == {"email": "a@x.com", "password": "", "sso": "sso-a"}
    r3 = parse_sso_roster_line("b@x.com----secret----sso-b")
    assert r3 == {"email": "b@x.com", "password": "secret", "sso": "sso-b"}
    assert parse_sso_roster_line("bad") is None


def test_append_writes_three_part_with_password(tmp_path: Path):
    p = tmp_path / "sso_roster.txt"
    ok = append_sso_roster(
        {"email": "c@ex.com", "password": "pw-c", "sso": "sso-c"},
        path=p,
    )
    assert ok is True
    assert p.read_text(encoding="utf-8").strip() == "c@ex.com----pw-c----sso-c"
    assert format_sso_roster_line("c@ex.com", "pw-c", "sso-c") == "c@ex.com----pw-c----sso-c\n"


def test_read_mixed_formats(tmp_path: Path):
    p = tmp_path / "sso_roster.txt"
    p.write_text(
        "a@ex.com----sso-a\n"
        "b@ex.com----pw----sso-b\n"
        "c@ex.com----sso-c\n",
        encoding="utf-8",
    )
    rows = read_sso_roster(p)
    assert [r["email"] for r in rows] == ["a@ex.com", "b@ex.com", "c@ex.com"]
    assert rows[0]["password"] == "" and rows[0]["sso"] == "sso-a"
    assert rows[1]["password"] == "pw" and rows[1]["sso"] == "sso-b"


def test_migrate_fills_password_from_evidence(tmp_path: Path):
    root = tmp_path
    accounts = root / "output" / "accounts"
    accounts.mkdir(parents=True)
    (accounts / "account_a.json").write_text(
        json.dumps({"email": "a@ex.com", "password": "from-ev", "sso": "sso-a", "error": None}),
        encoding="utf-8",
    )
    roster = root / "sso_roster.txt"
    roster.write_text("a@ex.com----sso-a\nb@ex.com----keep----sso-b\n", encoding="utf-8")

    dry = migrate_sso_roster_ensure_passwords(roster, dry_run=True, evidence_root=root)
    assert dry["two_part_in"] >= 1
    assert dry["rewritten"] is False

    stats = migrate_sso_roster_ensure_passwords(roster, dry_run=False, evidence_root=root)
    assert stats["rewritten"] is True
    assert stats["filled_password"] >= 1
    text = roster.read_text(encoding="utf-8")
    assert "a@ex.com----from-ev----sso-a" in text
    assert "b@ex.com----keep----sso-b" in text


def test_save_result_under_accounts_subdir(tmp_path: Path):
    cfg = {"_root": str(tmp_path)}
    path = save_result(
        cfg,
        None,
        {"email": "d@ex.com", "password": "p", "sso": "s", "error": None},
    )
    assert path.parent == account_evidence_dir(cfg)
    assert ACCOUNT_EVIDENCE_SUBDIR in path.parts
    assert path.parent.name == "accounts"
    assert (tmp_path / "output" / "accounts.jsonl").is_file() or (
        path.parent.parent / "accounts.jsonl"
    ).is_file()


def test_record_register_success_dual_write(tmp_path: Path):
    cfg = {"_root": str(tmp_path)}
    roster = tmp_path / "sso_roster.txt"
    result = {
        "email": "e@ex.com",
        "password": "secret",
        "sso": "sso-e",
        "error": None,
    }
    out = record_register_success(cfg, result, roster_path=roster)
    assert out["roster_appended"] is True
    assert ACCOUNT_EVIDENCE_SUBDIR in Path(out["path"]).parts
    assert "secret" in out["path"].read_text(encoding="utf-8")
    assert roster.read_text(encoding="utf-8").strip() == "e@ex.com----secret----sso-e"


def test_migrate_account_evidence_moves_root_files(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    f = out / "account_x_at_y_20260101.json"
    f.write_text('{"email":"x@y.com","sso":"s"}', encoding="utf-8")
    stats = migrate_account_evidence_to_subdir(tmp_path, dry_run=False)
    assert stats["moved"] == 1
    dest = out / "accounts" / f.name
    assert dest.is_file()
    assert not f.exists()
    paths = iter_account_evidence_paths(tmp_path)
    assert dest in paths


def test_recover_from_accounts_subdir(tmp_path: Path):
    accounts = tmp_path / "output" / "accounts"
    accounts.mkdir(parents=True)
    (accounts / "account_a.json").write_text(
        json.dumps({"email": "a@ex.com", "password": "p", "sso": "sso-a", "error": None}),
        encoding="utf-8",
    )
    roster = tmp_path / "sso_roster.txt"
    stats = recover_sso_roster_from_output(tmp_path / "output", cli_path=roster)
    assert stats["appended"] == 1
    assert "a@ex.com----p----sso-a" in roster.read_text(encoding="utf-8")


def test_constants():
    assert SSO_ROSTER_FILE == "sso_roster.txt"
    assert ACCOUNT_EVIDENCE_SUBDIR == "accounts"
