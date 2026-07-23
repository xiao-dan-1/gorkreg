"""Auto captcha backend — preserves solve_turnstile_auto fallback chain."""
from __future__ import annotations

from typing import Any

from .impl import solve_turnstile_auto


class AutoCaptchaBackend:
    """Delegates to solve_turnstile_auto (token > yes > 2c > local > browser > manual)."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        return solve_turnstile_auto(
            website_url=website_url,
            website_key=website_key,
            **self._kwargs,
        )
