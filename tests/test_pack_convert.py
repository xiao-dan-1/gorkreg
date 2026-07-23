"""CPA ↔ sub2api pack converters + provider guards."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from grokreg.backends.export.convert import (
    convert_paths,
    cpa_to_sub2api_document,
    detect_cpa_provider,
    detect_kind,
    detect_sub_provider,
    is_supported_cpa,
    is_supported_sub_account,
    sub2api_account_to_cpa,
    sub2api_to_cpa_payloads,
)
from grokreg.backends.export.xai_pack.schema import build_xai_auth
from grokreg.errors import ConfigError


def _sample_cpa(email: str = "alice@example.com") -> dict:
    return build_xai_auth(
        email=email,
        access_token="at-sample-token-not-jwt",
        refresh_token="rt-sample-refresh",
        id_token="id-sample",
        expires_in=21600,
        expired="2026-07-18T00:00:00Z",
        sub="sub-alice",
    )


def _sample_openai_oauth(email: str = "gpt@example.com") -> dict:
    """Real-shape sub2api openai oauth account (from converted-sub2api samples)."""
    return {
        "name": email,
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": "eyJhbGci-openai-at",
            "refresh_token": "rt.1.openai-refresh",
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "chatgpt_account_id": "c6197d14-2b4d-4039-87f2-5c3a8f3dc2d3",
            "plan_type": "team",
            "organization_id": "",
            "expires_at": 1781604798,
            "expires_in": 863999,
            "model_mapping": {"gpt-5.5": "gpt-5.5"},
        },
        "extra": {"load_factor": 10},
        "concurrency": 1,
        "priority": 1,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }


def _sample_openai_apikey(name: str = "sk-test-account") -> dict:
    return {
        "name": name,
        "platform": "openai",
        "type": "apikey",
        "credentials": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-not-real",
        },
        "concurrency": 10,
        "priority": 1,
        "rate_multiplier": 1.0,
    }


def _sample_cpa_codex(email: str = "codex@example.com") -> dict:
    """CLIProxy codex TokenStorage-like flat file (type=codex, not xai)."""
    return {
        "type": "codex",
        "access_token": "at-codex",
        "refresh_token": "rt-codex",
        "id_token": "id-codex",
        "email": email,
        "account_id": "acct-1",
        "expired": "2026-08-01T00:00:00Z",
        "last_refresh": "2026-07-17T00:00:00Z",
    }


def test_detect_kind_cpa_and_envelope():
    cpa = _sample_cpa()
    assert detect_kind(cpa) == "cpa"
    doc = cpa_to_sub2api_document(cpa)
    assert detect_kind(doc) == "sub2api_envelope"
    assert set(doc.keys()) >= {"exported_at", "proxies", "accounts"}
    acc = doc["accounts"][0]
    assert detect_kind(acc) == "sub2api_account"
    assert acc["platform"] == "grok"
    assert acc["type"] == "oauth"
    assert acc["credentials"]["email"] == "alice@example.com"
    assert acc["credentials"]["refresh_token"] == "rt-sample-refresh"


def test_roundtrip_tokens_preserved():
    cpa = _sample_cpa("bob@bj01.xdauv.xyz")
    doc = cpa_to_sub2api_document(cpa)
    back = sub2api_to_cpa_payloads(doc)
    assert len(back) == 1
    b = back[0]
    assert b["type"] == "xai"
    assert b["auth_kind"] == "oauth"
    assert b["email"] == "bob@bj01.xdauv.xyz"
    assert b["access_token"] == "at-sample-token-not-jwt"
    assert b["refresh_token"] == "rt-sample-refresh"
    assert b["base_url"].endswith("/v1")
    assert b["headers"]


def test_convert_paths_cpa_to_sub_and_back(tmp_path: Path):
    cpa_dir = tmp_path / "cpa"
    sub_dir = tmp_path / "sub"
    back_dir = tmp_path / "cpa2"
    cpa_dir.mkdir()
    cpa = _sample_cpa("carol@outlook.com")
    (cpa_dir / "xai-carol@outlook.com.json").write_text(
        json.dumps(cpa, indent=2) + "\n", encoding="utf-8"
    )

    s1 = convert_paths("cpa-to-sub", [cpa_dir], out_dir=sub_dir)
    assert s1["ok"] == 1
    assert s1["fail"] == 0
    out_files = list(sub_dir.glob("grok-*.json"))
    assert len(out_files) == 1
    env = json.loads(out_files[0].read_text(encoding="utf-8"))
    assert "exported_at" in env and "accounts" in env

    s2 = convert_paths("sub-to-cpa", [sub_dir], out_dir=back_dir)
    assert s2["ok"] == 1
    assert s2["fail"] == 0
    back_files = list(back_dir.glob("xai-*.json"))
    assert len(back_files) == 1
    back = json.loads(back_files[0].read_text(encoding="utf-8"))
    assert back["email"] == "carol@outlook.com"
    assert back["refresh_token"] == "rt-sample-refresh"
    assert back["type"] == "xai"


def test_merge_envelope(tmp_path: Path):
    cpa_dir = tmp_path / "cpa"
    out = tmp_path / "sub"
    cpa_dir.mkdir()
    for em in ("a@x.com", "b@x.com"):
        payload = _sample_cpa(em)
        (cpa_dir / f"xai-{em}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    stats = convert_paths(
        "cpa-to-sub", [cpa_dir], out_dir=out, merge_envelope=True
    )
    assert stats["ok"] == 2
    merged = out / "grok-merged-export.json"
    assert merged.is_file()
    doc = json.loads(merged.read_text(encoding="utf-8"))
    assert len(doc["accounts"]) == 2
    assert all(a["platform"] == "grok" for a in doc["accounts"])


def test_missing_rt_fails_sub_to_cpa():
    bare = {
        "name": "no-rt@x.com",
        "platform": "grok",
        "type": "oauth",
        "credentials": {
            "email": "no-rt@x.com",
            "access_token": "at-only",
            "refresh_token": "",
            "base_url": "https://cli-chat-proxy.grok.com/v1",
            "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
            "token_type": "Bearer",
        },
        "extra": {"email": "no-rt@x.com"},
    }
    with pytest.raises(Exception) as ei:
        sub2api_to_cpa_payloads(bare)
    assert "refresh_token" in str(ei.value).lower() or "missing" in str(ei.value).lower()


def test_openai_oauth_not_rewritten_as_xai():
    acc = _sample_openai_oauth()
    assert detect_sub_provider(acc) == "openai"
    ok, reason, prov = is_supported_sub_account(acc)
    assert not ok
    assert prov == "openai"
    assert "openai" in reason
    with pytest.raises(ConfigError) as ei:
        sub2api_account_to_cpa(acc)
    assert ei.value.code == "pack_convert_platform"
    assert "openai" in str(ei.value).lower()


def test_openai_apikey_not_rewritten_as_xai():
    acc = _sample_openai_apikey()
    assert detect_sub_provider(acc) == "openai"
    with pytest.raises(ConfigError):
        sub2api_account_to_cpa(acc)


def test_cpa_codex_not_converted_to_grok():
    cpa = _sample_cpa_codex()
    assert detect_kind(cpa) == "cpa"
    assert detect_cpa_provider(cpa) == "openai"
    ok, reason, prov = is_supported_cpa(cpa)
    assert not ok and prov == "openai"
    with pytest.raises(ConfigError) as ei:
        cpa_to_sub2api_document(cpa)
    assert ei.value.code == "pack_convert_platform"


def test_batch_skips_openai_keeps_xai(tmp_path: Path):
    """Mixed dir: xai converts; openai/codex skipped — no xai-*.json from openai."""
    src = tmp_path / "mixed"
    out = tmp_path / "out"
    src.mkdir()

    # xai CPA
    (src / "xai-good@x.com.json").write_text(
        json.dumps(_sample_cpa("good@x.com")), encoding="utf-8"
    )
    # codex CPA (must not become grok)
    (src / "codex-bad@x.com.json").write_text(
        json.dumps(_sample_cpa_codex("bad@x.com")), encoding="utf-8"
    )

    s = convert_paths("cpa-to-sub", [src], out_dir=out)
    assert s["ok"] == 1
    assert s["skip_unsupported"] >= 1
    assert s["fail"] == 0
    written = list(out.glob("*.json"))
    assert len(written) == 1
    doc = json.loads(written[0].read_text(encoding="utf-8"))
    assert doc["accounts"][0]["platform"] == "grok"
    assert doc["accounts"][0]["credentials"]["email"] == "good@x.com"


def test_batch_sub_mixed_envelope_skips_openai(tmp_path: Path):
    """Envelope with grok + openai accounts: only grok becomes xai-*.json."""
    src = tmp_path / "sub"
    out = tmp_path / "cpa"
    src.mkdir()
    grok = cpa_to_sub2api_document(_sample_cpa("g@x.com"))["accounts"][0]
    env = {
        "exported_at": "2026-07-17T00:00:00Z",
        "proxies": [],
        "accounts": [grok, _sample_openai_oauth("o@x.com")],
    }
    (src / "mixed.json").write_text(json.dumps(env), encoding="utf-8")

    s = convert_paths("sub-to-cpa", [src], out_dir=out)
    assert s["ok"] == 1
    assert s["skip_unsupported"] >= 1
    files = list(out.glob("*.json"))
    assert len(files) == 1
    assert files[0].name.startswith("xai-")
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["type"] == "xai"
    assert body["email"] == "g@x.com"
    # never wrote openai tokens as xai
    assert body["access_token"] != "eyJhbGci-openai-at"


def test_strict_openai_counts_as_fail(tmp_path: Path):
    src = tmp_path / "cpa"
    out = tmp_path / "sub"
    src.mkdir()
    (src / "codex-x.json").write_text(
        json.dumps(_sample_cpa_codex()), encoding="utf-8"
    )
    s = convert_paths("cpa-to-sub", [src], out_dir=out, strict=True)
    assert s["fail"] == 1
    assert s["ok"] == 0
    assert list(out.glob("*.json")) == []
