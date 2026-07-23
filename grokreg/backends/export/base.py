"""Export backend protocol — local target packs from credentials.

Layers (do not mix):
  mint/refresh  → RT/AT into auth.json only (protocol / ledger)
  auth.json     → multi-account ledger (source of truth)
  export backend→ target packs from ledger (cpa_files, sub2api, …)
  upload ops    → push packs to remote (not this Protocol)

CPA files (cpa_export/) are **export-layer only**, not mint/refresh input.
Optional mint packs=["cpa_files"] is an explicit opt-in, not the default path.
Remote upload/import stays ops CLI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExportBackend(Protocol):
    """Pluggable export surface.

    Preferred for new backends:
      export_entry(entry) -> Path

    Optional (cpa_files / pool only):
      save_cpa(payload)   — write pack file under cpa_export
      upsert_cpa(path)    — import CPA file → auth.json (ops/import)
    """

    def export_entry(
        self,
        entry: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        """Write one pack from an auth-pool style entry; return path."""
        ...

    def save_cpa(
        self,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        """Write CPA payload (mint path). May raise if unsupported."""
        ...

    def upsert_cpa(self, cpa_path: Path | str) -> str:
        """Upsert one CPA file into auth.json; return pool key. May raise."""
        ...
