"""Mail dual-path debt: root modules must stay thin shims over backends."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Root mail entrypoints that must not grow real implementations again.
_ROOT_SHIMS = (
    "grokreg/mail.py",
    "grokreg/mail_cloudmail.py",
    "grokreg/mail_imap.py",
)


def _module_source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_root_mail_modules_are_shims_only():
    """Root mail*.py must re-export backends — no local class/function bodies."""
    for rel in _ROOT_SHIMS:
        src = _module_source(rel)
        tree = ast.parse(src)
        defs = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert not defs, f"{rel} must not define functions/classes (got {[getattr(d,'name', d) for d in defs]})"
        # must import from backends.mail
        imports = []
        for n in tree.body:
            if isinstance(n, (ast.ImportFrom,)):
                imports.append(n.module or "")
        joined = " ".join(imports)
        assert "backends.mail" in joined or any(
            (m or "").startswith("backends.mail") or (m or "").endswith("backends.mail")
            or "backends" in (m or "")
            for m in imports
        ), f"{rel} must import from backends.mail, got {imports}"
        assert "from .backends.mail" in src or "from grokreg.backends.mail" in src


def test_backends_mail_is_canonical_package():
    pkg = ROOT / "grokreg" / "backends" / "mail"
    assert (pkg / "__init__.py").is_file()
    for name in ("base.py", "factory.py", "cloudmail.py", "graph.py", "imap.py", "codes.py"):
        assert (pkg / name).is_file(), name


def test_new_code_should_prefer_backends_factory():
    """pipeline.register already uses backends; keep that as the pattern."""
    src = _module_source("grokreg/pipeline/register.py")
    assert "backends.mail" in src or "get_mail_backend" in src or "backends" in src
    # must not open-code MSMailClient from a non-shim path in register core
    # (compat re-exports via mail_cloudmail are OK in scripts)
    assert "from ..backends" in src or "from grokreg.backends" in src
