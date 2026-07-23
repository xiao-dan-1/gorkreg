"""env-check mint.proxy follows LOCAL_PROXY (MINT_PROXY cancelled)."""
from __future__ import annotations


def test_env_check_ready_for_mint_shows_local_proxy(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = {
        "_root": str(tmp_path),
        "proxy": {
            "default": "http://127.0.0.1:7890",
            "dynamic": {
                "chain_via": "http://127.0.0.1:7890",
                "enabled": False,
                "template": "",
            },
        },
        "cpa": {},
    }
    monkeypatch.setenv("LOCAL_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("MINT_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("YESCAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)

    from grokreg.ops import env_cmds

    env_cmds._cmd_env_check(cfg)
    out = capsys.readouterr().out
    assert "mint.proxy" in out
    assert "7890" in out
    assert "ready_for_mint:" in out
    assert "outbound=http://127.0.0.1:7890" in out
    assert "ready_for_mint:     yes (proxy=" not in out
