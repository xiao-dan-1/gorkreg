"""Guard: mint_cmds must import every free name it uses from helpers."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINT = ROOT / "grokreg" / "ops" / "mint_cmds.py"


def test_mint_cmds_defines_or_imports_tally_and_helpers():
    src = MINT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)

    # critical helpers used by _cmd_mint body
    for name in ("_tally", "_auth_path", "_read_sso_roster", "select_mint_todos"):
        # select_mint_todos may be imported inside function — allow either
        if name == "select_mint_todos":
            assert name in src
            continue
        assert name in imported or name in defined, f"mint_cmds missing {name}"


def test_mint_cmds_no_prod_pipeline_dependency():
    src = MINT.read_text(encoding="utf-8")
    assert "prod_pipeline" not in src
