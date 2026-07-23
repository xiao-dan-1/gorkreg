"""xAI / CLIProxy pack schema + writer (export layer).

Product path names still use cpa_export / cpa_files historically.
This package is the pack format implementation — not an OAuth protocol module.
"""
from __future__ import annotations

from .schema import build_xai_auth, credential_file_name
from .writer import write_xai_auth

__all__ = ["build_xai_auth", "credential_file_name", "write_xai_auth"]
