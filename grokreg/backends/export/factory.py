"""Pack export factory — cpa_files / sub2api (+ future packs).

Ledger publish lives in :mod:`grokreg.backends.export.ledger`.
This module only:

  - registers pack backends (PACK_BACKENDS)
  - builds exporters (get_export_backend)
  - batch-writes packs from auth.json (export_auth_pool)

Do NOT mint inside export. Do NOT put RT lifecycle here.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...errors import ConfigError
from .base import ExportBackend
from .cockpit import CockpitExport
from .cpa_files import CpaFilesExport
from .pool import AuthPoolExport
from .sub2api import Sub2ApiExport

# Same-tier pack targets: batch via export_auth_pool / --export
PACK_BACKENDS: dict[str, frozenset[str]] = {
    "cpa_files": frozenset(
        {"cpa_files", "local", "files", "cpa", "xai", "cliproxy"}
    ),
    "sub2api": frozenset({"sub2api", "s2a", "sub2"}),
    "cockpit": frozenset({"cockpit", "cp", "cockpit_tools", "antigravity_cockpit"}),
}
# flat alias set for membership
POOL_PACK_BACKENDS = frozenset().union(*PACK_BACKENDS.values())

_DEFAULT_OUT = {
    "cpa_files": "cpa_export",
    "sub2api": "sub2api_export",
    "cockpit": "cockpit_export",
}


def _normalize_pack_name(name: str | None) -> str | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for canonical, aliases in PACK_BACKENDS.items():
        if key == canonical or key in aliases:
            return canonical
    return None


def get_export_backend(
    name: str | None = "cpa_files",
    *,
    auth_dir: str | Path | None = None,
    auth_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    cfg: dict[str, Any] | None = None,
    include_model_mapping: bool = True,
) -> ExportBackend:
    """
    name:
      - cpa_files | cliproxy | xai → pack: cpa_export/xai-*.json
      - sub2api | s2a              → pack: sub2api_export/grok-*.json
      - cockpit | cp               → pack: cockpit_export/grok-*.json (+ grok_accounts.json)
      - auth_pool | pool           → ledger: auth.json (not pack)
    """
    key = (name or "cpa_files").strip().lower()
    pack = _normalize_pack_name(key)

    if pack == "cpa_files" or key in {
        "cpa_files",
        "local",
        "files",
        "cpa",
        "xai",
        "cliproxy",
    }:
        directory = out_dir or auth_dir
        if directory is None and isinstance(cfg, dict):
            cpa_cfg = cfg.get("cpa") or {}
            if isinstance(cpa_cfg, dict) and cpa_cfg.get("auth_dir"):
                directory = cpa_cfg.get("auth_dir")
        return CpaFilesExport(directory or "cpa_export")

    if pack == "sub2api" or key in {"sub2api", "s2a", "sub2"}:
        directory = out_dir or auth_dir
        if directory is None and isinstance(cfg, dict):
            s2 = cfg.get("sub2api") or {}
            if isinstance(s2, dict) and s2.get("export_dir"):
                directory = s2.get("export_dir")
        return Sub2ApiExport(
            directory or "sub2api_export",
            include_model_mapping=include_model_mapping,
        )

    if pack == "cockpit" or key in {
        "cockpit",
        "cp",
        "cockpit_tools",
        "antigravity_cockpit",
    }:
        directory = out_dir or auth_dir
        if directory is None and isinstance(cfg, dict):
            ck = cfg.get("cockpit") or {}
            if isinstance(ck, dict) and ck.get("export_dir"):
                directory = ck.get("export_dir")
        return CockpitExport(directory or "cockpit_export")

    if key in {"auth_pool", "pool", "auth"}:
        path = auth_path
        if path is None and isinstance(cfg, dict):
            path = cfg.get("auth_file") or cfg.get("auth_path")
        return AuthPoolExport(path)

    raise ConfigError(f"unknown export backend: {name!r}", code="export_backend")


def save_cpa_file(
    payload: dict[str, Any],
    *,
    auth_dir: str | Path = "cpa_export",
    filename: str | None = None,
) -> Path:
    """Write one xAI pack file only (no ledger). Used by --export cpa_files / optional packs."""
    return get_export_backend("cpa_files", out_dir=auth_dir).save_cpa(
        payload, filename=filename
    )


def export_entry_file(
    backend: str,
    entry: dict[str, Any],
    *,
    out_dir: str | Path | None = None,
    filename: str | None = None,
    cfg: dict[str, Any] | None = None,
    include_model_mapping: bool = True,
) -> Path:
    """Write one pack via backend.export_entry."""
    pack = _normalize_pack_name(backend)
    if pack is None and (backend or "").strip().lower() in {"auth_pool", "pool", "auth"}:
        raise ConfigError(
            "auth_pool is ledger, not pack; use sync_cpa_to_pool / upsert",
            code="export_backend",
        )
    be = get_export_backend(
        backend,
        out_dir=out_dir,
        auth_dir=out_dir,
        cfg=cfg,
        include_model_mapping=include_model_mapping,
    )
    return be.export_entry(entry, filename=filename)


def export_sub2api_file(
    entry: dict[str, Any],
    *,
    out_dir: str | Path = "sub2api_export",
    filename: str | None = None,
    include_model_mapping: bool = True,
) -> Path:
    return export_entry_file(
        "sub2api",
        entry,
        out_dir=out_dir,
        filename=filename,
        include_model_mapping=include_model_mapping,
    )


def export_auth_pool(
    backend: str,
    auth_path: Path | str,
    *,
    out_dir: Path | str | None = None,
    only: str | None = None,
    include_disabled: bool = False,
    require_refresh_token: bool = True,
    dry_run: bool = False,
    include_model_mapping: bool = True,
    cfg: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Batch-export auth.json entries through a **pack** backend.

    Supported packs: cpa_files | sub2api (same tier).
    auth_pool is ledger-only and is rejected here.
    """
    from ...auth_pool import list_entries

    pack = _normalize_pack_name(backend)
    if pack is None:
        key = (backend or "").strip().lower()
        if key in {"auth_pool", "pool", "auth"}:
            raise ConfigError(
                "auth_pool is ledger, not pack export; use --auth-import / sync_cpa_to_pool",
                code="export_backend",
            )
        raise ConfigError(
            f"unknown pack export backend: {backend!r} "
            f"(packs: cpa_files, sub2api, cockpit)",
            code="export_backend",
        )

    auth_path = Path(auth_path)
    if out_dir is None:
        out_dir = _DEFAULT_OUT.get(pack, f"export_{pack}")
    out_dir = Path(out_dir)

    be = get_export_backend(
        pack,
        out_dir=out_dir,
        auth_dir=out_dir if pack in {"cpa_files", "cockpit"} else None,
        cfg=cfg,
        include_model_mapping=include_model_mapping,
    )

    entries = list_entries(
        auth_path,
        include_disabled=include_disabled,
        include_expired=True,
    )
    only_q = (only or "").strip().lower()
    if only_q and only_q not in {"all", "*"}:
        entries = [
            (k, e)
            for k, e in entries
            if only_q in (e.get("email") or "").lower()
            or (e.get("email") or "").lower() == only_q
        ]
    limit_n = 0
    try:
        if limit is not None and int(limit) > 0:
            limit_n = int(limit)
            entries = entries[:limit_n]
    except (TypeError, ValueError):
        limit_n = 0

    stats: dict[str, Any] = {
        "backend": pack,
        "kind": "pack",
        "total": len(entries),
        "ok": 0,
        "skip": 0,
        "fail": 0,
        "out_dir": str(out_dir),
        "auth_path": str(auth_path),
        "dry_run": bool(dry_run),
        "limit": limit_n or 0,
        "files": [],
        "errors": [],
    }

    for _k, entry in entries:
        email = (entry.get("email") or "").strip()
        if not email:
            stats["skip"] += 1
            continue
        if entry.get("disabled") and not include_disabled:
            stats["skip"] += 1
            continue
        if require_refresh_token and not (entry.get("refresh_token") or "").strip():
            stats["skip"] += 1
            stats["errors"].append({"email": email, "error": "no_refresh_token"})
            continue
        try:
            if dry_run:
                if hasattr(be, "preview_path"):
                    preview = str(be.preview_path(email))
                else:
                    preview = str(out_dir / email)
                stats["ok"] += 1
                stats["files"].append(preview)
                continue
            path = be.export_entry(entry)
            stats["ok"] += 1
            stats["files"].append(str(path))
        except Exception as e:
            stats["fail"] += 1
            stats["errors"].append({"email": email, "error": str(e)[:200]})

    stats["exported_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return stats


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (ledger API used to live here)
# ---------------------------------------------------------------------------
from .ledger import publish_credentials, sync_cpa_to_pool, upsert_payload_to_pool  # noqa: E402

__all__ = [
    "PACK_BACKENDS",
    "POOL_PACK_BACKENDS",
    "get_export_backend",
    "save_cpa_file",
    "export_entry_file",
    "export_sub2api_file",
    "export_auth_pool",
    "publish_credentials",
    "upsert_payload_to_pool",
    "sync_cpa_to_pool",
    "_normalize_pack_name",
]
