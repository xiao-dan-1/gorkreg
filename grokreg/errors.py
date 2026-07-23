"""Structured errors for grokreg.

Inspired by Grok-Register-Tool AuthError + domain RuntimeError patterns,
but kept small: one hierarchy, retryable flag, stable code for batch logs.
"""
from __future__ import annotations

from typing import Any, Optional


class GrokRegError(Exception):
    """Base error. code=stable tag for logs; retryable=hint for batch retry."""

    code: str = "error"
    retryable: bool = False

    def __init__(self, message: str = "", *, code: str | None = None, retryable: bool | None = None, detail: Any = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.detail = detail

    def __str__(self) -> str:
        msg = super().__str__() or self.code
        return f"{self.code}: {msg}" if msg != self.code else self.code


class ConfigError(GrokRegError):
    code = "config"
    retryable = False


class ProxyError(GrokRegError):
    code = "proxy"
    retryable = True


class MailError(GrokRegError):
    code = "mail"
    retryable = True


class CaptchaError(GrokRegError):
    code = "captcha"
    retryable = True


class ProtocolError(GrokRegError):
    """accounts.x.ai / scrape / form / RSC protocol issues."""
    code = "protocol"
    retryable = False


class CreateAccountError(GrokRegError):
    code = "create"
    retryable = True


class SSOError(GrokRegError):
    code = "sso"
    retryable = True


class MintError(GrokRegError):
    code = "mint"
    retryable = True


class AuthError(GrokRegError):
    """Credential pool / token / refresh failures."""
    code = "auth"
    retryable = False


def classify(exc: BaseException) -> GrokRegError:
    """Wrap unknown exceptions into GrokRegError with a best-effort code."""
    if isinstance(exc, GrokRegError):
        return exc
    msg = str(exc) or type(exc).__name__
    low = msg.lower()
    # domain prefixes first (batch error strings)
    if low.startswith("mail:") or low.startswith("mail_") or "验证码超时" in msg or "via imap" in low:
        # Permanent mailbox auth (AAD abuse / invalid_grant) — do not batch-retry.
        if "mail_auth" in low or "aadsts70000" in low or "service abuse" in low or (
            "invalid_grant" in low and ("abuse" in low or "aadsts70000" in low or "expired" in low)
        ):
            return MailError(msg, code="mail_auth", retryable=False, detail=type(exc).__name__)
        if "mail_token" in low:
            return MailError(msg, code="mail_token", retryable=True, detail=type(exc).__name__)
        return MailError(msg, code="mail", retryable=True, detail=type(exc).__name__)
    if low.startswith("captcha:") or "turnstile" in low or "2captcha" in low or "yescaptcha" in low:
        return CaptchaError(msg, retryable=True, detail=type(exc).__name__)
    if low.startswith("mint:") or "device/" in low or "consent" in low:
        return MintError(msg, retryable=True, detail=type(exc).__name__)
    if low.startswith("sso") or "sso_failed" in low:
        return SSOError(msg, retryable=True, detail=type(exc).__name__)
    if low.startswith("create") or "empty_body" in low:
        return CreateAccountError(msg, retryable=True, detail=type(exc).__name__)
    # gRPC-web parse flake (truncated / non-proto body through residential proxy)
    if (
        "wire type" in low
        or "parse_error" in low
        or "unsupported wire" in low
        or low.startswith("grpc_parse")
    ):
        return ProxyError(msg, code="grpc_parse", retryable=True, detail=type(exc).__name__)
    # curl / network
    if "curl: (28)" in low or "timed out" in low or "operation timed out" in low:
        return ProxyError(msg, code="proxy_timeout", retryable=True, detail=type(exc).__name__)
    if "curl:" in low or "proxy" in low or "tunnel" in low or "connection" in low:
        return ProxyError(msg, retryable=True, detail=type(exc).__name__)
    if "mail" in low or "imap" in low:
        return MailError(msg, retryable=True, detail=type(exc).__name__)
    if "timeout" in low:
        return ProxyError(msg, code="proxy_timeout", retryable=True, detail=type(exc).__name__)
    return GrokRegError(msg, code="exception", retryable=False, detail=type(exc).__name__)


def error_tag(exc: BaseException) -> str:
    """Short tag for batch summary lines."""
    e = classify(exc)
    return e.code
