"""Local xAI pack export: auth entry / mint payload → cpa_export/xai-<email>.json.

Same pack-export tier as sub2api (export factory).
  - primary: export_entry(auth_pool entry) / --export cpa_files
  - optional: mint(..., packs=["cpa_files"]) opt-in only
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...errors import ConfigError


def sanitize_email_for_filename(email: str) -> str:
    s = (email or "").strip()
    if not s:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9@._-]+", "-", s)


def cpa_filename(email: str) -> str:
    return f"xai-{sanitize_email_for_filename(email)}.json"


class CpaFilesExport:
    """Write CLIProxy-compatible xAI auth JSON under auth_dir (= pack out_dir)."""

    name = "cpa_files"
    # pack export: same class of thing as sub2api
    kind = "pack"
    source = "auth_pool_or_payload"

    def __init__(self, auth_dir: str | Path = "cpa_export") -> None:
        self.auth_dir = Path(auth_dir)
        # alias so generic pack code can use out_dir
        self.out_dir = self.auth_dir

    def export_entry(
        self,
        entry: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        """Pack from auth-pool style entry (or mint-shaped payload)."""
        return self.save_cpa(entry, filename=filename)

    def save_cpa(
        self,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        if not isinstance(payload, dict) or not payload:
            raise ConfigError("empty xAI pack payload", code="export_xai")
        email = (payload.get("email") or "").strip()
        if not email and not filename:
            raise ConfigError("xAI pack missing email", code="export_xai")
        from .xai_pack.writer import write_xai_auth

        path = write_xai_auth(
            self.auth_dir,
            payload,
            filename=filename or (cpa_filename(email) if email else None),
        )
        return Path(path)

    def preview_path(self, email: str) -> Path:
        return self.auth_dir / cpa_filename(email)

    def upsert_cpa(self, cpa_path: Path | str) -> str:
        raise ConfigError(
            "cpa_files is a pack export (writes files); use auth_pool to upsert ledger",
            code="export_backend",
        )
