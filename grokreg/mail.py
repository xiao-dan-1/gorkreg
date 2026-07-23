"""Mail helpers + Graph client (compat re-export).

Canonical implementations live under ``grokreg.backends.mail``.
This module stays import-stable for cli / pool / scripts.
"""
from __future__ import annotations

from .backends.mail.codes import (
    XAI_CODE_RE,
    XAI_HINTS,
    XAI_SUBJECT_RE,
    extract_xai_code,
    normalize_xai_code,
    parse_mail_line,
)
from .backends.mail.graph import MailClientError, MSMailClient

__all__ = [
    "XAI_CODE_RE",
    "XAI_HINTS",
    "XAI_SUBJECT_RE",
    "MailClientError",
    "MSMailClient",
    "extract_xai_code",
    "normalize_xai_code",
    "parse_mail_line",
]
