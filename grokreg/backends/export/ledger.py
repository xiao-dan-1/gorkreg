"""Ledger helpers — auth.json upsert / publish (not pack export).

Layers::

  mint → publish_credentials → auth.json          # this module
  auth.json → factory.export_auth_pool → packs    # factory.py
  packs → ops upload                              # ops/*

xAI pack files (cpa_export/) are optional side products of publish when
``packs=["cpa_files"]``; default is ledger-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import ConfigError


def upsert_payload_to_pool(
    payload: dict[str, Any],
    *,
    auth_path: str | Path = "auth.json",
    cpa_path: str | Path | None = None,
) -> str:
    """Upsert RT/AT payload directly into auth.json (no pack file required)."""
    from ...auth_pool import cpa_to_entry, upsert

    if not isinstance(payload, dict) or not payload:
        raise ConfigError("empty credentials payload", code="export_publish")
    key, entry = cpa_to_entry(payload, cpa_path=cpa_path)
    return upsert(auth_path, entry, key=key)


def sync_cpa_to_pool(
    cpa_path: Path | str,
    *,
    auth_path: str | Path | None = None,
) -> str:
    """Ledger convenience: upsert one CPA file into auth.json."""
    from .factory import get_export_backend

    return get_export_backend("auth_pool", auth_path=auth_path).upsert_cpa(cpa_path)


def publish_credentials(
    payload: dict[str, Any],
    *,
    auth_path: str | Path | None = "auth.json",
    packs: list[str] | None = None,
    auth_dir: str | Path = "cpa_export",
    filename: str | None = None,
) -> dict[str, Any]:
    """After mint: put credentials in the ledger; packs are optional.

    Default (user architecture)::

        RT/AT → auth.json only
        # cpa_files / sub2api only when requested:
        packs=["cpa_files"]  or later --export

    Returns {pool_key, path, paths, email} without token bodies.
    ``path`` is first pack path if any pack written, else None.
    """
    # Local imports keep pack factory free of ledger cycles at import time
    # when only export_auth_pool is needed; publish still can call packs.
    from .factory import (
        _normalize_pack_name,
        export_entry_file,
        save_cpa_file,
    )

    if not isinstance(payload, dict) or not payload:
        raise ConfigError("empty credentials payload", code="export_publish")

    email = (payload.get("email") or "").strip()
    pool_key: str | None = None
    cpa_paths: list[str] = []

    # 1) ledger first (source of truth) — does NOT require cpa files
    if auth_path is not None:
        pool_key = upsert_payload_to_pool(payload, auth_path=auth_path)

    # 2) optional same-tier packs (default none)
    wanted = [str(x).strip().lower() for x in (packs or []) if str(x).strip()]
    for name in wanted:
        pack = _normalize_pack_name(name) or name
        if pack == "cpa_files" or name in {"cpa_files", "cpa", "local", "files", "cpa"}:
            p = save_cpa_file(payload, auth_dir=auth_dir, filename=filename)
            cpa_paths.append(str(p))
            # if ledger already written without cpa_path, refresh entry cpa_path
            if auth_path is not None and pool_key:
                try:
                    upsert_payload_to_pool(payload, auth_path=auth_path, cpa_path=p)
                except Exception:
                    pass
        elif pack == "sub2api" or name in {"sub2api", "s2a", "sub2"}:
            p = export_entry_file(
                "sub2api",
                payload,
                out_dir="sub2api_export",
            )
            cpa_paths.append(str(p))
        elif pack == "cockpit" or name in {
            "cockpit",
            "cp",
            "cockpit_tools",
            "antigravity_cockpit",
        }:
            p = export_entry_file(
                "cockpit",
                payload,
                out_dir="cockpit_export",
            )
            cpa_paths.append(str(p))
        else:
            raise ConfigError(f"unknown pack in publish: {name!r}", code="export_publish")

    return {
        "path": cpa_paths[0] if cpa_paths else None,
        "paths": cpa_paths,
        "pool_key": pool_key,
        "email": email,
        "auth_path": None if auth_path is None else str(auth_path),
        "packs": wanted,
    }
