"""CloudMail client + allocate helpers (compat re-export).

Canonical: ``grokreg.backends.mail.cloudmail``.
"""
from __future__ import annotations

from .backends.mail.cloudmail import (
    CloudMailClient,
    CloudMailError,
    allocate_cloudmail_address,
    gen_public_token,
    generate_local_part,
    get_shared_public_token,
    parse_domains,
    public_email_list,
    resolve_cloudmail_settings,
)

__all__ = [
    "CloudMailClient",
    "CloudMailError",
    "allocate_cloudmail_address",
    "gen_public_token",
    "generate_local_part",
    "get_shared_public_token",
    "parse_domains",
    "public_email_list",
    "resolve_cloudmail_settings",
]
