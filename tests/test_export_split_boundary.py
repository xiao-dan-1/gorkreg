"""Export split: ledger publish vs pack factory stay separate."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "grokreg" / "backends" / "export"


def test_ledger_module_exists_and_has_publish():
    src = (EXPORT / "ledger.py").read_text(encoding="utf-8")
    assert "def publish_credentials" in src
    assert "def upsert_payload_to_pool" in src
    # ledger must not batch-export packs
    assert "def export_auth_pool" not in src


def test_factory_is_pack_oriented():
    src = (EXPORT / "factory.py").read_text(encoding="utf-8")
    assert "PACK_BACKENDS" in src
    assert "def export_auth_pool" in src
    assert "def get_export_backend" in src
    # publish body should live in ledger (factory may re-export only)
    tree = ast.parse(src)
    publish_defs = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "publish_credentials"
    ]
    assert not publish_defs, "publish_credentials must not be defined in factory.py"


def test_public_api_from_package():
    from grokreg.backends.export import (
        PACK_BACKENDS,
        export_auth_pool,
        get_export_backend,
        publish_credentials,
        upsert_payload_to_pool,
    )
    from grokreg.backends.export import ledger as ledger_mod
    from grokreg.backends.export import factory as factory_mod

    assert publish_credentials is ledger_mod.publish_credentials
    assert export_auth_pool is factory_mod.export_auth_pool
    assert "cpa_files" in PACK_BACKENDS
    assert "sub2api" in PACK_BACKENDS
    assert "cockpit" in PACK_BACKENDS
    assert callable(get_export_backend)
    assert callable(upsert_payload_to_pool)


def test_publish_ledger_only(tmp_path):
    from grokreg.backends.export import publish_credentials

    auth = tmp_path / "auth.json"
    payload = {
        "email": "pack-split@example.com",
        "access_token": "at",
        "refresh_token": "rt",
        "type": "xai",
        "auth_kind": "oauth",
    }
    r = publish_credentials(payload, auth_path=auth, packs=[])
    assert r.get("pool_key")
    assert r.get("path") is None
    assert (auth).is_file()
    assert "pack-split@example.com" in auth.read_text(encoding="utf-8")


def test_export_auth_pool_cpa_files(tmp_path):
    from grokreg.auth_pool import upsert
    from grokreg.backends.export import export_auth_pool

    auth = tmp_path / "auth.json"
    out = tmp_path / "cpa_export"
    upsert(
        auth,
        {
            "email": "ex@example.com",
            "access_token": "at",
            "refresh_token": "rt",
            "type": "xai",
            "auth_kind": "oauth",
        },
    )
    stats = export_auth_pool("cpa_files", auth, out_dir=out, dry_run=False)
    assert stats["ok"] == 1
    assert stats["fail"] == 0
    files = list(out.glob("xai-*.json"))
    assert len(files) == 1
    assert "ex@example.com" in files[0].read_text(encoding="utf-8")


def test_oauth_mint_uses_publish_from_export_package():
    """mint must import publish_credentials from backends.export (not a private path)."""
    src = (ROOT / "grokreg" / "oauth" / "mint.py").read_text(encoding="utf-8")
    assert "publish_credentials" in src
    assert "backends.export" in src or "from ..backends.export" in src
