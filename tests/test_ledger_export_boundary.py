"""Architecture boundary: credential lifecycle must not require cpa_export."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_refresh_entry_has_no_cpa_sidecar_knobs():
    from grokreg.auth_pool import refresh_entry

    sig = inspect.signature(refresh_entry)
    assert "also_write_cpa" not in sig.parameters
    assert "cpa_dir" not in sig.parameters


def test_refresh_entry_source_is_refresh_tokens():
    src = (ROOT / "grokreg" / "auth_pool.py").read_text(encoding="utf-8")
    start = src.find("def refresh_entry")
    end = src.find("def summarize", start)
    body = src[start:end]
    assert "refresh_tokens" in body
    assert "cpa_refresh" not in body
    # no file I/O of cpa sidecars (docstring may still mention the path name)
    assert "Path(\"cpa_export\")" not in body
    assert "cpa_path" not in body
    assert "write_text" not in body
    assert "publish_credentials" not in body
    # primary ledger exp field must be updated on RT→AT
    assert "apply_token_expiry" in body or 'entry2["expires_at"]' in body


def test_cli_refresh_does_not_pass_cpa_dir():
    """Regression: refresh_entry no longer accepts cpa_dir; CLI must not pass it."""
    src = (ROOT / "grokreg" / "ops" / "credential_cmds.py").read_text(encoding="utf-8")
    i = src.find("def _cmd_refresh")
    assert i > 0
    j = src.find("\ndef ", i + 1)
    body = src[i : j if j > 0 else i + 12000]
    assert "refresh_entry(" in body
    assert "cpa_dir=" not in body
    # dead out_dir for refresh path should stay gone (comment may still say cpa_dir)
    assert "out_dir = Path(args.mint_out_dir" not in body


def test_refresh_entry_updates_expires_at(tmp_path, monkeypatch):
    """RT refresh must sync expires_at (status_row primary) not only expired ISO."""
    import grokreg.auth_pool as ap

    auth = tmp_path / "auth.json"
    email = "rt-sync@example.com"
    old_exp = 1_700_000_000.0
    key = ap.upsert(
        auth,
        {
            "email": email,
            "access_token": "old_at",
            "refresh_token": "rt1",
            "expires_at": old_exp,
            "expires_in": 21600,
            "type": "xai",
            "auth_kind": "oauth",
        },
    )
    new_exp = 1_800_000_000

    def _fake_refresh_tokens(**kwargs):
        return {
            "ok": True,
            "email": email,
            "access_token": "new_at",
            "refresh_token": "rt2",
            "exp_old": int(old_exp),
            "exp_new": new_exp,
            "expires_in": 21600,
        }

    # refresh_entry imports refresh_tokens from .oauth (inside function)
    import grokreg.oauth as oauth_mod

    monkeypatch.setattr(oauth_mod, "refresh_tokens", _fake_refresh_tokens)

    entry = ap.load_pool(auth)[key]
    r = ap.refresh_entry(auth, key, entry, proxy="http://127.0.0.1:9", probe_mode="none")
    assert r.get("ok") is True

    saved = ap.load_pool(auth)[key]
    assert saved["access_token"] == "new_at"
    assert saved["refresh_token"] == "rt2"
    assert float(saved["expires_at"]) == float(new_exp)
    assert "expired" in saved
    row = ap.status_row(key, saved, skew_sec=300)
    # left_h must track new exp (primary expires_at), not stale old_exp
    assert float(row["expires_at"]) == float(new_exp)
    assert float(saved["expires_at"]) > old_exp
    assert row["state"] == "fresh"


def test_cli_remint_packs_empty():
    src = (ROOT / "grokreg" / "ops" / "credential_cmds.py").read_text(encoding="utf-8")
    i = src.find("refresh revoked")
    assert i > 0
    chunk = src[i : i + 900]
    assert "packs=[]" in chunk


def test_oauth_no_public_file_path_refresh():
    """File-path refresh removed — only refresh_tokens is public."""
    import types
    import grokreg.oauth as oauth

    assert callable(oauth.refresh_tokens)
    assert not (ROOT / "grokreg" / "oauth" / "legacy_file_refresh.py").exists()
    assert "refresh" not in getattr(oauth, "__all__", [])
    # oauth.refresh is the refresh.py submodule if present — not a file-path API
    if hasattr(oauth, "refresh"):
        assert isinstance(oauth.refresh, types.ModuleType)



def test_oauth_package_not_cpa_monolith():
    """Protocol lives in grokreg.oauth; cpa is compat alias package."""
    import grokreg.oauth as cpa
    import grokreg.oauth as oauth

    assert (ROOT / "grokreg" / "oauth" / "__init__.py").is_file()
    assert (ROOT / "grokreg" / "oauth" / "__init__.py").is_file()
    assert not (ROOT / "grokreg" / "cpa.py").is_file()
    assert not (ROOT / "grokreg" / "cpa").exists()
    assert (ROOT / "grokreg" / "oauth" / "__init__.py").is_file()
    for name in ("mint", "refresh_tokens", "probe_quota", "CLIENT_ID", "BASE_URL"):
        assert hasattr(cpa, name), name
        assert getattr(cpa, name) is getattr(oauth, name), name
    for rel in (
        "grokreg/oauth/mint.py",
        "grokreg/oauth/refresh.py",
                "grokreg/oauth/probe_quota.py",
        "grokreg/backends/export/cpa_health.py",
    ):
        ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def test_syntax_core_modules():
    for rel in (
        "grokreg/auth_pool.py",
        "grokreg/oauth/__init__.py",
        "grokreg/oauth/mint.py",
                "grokreg/cli.py",
        "grokreg/ops/prod_pipeline.py",
        "grokreg/ops/env_cmds.py",
        "grokreg/ops/register_cmds.py",
        "grokreg/ops/export_cmds.py",
    ):
        ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def test_probe_quota_accepts_tokens_without_file():
    import inspect
    from grokreg.oauth import probe_quota

    sig = inspect.signature(probe_quota)
    assert "access_token" in sig.parameters
    assert "email" in sig.parameters


def test_cli_probe_uses_auth_json_not_cpa_glob():
    src = (ROOT / "grokreg" / "ops" / "credential_cmds.py").read_text(encoding="utf-8")
    i = src.find("def _cmd_probe_quota")
    assert i > 0
    body = src[i : i + 2500]
    assert "list_entries" in body
    assert "source=auth.json" in body or "auth.json" in body
    assert "CPA 目录下无匹配文件" not in body


def test_select_mint_todos_newest_first_with_limit(tmp_path):
    from grokreg.ops.ledger_ops import select_mint_todos

    accounts = [
        {"email": f"old{i}@x.com", "password": "p", "sso": f"s{i}"}
        for i in range(1, 6)
    ]
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    todos, stats = select_mint_todos(
        accounts,
        target="all",
        auth_path=auth,
        missing_only=True,
        limit=2,
        newest_first=True,
    )
    assert len(todos) == 2
    assert todos[0]["email"] == "old5@x.com"
    assert todos[1]["email"] == "old4@x.com"
    assert stats["will_mint"] == 2


def test_select_mint_todos_skips_existing(tmp_path):
    from grokreg.ops.ledger_ops import select_mint_todos
    from grokreg.auth_pool import upsert

    auth = tmp_path / "auth.json"
    upsert(
        auth,
        {
            "email": "old5@x.com",
            "access_token": "at",
            "refresh_token": "rt",
            "type": "xai",
            "auth_kind": "oauth",
        },
    )
    accounts = [
        {"email": f"old{i}@x.com", "password": "p", "sso": f"s{i}"}
        for i in range(1, 6)
    ]
    todos, stats = select_mint_todos(
        accounts,
        target="all",
        auth_path=auth,
        missing_only=True,
        limit=2,
        newest_first=True,
    )
    emails = [t["email"] for t in todos]
    assert "old5@x.com" not in emails
    assert emails[0] == "old4@x.com"
    assert stats["skipped_existing"] >= 1


def test_credential_cmds_module_importable():
    from grokreg.ops import credential_cmds
    from grokreg.cli import _cmd_mint, _cmd_refresh, _cmd_probe_quota

    assert _cmd_mint.__module__.endswith("mint_cmds")
    assert _cmd_refresh.__module__.endswith("credential_cmds")
    assert callable(credential_cmds._cmd_auth_status)


def test_export_cmds_module_importable():
    from grokreg.ops import export_cmds
    from grokreg.cli import _cmd_cpa_upload, _cmd_export, _cmd_sub2api_upload

    assert _cmd_cpa_upload.__module__.endswith("export_cmds")
    assert _cmd_export.__module__.endswith("export_cmds")
    assert callable(export_cmds._cmd_auth_list)


def test_env_and_register_cmds_importable():
    from grokreg.ops import env_cmds, register_cmds
    from grokreg.cli import _cmd_env_check, _cmd_batch, _cmd_register, _save_result

    assert _cmd_env_check.__module__.endswith("env_cmds")
    assert _cmd_batch.__module__.endswith("register_cmds")
    assert _cmd_register.__module__.endswith("register_cmds")
    # save_result lives in ledger_ops; re-exported via register_cmds alias
    assert _save_result.__module__.endswith("ledger_ops")
    assert callable(env_cmds._cmd_check_chain)
    # summary / sso audit live in summary_cmds, re-exported from register_cmds
    assert register_cmds._cmd_summary.__module__.endswith("summary_cmds")
    assert callable(getattr(register_cmds, "_cmd_sso_audit", None))
    assert register_cmds._cmd_sso_audit.__module__.endswith("summary_cmds")
    assert register_cmds._cmd_exp_round.__module__.endswith("exp_cmds")
    assert register_cmds._cmd_mail_mark.__module__.endswith("mail_cmds")


def test_register_cmds_split_modules_exist():
    from pathlib import Path

    ops = Path(__file__).resolve().parents[1] / "grokreg" / "ops"
    for name in ("register_cmds.py", "summary_cmds.py", "mail_cmds.py", "exp_cmds.py"):
        assert (ops / name).is_file(), name
    # core register file should stay leaner than the pre-split monolith (~1250)
    assert (ops / "register_cmds.py").stat().st_size < 80_000



def test_oauth_package_layout():
    """Protocol package is oauth only (no cpa protocol dir)."""
    oauth = ROOT / "grokreg" / "oauth"
    assert oauth.is_dir()
    assert not (ROOT / "grokreg" / "cpa").exists()
    assert not (ROOT / "grokreg" / "cpa.py").exists()
    for name in (
        "mint.py",
        "refresh.py",
        "probe_quota.py",
        "probe_billing.py",
        "device.py",
    ):
        assert (oauth / name).is_file(), name
    assert (ROOT / "grokreg" / "backends" / "export" / "cpa_health.py").is_file()
    assert not (oauth / "export_health.py").exists()



def test_oauth_public_imports():
    from grokreg.oauth import mint, refresh_tokens, probe_quota, CLIENT_ID
    from grokreg.backends.export.cpa_health import inspect_cpa_file

    assert callable(mint) and callable(refresh_tokens) and callable(probe_quota)
    assert callable(inspect_cpa_file)
    assert CLIENT_ID


def test_no_cpa_protocol_package():
    """Protocol is oauth only — no grokreg.cpa package / module."""
    import importlib.util

    assert not (ROOT / "grokreg" / "cpa").exists()
    assert not (ROOT / "grokreg" / "cpa.py").exists()
    # cpa_upload / xai_pack may exist (export product)
    assert (ROOT / "grokreg" / "oauth" / "__init__.py").is_file()
    assert importlib.util.find_spec("grokreg.oauth") is not None
    assert importlib.util.find_spec("grokreg.cpa") is None
