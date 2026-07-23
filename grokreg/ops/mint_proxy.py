"""Proxy for mint / refresh / probe — same local hop as register.

Priority (first non-empty):
  1. CLI --mint-proxy
  2. env LOCAL_PROXY
  3. env PROXY_DEFAULT
  4. env HTTPS_PROXY / https_proxy / HTTP_PROXY
  5. default http://127.0.0.1:7890  (same as register hop1)

MINT_PROXY is intentionally NOT read (cancelled; use LOCAL_PROXY).
"""
from __future__ import annotations

import os
from typing import Any


DEFAULT_LOCAL_PROXY = "http://127.0.0.1:7890"


def resolve_mint_proxy(args: Any | None = None, cfg: dict | None = None) -> str:
    """Resolve mint/refresh/probe proxy URL (defaults to LOCAL_PROXY / hop1)."""
    if args is not None:
        cli = (getattr(args, "mint_proxy", None) or "").strip()
        if cli:
            return cli
    for key in (
        "LOCAL_PROXY",
        "PROXY_DEFAULT",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    if cfg:
        px = (cfg.get("proxy") or {}) if isinstance(cfg.get("proxy"), dict) else {}
        d = str(px.get("default") or "").strip()
        if d:
            return d
        dyn = px.get("dynamic") or {}
        if isinstance(dyn, dict):
            via = str(dyn.get("chain_via") or "").strip()
            if via:
                return via
    return DEFAULT_LOCAL_PROXY
