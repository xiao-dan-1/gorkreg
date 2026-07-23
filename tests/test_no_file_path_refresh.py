"""No public file-path cpa refresh (policy: no compat)."""
from __future__ import annotations

import importlib
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_file_refresh_module_gone():
    assert not (ROOT / "grokreg" / "oauth" / "legacy_file_refresh.py").exists()


def test_oauth_exports_refresh_tokens_only():
    oauth = importlib.import_module("grokreg.oauth")
    assert callable(oauth.refresh_tokens)
    assert "refresh" not in oauth.__all__
    # package attribute oauth.refresh is the submodule refresh.py if loaded — not a file-path API
    if hasattr(oauth, "refresh"):
        assert isinstance(oauth.refresh, types.ModuleType)
    assert not hasattr(oauth.refresh_tokens, "cpa_path")
