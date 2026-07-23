"""Upload modules live under ops/; root is compat shim."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_upload_modules_in_ops():
    assert (ROOT / "grokreg" / "ops" / "cpa_upload.py").is_file()
    assert (ROOT / "grokreg" / "ops" / "sub2api_upload.py").is_file()


def test_root_upload_are_shims():
    for name in ("cpa_upload.py", "sub2api_upload.py"):
        src = (ROOT / "grokreg" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        defs = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert not defs, f"{name} must be shim, got defs"
        assert "from .ops." in src or "ops.cpa_upload" in src or "ops.sub2api_upload" in src


def test_export_cmds_imports_ops_upload():
    src = (ROOT / "grokreg" / "ops" / "export_cmds.py").read_text(encoding="utf-8")
    assert "from .cpa_upload import" in src
    assert "from .sub2api_upload import" in src
    assert "from ..cpa_upload import" not in src
    assert "from ..sub2api_upload import" not in src


def test_public_import_compat():
    from grokreg.cpa_upload import upload_all
    from grokreg.ops.cpa_upload import upload_all as u2
    from grokreg.sub2api_upload import upload_from_dir
    from grokreg.ops.sub2api_upload import upload_from_dir as u3

    assert upload_all is u2
    assert upload_from_dir is u3
