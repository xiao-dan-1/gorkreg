"""xAI / CLIProxy pack helpers live under backends.export — not package root."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_xai_pack_canonical_under_export():
    pack = ROOT / "grokreg" / "backends" / "export" / "xai_pack"
    assert (pack / "schema.py").is_file()
    assert (pack / "writer.py").is_file()
    from grokreg.backends.export.xai_pack import (
        build_xai_auth,
        credential_file_name,
        write_xai_auth,
    )

    assert callable(build_xai_auth)
    assert callable(write_xai_auth)
    assert callable(credential_file_name)


def test_no_root_cpa_xai_or_xai_pack_package():
    """Hard cut: no top-level grokreg.backends.export.xai_pack / grokreg.xai_pack package."""
    assert not (ROOT / "grokreg" / "cpa_xai").exists()
    assert not (ROOT / "grokreg" / "xai_pack").exists()


def test_export_cpa_files_imports_sibling_xai_pack():
    src = (ROOT / "grokreg" / "backends" / "export" / "cpa_files.py").read_text(
        encoding="utf-8"
    )
    assert "from .xai_pack" in src
    assert "cpa_xai" not in src
