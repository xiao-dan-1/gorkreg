"""2Captcha Turnstile backend (wraps TwoCaptchaSolver)."""
from __future__ import annotations

from .impl import TwoCaptchaSolver


class TwoCaptchaBackend:
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str | None = None,
        timeout: float = 180.0,
        debug: bool = False,
        proxy: str | None = None,
    ) -> None:
        self._solver = TwoCaptchaSolver(
            api_key,
            endpoint=endpoint or "https://2captcha.com",
            timeout=timeout,
            debug=debug,
            proxy=proxy,
        )

    def get_balance(self) -> float:
        return self._solver.get_balance()

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        return self._solver.solve_turnstile(website_url, website_key)
