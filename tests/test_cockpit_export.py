"""Cockpit Tools pack export — same tier as cpa_files / sub2api."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from grokreg.auth_pool import upsert
from grokreg.backends.export import (
    PACK_BACKENDS,
    export_auth_pool,
    get_export_backend,
    publish_credentials,
)
from grokreg.backends.export.cockpit import (
    BATCH_FILENAME,
    CockpitExport,
    entry_to_cockpit_account,
    stable_account_id,
)
from grokreg.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def _fake_jwt(*, sub: str, exp: int, principal_id: str | None = None, team_id: str = "") -> str:
    """Minimal unsigned JWT for claim parsing (not for network)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = {
        "sub": sub,
        "principal_id": principal_id or sub,
        "principal_type": "User",
        "team_id": team_id,
        "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
        "aud": "b1a00492-073a-47ea-816f-4c329264a828",
        "iss": "https://auth.x.ai",
        "exp": exp,
        "iat": exp - 21600,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def _fake_id_token(*, sub: str, email: str, given: str = "Ada", family: str = "Lovelace") -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = {
        "sub": sub,
        "email": email,
        "given_name": given,
        "family_name": family,
        "iss": "https://auth.x.ai",
        "aud": "b1a00492-073a-47ea-816f-4c329264a828",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def _sample_entry(email: str = "cockpit@example.com") -> dict:
    sub = "11111111-2222-3333-4444-555555555555"
    exp = int(time.time()) + 7200
    return {
        "email": email,
        "access_token": _fake_jwt(sub=sub, exp=exp, team_id="team-abc"),
        "refresh_token": "rt-fake-cockpit-refresh-not-real",
        "id_token": _fake_id_token(sub=sub, email=email),
        "type": "xai",
        "auth_kind": "oauth",
        "sub": sub,
        "expires_at": exp,
        "token_endpoint": "https://auth.x.ai/oauth2/token",
    }


def test_pack_backends_includes_cockpit():
    assert "cockpit" in PACK_BACKENDS
    assert "cp" in PACK_BACKENDS["cockpit"]
    be = get_export_backend("cockpit")
    assert isinstance(be, CockpitExport)
    assert be.kind == "pack"
    assert get_export_backend("cp").name == "cockpit"
    assert get_export_backend("cliproxy").name == "cpa_files"


def test_entry_to_cockpit_required_fields():
    entry = _sample_entry()
    acc = entry_to_cockpit_account(entry, now_ms=1_700_000_000_000)
    assert acc["email"] == "cockpit@example.com"
    assert acc["auth_mode"] == "oauth"
    assert acc["refresh_token"] == "rt-fake-cockpit-refresh-not-real"
    assert acc["access_token"]
    assert acc["token_type"] == "Bearer"
    assert isinstance(acc["expires_at"], int) and acc["expires_at"] > 0
    assert acc["expires_at_raw"].endswith("+00:00")
    assert acc["oidc_issuer"] == "https://auth.x.ai"
    assert acc["oidc_client_id"] == "b1a00492-073a-47ea-816f-4c329264a828"
    assert acc["token_endpoint"] == "https://auth.x.ai/oauth2/token"
    assert acc["user_id"] == "11111111-2222-3333-4444-555555555555"
    assert acc["principal_id"] == acc["user_id"]
    assert acc["principal_type"] == "User"
    assert acc["team_id"] == "team-abc"
    assert acc["first_name"] == "Ada"
    assert acc["last_name"] == "Lovelace"
    assert acc["id"] == stable_account_id(entry["email"])
    assert acc["created_at"] == 1_700_000_000_000
    assert "auth_raw" in acc
    assert acc["auth_raw"]["refresh_token"] == acc["refresh_token"]
    assert acc["auth_raw"]["key"] == acc["access_token"]
    assert acc["subscription_raw"] == {"subscriptions": []}
    # no fake quota probe data
    assert "quota" not in acc
    assert "billing_raw" not in acc


def test_entry_requires_tokens():
    with pytest.raises(ConfigError):
        entry_to_cockpit_account({"email": "x@y.com"})


def test_export_entry_writes_file_and_batch(tmp_path: Path):
    be = CockpitExport(tmp_path)
    path = be.export_entry(_sample_entry("a@example.com"))
    assert path.is_file()
    assert path.name.startswith("grok-")
    one = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(one, dict)
    assert one["email"] == "a@example.com"

    batch = tmp_path / BATCH_FILENAME
    assert batch.is_file()
    arr = json.loads(batch.read_text(encoding="utf-8"))
    assert isinstance(arr, list) and len(arr) == 1
    assert arr[0]["email"] == "a@example.com"

    # second email upserts into batch
    be.export_entry(_sample_entry("b@example.com"))
    arr2 = json.loads(batch.read_text(encoding="utf-8"))
    assert len(arr2) == 2
    emails = {x["email"] for x in arr2}
    assert emails == {"a@example.com", "b@example.com"}

    # re-export same email replaces, not duplicates
    be.export_entry(_sample_entry("a@example.com"))
    arr3 = json.loads(batch.read_text(encoding="utf-8"))
    assert len(arr3) == 2
    assert sum(1 for x in arr3 if x["email"] == "a@example.com") == 1


def test_export_auth_pool_cockpit(tmp_path: Path):
    auth = tmp_path / "auth.json"
    out = tmp_path / "cockpit_export"
    upsert(auth, _sample_entry("pool@example.com"))
    stats = export_auth_pool("cockpit", auth, out_dir=out, dry_run=False)
    assert stats["backend"] == "cockpit"
    assert stats["ok"] == 1
    assert stats["fail"] == 0
    files = list(out.glob("grok-*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["refresh_token"]
    assert body["auth_mode"] == "oauth"
    batch = out / BATCH_FILENAME
    assert batch.is_file()
    assert len(json.loads(batch.read_text(encoding="utf-8"))) == 1


def test_export_auth_pool_dry_run(tmp_path: Path):
    auth = tmp_path / "auth.json"
    out = tmp_path / "cockpit_export"
    upsert(auth, _sample_entry("dry@example.com"))
    stats = export_auth_pool("cockpit", auth, out_dir=out, dry_run=True)
    assert stats["ok"] == 1
    assert stats["dry_run"] is True
    assert not list(out.glob("*.json"))


def test_publish_credentials_optional_cockpit_pack(tmp_path: Path):
    auth = tmp_path / "auth.json"
    # chdir so default cockpit_export is under tmp if used; we pass via export_entry_file path
    # publish with packs=["cockpit"] writes under cwd cockpit_export — use explicit export_auth_pool instead
    r = publish_credentials(_sample_entry("pub@example.com"), auth_path=auth, packs=[])
    assert r["pool_key"]
    assert r["path"] is None
    assert auth.is_file()

    # optional pack path: export_entry_file via publish packs
    # publish writes to fixed "cockpit_export" relative cwd — isolate with monkeypatch via factory
    out = tmp_path / "cockpit_export"
    stats = export_auth_pool("cockpit", auth, out_dir=out)
    assert stats["ok"] == 1


def test_stable_id_deterministic():
    a = stable_account_id("Same@Example.com")
    b = stable_account_id("same@example.com")
    # email lowercased inside stable_account_id
    assert a == b
    assert a != stable_account_id("other@example.com")


def test_module_is_pack_not_ledger():
    src = (ROOT / "grokreg" / "backends" / "export" / "cockpit.py").read_text(encoding="utf-8")
    assert "kind = \"pack\"" in src
    assert "def mint" not in src
    assert "refresh_tokens" not in src
