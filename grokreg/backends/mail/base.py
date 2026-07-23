"""Mail backend protocol — wait for xAI email OTP."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MailBackend(Protocol):
    def wait_for_xai_code(
        self,
        after_ts: float = 0,
        timeout: int = 120,
        interval: int = 3,
        exclude_codes: set[str] | None = None,
    ) -> str:
        """Block until a fresh xAI confirmation code is available.

        after_ts: fixed session baseline (unix epoch UTC seconds). Only mails
                at/after this time count. Mail timestamps without TZ are parsed as UTC
                (see timeutil). Callers must NOT refresh baseline when skipping stale codes.
        exclude_codes: codes already tried / known-stale; keep polling for another.
        """
        ...
