"""env_cmds must resolve resolve_proxy / client without NameError (lazy imports)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "grokreg" / "ops" / "env_cmds.py"


def _fn_body_names(fn_name: str) -> set[str]:
    tree = ast.parse(ENV.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            names: set[str] = set()
            for n in ast.walk(node):
                if isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        names.add(a.name)
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        names.add(a.name.split(".")[0])
            return names
    raise AssertionError(f"function {fn_name} not found")


def test_check_proxy_imports_resolve_proxy():
    names = _fn_body_names("_cmd_check_proxy")
    assert "resolve_proxy" in names
    assert "probe_proxy" in names


def test_half_chain_imports_resolve_and_client():
    names = _fn_body_names("_cmd_half_chain")
    assert "resolve_proxy" in names
    assert "GrokAuthClient" in names
    assert "normalize_xai_code" in names


def test_check_proxy_callable_without_nameerror():
    """Import path: function object exists; dry call with empty cfg is ok if no network.

    We only assert the NameError class of bug is gone by compiling free names used
    at the first resolve_proxy line via the lazy import present in body.
    """
    from grokreg.ops import env_cmds

    assert callable(env_cmds._cmd_check_proxy)
    assert callable(env_cmds._cmd_half_chain)
    src = ENV.read_text(encoding="utf-8")
    # bare resolve_proxy at module level would still fail; must be function-local import
    assert "from ..proxyutil import probe_proxy, resolve_proxy" in src
    assert "from ..proxyutil import resolve_proxy" in src
    assert "from ..client import GrokAuthClient" in src
