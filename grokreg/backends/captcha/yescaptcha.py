"""YesCaptcha Turnstile backend (wraps YesCaptchaSolver)."""
from __future__ import annotations

from .impl import YesCaptchaSolver


class YesCaptchaBackend:
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str | None = None,
        timeout: float = 180.0,
        debug: bool = False,
        proxy: str | None = None,
        premium: bool = True,
    ) -> None:
        self._solver = YesCaptchaSolver(
            api_key,
            endpoint=endpoint,
            timeout=timeout,
            debug=debug,
            proxy=proxy,
        )
        self._premium = premium

    def get_balance(self) -> float:
        return self._solver.get_balance()

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        return self._solver.solve_turnstile(
            website_url, website_key, premium=self._premium
        )
