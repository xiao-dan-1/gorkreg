"""Public ledger_ops API: no scripts/ops sibling must need credential_cmds privates."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_append_sso_roster_public_importable():
    from grokreg.ops.ledger_ops import append_sso_roster

    assert callable(append_sso_roster)


def test_append_sso_roster_dedupe_and_lock(tmp_path: Path):
    from grokreg.ops.ledger_ops import append_sso_roster

    cli = tmp_path / "sso_roster.txt"
    email = "public-lock@example.com"
    result = {"email": email, "password": "pw", "sso": "sso-token-pub"}

    def once():
        return append_sso_roster(result, path=cli)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(once) for _ in range(20)]
        results = [f.result() for f in as_completed(futs)]

    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == 19
    lines = [ln for ln in cli.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].startswith(email + "----")
    assert lines[0].count("----") == 2  # email----password----sso
    assert "sso-token-pub" in lines[0]


def test_append_sso_roster_skips_error_or_missing():
    from grokreg.ops.ledger_ops import append_sso_roster

    assert append_sso_roster({"email": "a@b.c", "sso": "x", "error": "fail"}) is False
    assert append_sso_roster({"email": "", "sso": "x"}) is False
    assert append_sso_roster({"email": "a@b.c", "sso": ""}) is False


def test_resolve_auth_path_public():
    from grokreg.ops.ledger_ops import resolve_auth_path

    class NS:
        auth_file = None

    p = resolve_auth_path({"_root": str(ROOT)}, NS())
    assert p.name == "auth.json"
    assert p.is_absolute() or str(p).endswith("auth.json")


def test_scripts_and_ops_do_not_import_private_append():
    """Call sites must use ledger_ops.append_sso_roster (public)."""
    offenders: list[str] = []
    paths = [
        ROOT / "scripts" / "prod_cloudmail_batch.py",
        ROOT / "grokreg" / "ops" / "register_cmds.py",
        ROOT / "grokreg" / "ops" / "prod_pipeline.py",
    ]
    for path in paths:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                name = alias.name
                if name == "_append_sso_roster":
                    offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert not offenders, offenders


def test_credential_cmds_reexports_append_compat():
    """One-release compat: private name still works for old callers/tests."""
    from grokreg.ops.credential_cmds import _append_sso_roster
    from grokreg.ops.ledger_ops import append_sso_roster

    assert _append_sso_roster is append_sso_roster or callable(_append_sso_roster)
