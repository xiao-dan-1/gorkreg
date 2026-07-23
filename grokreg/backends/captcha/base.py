"""Captcha backend protocol — Turnstile only at P0."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CaptchaBackend(Protocol):
    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        """Return a Turnstile token string."""
        ...
