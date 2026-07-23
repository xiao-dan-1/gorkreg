"""Mint/refresh/probe proxy resolution — LOCAL_PROXY default (no MINT_PROXY)."""
from __future__ import annotations

from types import SimpleNamespace


def test_resolve_mint_proxy_priority(monkeypatch):
    from grokreg.ops.mint_proxy import resolve_mint_proxy

    for k in (
        "MINT_PROXY",
        "PROBE_PROXY",
        "LOCAL_PROXY",
        "PROXY_DEFAULT",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(k, raising=False)

    # CLI wins
    assert resolve_mint_proxy(SimpleNamespace(mint_proxy="http://cli:1")) == "http://cli:1"

    # LOCAL_PROXY preferred (MINT_PROXY ignored even if set)
    monkeypatch.setenv("MINT_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("LOCAL_PROXY", "http://127.0.0.1:7890")
    assert resolve_mint_proxy(SimpleNamespace(mint_proxy=None)) == "http://127.0.0.1:7890"

    # no LOCAL → PROXY_DEFAULT
    monkeypatch.delenv("LOCAL_PROXY", raising=False)
    monkeypatch.setenv("PROXY_DEFAULT", "http://127.0.0.1:7891")
    assert resolve_mint_proxy(SimpleNamespace(mint_proxy="")) == "http://127.0.0.1:7891"

    # default 7890
    monkeypatch.delenv("PROXY_DEFAULT", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    assert resolve_mint_proxy(SimpleNamespace(mint_proxy=None)) == "http://127.0.0.1:7890"

    # cfg proxy.default
    assert (
        resolve_mint_proxy(
            None,
            cfg={"proxy": {"default": "http://127.0.0.1:7999"}},
        )
        == "http://127.0.0.1:7999"
    )


def test_mint_cmds_uses_local_proxy(monkeypatch, tmp_path):
    """_cmd_mint uses LOCAL_PROXY via resolve_mint_proxy."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sso_roster.txt").write_text(
        "a@x.com----pw----sso-token\n", encoding="utf-8"
    )
    seen = {}

    def fake_mint(**kwargs):
        seen["proxy"] = kwargs.get("proxy")
        return {"ok": True, "email": kwargs.get("email")}

    monkeypatch.setenv("LOCAL_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("MINT_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    import grokreg.ops.mint_cmds as mc
    import grokreg.oauth as oauth

    monkeypatch.setattr(oauth, "mint", fake_mint)

    args = SimpleNamespace(
        mint="all",
        mint_missing=True,
        mint_proxy=None,
        mint_out_dir=None,
        mint_write_cpa=False,
        no_probe=True,
        mint_probe_mode=None,
        jobs=1,
        limit=None,
        auth_file=None,
    )
    rc = mc._cmd_mint({"_root": str(tmp_path)}, args)
    assert rc == 0
    assert seen.get("proxy") == "http://127.0.0.1:7890"
