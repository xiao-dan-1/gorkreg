"""Web proxy store + mint without confirm."""
from pathlib import Path

import os


def test_proxy_store_roundtrip(tmp_path, monkeypatch):
    import client.proxy_store as ps

    monkeypatch.setattr(ps, "STORE_PATH", tmp_path / "proxy_config.json")
    st = ps.save_config(
        local_proxy="http://127.0.0.1:7890",
        dynamic_template="user-region-US-sid-abc:pass@host:2000",
        dynamic_enabled=True,
    )
    assert st["dynamic_enabled"] is True
    assert st["dynamic_template_set"] is True
    assert os.environ.get("PROXY_DYNAMIC_ENABLED") == "true"
    st2 = ps.save_config(dynamic_enabled=False)
    assert st2["dynamic_enabled"] is False
    assert os.environ.get("PROXY_DYNAMIC_ENABLED") == "false"


def test_no_mint_bulk_confirm_in_main():
    main = Path("client/static/js/main.js").read_text(encoding="utf-8")
    assert "只写 auth.json，不写 cpa pack。继续？" not in main
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    assert "px-dyn-en" in html and "settings-proxy-card" in html
