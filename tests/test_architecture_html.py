"""Architecture diagram stays aligned with oauth-only step ops."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "architecture.html"


def test_architecture_html_exists_and_well_formed():
    assert HTML.is_file()
    text = HTML.read_text(encoding="utf-8")
    assert text.strip().endswith("</html>")
    assert text.count("<svg") >= 2


def test_architecture_html_core_claims():
    text = HTML.read_text(encoding="utf-8")
    required = [
        "grokreg/oauth",
        "auth.json",
        "newest-first",
        "proxy-retries",
        "无 grokreg 协议 cpa",
        "from grokreg.oauth import",
        "可独立",  # use cases not forced pipeline
        "失败",
        "sso_roster",
        "export only",
        "ops/cpa_upload",
        "captcha/impl",
        # file-path refresh removed from public API
        "cpa_health",
        "暂不开发",
        "分步",
    ]
    missing = [k for k in required if k not in text]
    assert not missing, f"architecture.html missing: {missing}"


def test_architecture_html_one_shot_demoted():
    """One-shot must not dominate: no ENTRY box claiming primary path."""
    text = HTML.read_text(encoding="utf-8")
    assert "主路径" in text or "分步主路径" in text
    # must state temporary / not developing
    assert "暂不开发" in text
    # must not re-sell one-shot as recommended daily path
    assert "推荐日常（一条龙）" not in text
    assert "一条龙入口" not in text or "暂不" in text


def test_architecture_html_forbids_cpa_refresh_path():
    text = HTML.read_text(encoding="utf-8")
    assert "禁 refresh" in text or "禁读" in text or "不得读 cpa" in text or "✗" in text
