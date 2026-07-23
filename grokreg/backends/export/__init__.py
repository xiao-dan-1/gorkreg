"""Export backends — pack factory + ledger publish.

Architecture::

  SSO → mint → publish_credentials → auth.json   (ledger.py)
  auth.json → export_auth_pool → cpa_files|sub2api|cockpit (factory.py)
  packs → ops upload
  xai pack schema/writer: backends.export.xai_pack (compat: grokreg.backends.export.xai_pack)

publish_credentials default: ledger only (no automatic cpa_files).
Optional packs=[\"cpa_files\"] when explicitly requested.
"""
from __future__ import annotations

from .base import ExportBackend
from .cockpit import (
    DEFAULT_OUT_DIR as COCKPIT_EXPORT_DIR,
    entry_to_cockpit_account,
    export_auth_pool_to_cockpit,
)
from .factory import (
    PACK_BACKENDS,
    POOL_PACK_BACKENDS,
    export_auth_pool,
    export_entry_file,
    export_sub2api_file,
    get_export_backend,
    save_cpa_file,
)
from .ledger import (
    publish_credentials,
    sync_cpa_to_pool,
    upsert_payload_to_pool,
)
from .sub2api import (
    DEFAULT_OUT_DIR as SUB2API_EXPORT_DIR,
    entry_to_sub2api_account,
    export_auth_pool_to_sub2api,
    wrap_export_document,
)
from .xai_pack import build_xai_auth, credential_file_name, write_xai_auth
from .convert import (
    convert_paths,
    cpa_to_sub2api_document,
    detect_kind,
    sub2api_to_cpa_payloads,
)

__all__ = [
    "ExportBackend",
    "PACK_BACKENDS",
    "POOL_PACK_BACKENDS",
    "get_export_backend",
    "publish_credentials",
    "upsert_payload_to_pool",
    "save_cpa_file",
    "sync_cpa_to_pool",
    "export_entry_file",
    "export_auth_pool",
    "export_sub2api_file",
    "export_auth_pool_to_sub2api",
    "entry_to_sub2api_account",
    "wrap_export_document",
    "SUB2API_EXPORT_DIR",
    "COCKPIT_EXPORT_DIR",
    "entry_to_cockpit_account",
    "export_auth_pool_to_cockpit",
    "convert_paths",
    "cpa_to_sub2api_document",
    "detect_kind",
    "sub2api_to_cpa_payloads",
    "build_xai_auth",
    "credential_file_name",
    "write_xai_auth",
]
