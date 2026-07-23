"""Mail backend factory (dict, not heavy registry)."""
from __future__ import annotations

from typing import Any

from ...errors import ConfigError
from .base import MailBackend
from .cloudmail import CloudMailBackend
from .graph import GraphMailBackend
from .imap import ImapMailBackend


def get_mail_backend(
    name: str | None,
    account: dict[str, Any],
    *,
    proxy: str | None = None,
    api_url: str | None = None,
    impersonate: str = "chrome131",
    timeout: int = 120,
    interval: int = 3,
    cfg: dict[str, Any] | None = None,
) -> MailBackend:
    """
    name: graph (default) | imap | cloudmail
    account: {email, password, client_id, refresh_token}
    """
    key = (name or "graph").strip().lower()
    if key in {"graph", "outlook", "ms", "oauth"}:
        return GraphMailBackend(account, proxy=proxy, impersonate=impersonate)
    if key in {"imap", "imap_api", "outlook_imap"}:
        url = (api_url or "https://outlook.xdauv.xyz").strip() or "https://outlook.xdauv.xyz"
        return ImapMailBackend(
            account,
            api_url=url,
            proxy=proxy,
            timeout=timeout,
            interval=interval,
        )
    if key in {"cloudmail", "cloud-mail", "cloud_mail", "cm"}:
        # catch-all + public emailList; credentials from cfg / env
        return CloudMailBackend(
            account,
            cfg=cfg,
            proxy=proxy,
            impersonate=impersonate,
        )
    raise ConfigError(f"unknown mail backend: {name!r}", code="mail_backend")
