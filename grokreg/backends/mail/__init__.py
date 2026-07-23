"""Mail backends: graph / imap / cloudmail.

Canonical home for receive-code implementations. Top-level
``grokreg.mail`` / ``mail_imap`` / ``mail_cloudmail`` re-export for compat.
"""
from .base import MailBackend
from .cloudmail import CloudMailBackend, CloudMailClient, allocate_cloudmail_address
from .codes import extract_xai_code, normalize_xai_code, parse_mail_line
from .factory import get_mail_backend
from .graph import GraphMailBackend, MSMailClient
from .imap import ImapMailBackend, OutlookIMAPClient

__all__ = [
    "MailBackend",
    "get_mail_backend",
    "GraphMailBackend",
    "ImapMailBackend",
    "CloudMailBackend",
    "MSMailClient",
    "OutlookIMAPClient",
    "CloudMailClient",
    "allocate_cloudmail_address",
    "parse_mail_line",
    "normalize_xai_code",
    "extract_xai_code",
]
