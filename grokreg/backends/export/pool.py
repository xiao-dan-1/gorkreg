"""auth.json ledger helper — NOT a pack export.

Pack exports (cpa_files / sub2api / future) write target product files.
This backend only upserts the multi-account ledger used as pack source.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...auth_pool import DEFAULT_AUTH_FILE, upsert, upsert_from_cpa
from ...errors import ConfigError


class AuthPoolExport:
    """Sync CPA files / entries into auth.json (ledger, not pack)."""

    name = "auth_pool"
    kind = "ledger"
    source = "cpa_file_or_entry"

    def __init__(self, auth_path: str | Path | None = None) -> None:
        self.auth_path = Path(auth_path) if auth_path else Path(DEFAULT_AUTH_FILE)

    def export_entry(
        self,
        entry: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        """Upsert one pool entry; return auth.json path."""
        _ = filename
        if not isinstance(entry, dict) or not entry:
            raise ConfigError("empty pool entry", code="export_pool")
        upsert(self.auth_path, entry)
        return self.auth_path

    def save_cpa(
        self,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        raise ConfigError(
            "auth_pool is ledger-only; pack targets are cpa_files / sub2api",
            code="export_backend",
        )

    def upsert_cpa(self, cpa_path: Path | str) -> str:
        p = Path(cpa_path)
        if not p.is_file():
            raise ConfigError(f"CPA file not found: {p}", code="export_pool")
        return upsert_from_cpa(self.auth_path, p)
