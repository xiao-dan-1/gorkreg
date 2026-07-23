"""Scripts/ops must use public ops APIs — not private _cmd/_save helpers."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRIVATE_BANNED = {
    "_save_result",
    "_summary_error_bucket",
    "_run_proxy_preflight",
    "_append_sso_roster",
    "_auth_path",
}

CALL_SITES = [
    ROOT / "scripts" / "prod_cloudmail_batch.py",
    ROOT / "scripts" / "probe_sso_failed.py",
    ROOT / "grokreg" / "ops" / "export_cmds.py",
    ROOT / "grokreg" / "ops" / "prod_pipeline.py",
]


def test_public_ops_exports():
    from grokreg.ops import (
        append_sso_roster,
        resolve_auth_path,
        save_result,
        summary_error_bucket,
        run_proxy_preflight,
        audit_sso_ledgers,
    )

    assert callable(save_result)
    assert callable(summary_error_bucket)
    assert callable(run_proxy_preflight)
    assert callable(audit_sso_ledgers)
    assert callable(append_sso_roster)
    assert callable(resolve_auth_path)


def test_summary_error_bucket_public():
    from grokreg.ops.ledger_ops import summary_error_bucket

    assert summary_error_bucket(None) == "-"
    assert summary_error_bucket("sso_failed after create") == "sso_failed"
    assert "proxy" in summary_error_bucket("proxy connect tunnel failed").lower() or summary_error_bucket(
        "proxy connect tunnel failed"
    ) == "proxy"


def test_save_result_writes_account_json(tmp_path: Path):
    from grokreg.ops.ledger_ops import save_result

    cfg = {"_root": str(tmp_path)}
    result = {
        "email": "pub@example.com",
        "password": "x",
        "sso": "sso-token",
        "error": None,
    }
    path = save_result(cfg, None, result)
    assert path.is_file()
    assert "accounts" in path.parts
    assert "pub" in path.name
    text = path.read_text(encoding="utf-8")
    assert "sso-token" in text
    # second save same email overwrites same file family
    path2 = save_result(cfg, None, {**result, "sso": "sso-token-2"})
    assert path2.name == path.name or path2.parent == path.parent


def test_call_sites_do_not_import_private_ops_helpers():
    offenders: list[str] = []
    for path in CALL_SITES:
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in PRIVATE_BANNED:
                    offenders.append(f"{path.relative_to(ROOT)} imports {alias.name}")
    assert not offenders, offenders


def test_export_cmds_uses_resolve_auth_path():
    src = (ROOT / "grokreg" / "ops" / "export_cmds.py").read_text(encoding="utf-8")
    assert "resolve_auth_path" in src
    assert "from grokreg.ops.credential_cmds import _auth_path" not in src
