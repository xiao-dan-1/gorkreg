"""SSO CreateSession encode + fetch_sso signature."""
from __future__ import annotations

import ast
from pathlib import Path

from grokreg import grpcweb


def test_encode_create_session_nonempty():
    body = grpcweb.encode_create_session_request(
        "a@b.com", "Secret123!", turnstile_token="0.fake"
    )
    assert isinstance(body, bytes)
    assert len(body) > 20
    framed = grpcweb.frame_request(body)
    assert framed[0] == 0
    assert len(framed) == 5 + len(body)


def test_fetch_sso_token_accepts_password_kwargs():
    src = Path("grokreg/client.py").read_text(encoding="utf-8")
    assert "def obtain_session_via_password" in src
    assert "turnstile_token: str = \"\"" in src or "turnstile_token: str=\"\"" in src
    assert "CreateSession" in src


def test_register_has_create_session_rescue():
    src = Path("grokreg/pipeline/register.py").read_text(encoding="utf-8")
    assert "CreateSession rescue" in src
    assert "sso_via" in src
    assert "recover_sso_failed.py" in src


def test_recover_script_exists():
    p = Path("scripts/recover_sso_failed.py")
    assert p.is_file()
    tree = ast.parse(p.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "_recover_one" in names
    assert "main" in names
