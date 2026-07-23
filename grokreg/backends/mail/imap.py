"""IMAP REST mail backend (canonical: OutlookIMAPClient + adapter)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from curl_cffi import requests as cr

from ...errors import MailError
from .codes import normalize_xai_code

logger = logging.getLogger(__name__)

XAI_CODE_RE = re.compile(r"\b([A-Z0-9]{3}[- ][A-Z0-9]{3})\b")

# Local/mail clock skew. Baseline stays fixed; only this window is soft.
_SKEW_SEC = 30.0


class OutlookIMAPClient:
    """Fetch xAI verification codes via the local Outlook IMAP API."""

    def __init__(
        self,
        account: dict[str, Any],
        *,
        api_url: str = "https://outlook.xdauv.xyz",
        proxy: str | None = None,
        timeout: int = 120,
        interval: int = 3,
    ):
        self.email = account["email"]
        self._account_text = (
            f"{account['email']}----{account.get('password','')}----"
            f"{account.get('client_id','')}----{account.get('refresh_token','')}"
        )
        self._api_url = api_url.rstrip("/")
        self._proxy = proxy
        self._timeout = timeout
        self._interval = interval

    def _proxies(self) -> dict | None:
        if not self._proxy:
            return None
        return {"http": self._proxy, "https": self._proxy}

    def _fetch(self, limit: int = 30) -> list[dict]:
        try:
            r = cr.post(
                f"{self._api_url}/api/fetch",
                json={"account_text": self._account_text, "limit": limit, "mailbox": "INBOX"},
                headers={"Content-Type": "application/json"},
                timeout=30,
                proxies=self._proxies(),
                impersonate="chrome131",
            )
            data = r.json()
            if not isinstance(data, dict):
                return []
            return data.get("messages") or []
        except Exception as e:
            logger.warning("[IMAP] fetch failed for %s: %s", self.email, e)
            return []

    @staticmethod
    def _parse_ts(raw: str) -> float:
        from ...timeutil import parse_to_epoch_or_zero

        return parse_to_epoch_or_zero(raw)

    def wait_for_xai_code(
        self,
        after_ts: float = 0,
        timeout: int = 120,
        interval: int = 3,
        exclude_codes: set[str] | None = None,
    ) -> str:
        """Poll for a code newer than fixed baseline ``after_ts``."""
        deadline = time.time() + max(1, int(timeout))
        poll = max(1, int(interval))
        seen: set[str] = {normalize_xai_code(c) for c in (exclude_codes or set()) if c}
        baseline = float(after_ts or 0)
        min_ts = (baseline - _SKEW_SEC) if baseline > 0 else 0.0

        while time.time() < deadline:
            msgs = self._fetch(limit=30)
            scored: list[tuple[float, dict]] = [
                (self._parse_ts(m.get("sent_at") or ""), m) for m in msgs
            ]
            scored.sort(key=lambda x: x[0], reverse=True)

            for ts, m in scored:
                subject = (m.get("subject") or "").strip()
                sender = (m.get("sender") or "").lower()
                body = (m.get("body_preview") or "").strip()
                blob = f"{sender} {subject} {body}".lower()
                if "xai" not in blob and "x.ai" not in blob and "confirmation code" not in blob:
                    continue

                code = None
                m_subj = re.search(XAI_CODE_RE, subject.upper())
                if m_subj:
                    code = normalize_xai_code(m_subj.group(1))
                if not code:
                    m_body = re.search(XAI_CODE_RE, body.upper())
                    if m_body:
                        code = normalize_xai_code(m_body.group(1))
                if not code:
                    continue

                if min_ts > 0:
                    if not ts or ts < min_ts:
                        if code not in seen:
                            logger.info(
                                "[IMAP] skip stale/unknown code=%s sent_at=%s baseline=%.0f for %s",
                                code,
                                m.get("sent_at") or "-",
                                baseline,
                                self.email,
                            )
                            seen.add(code)
                        continue

                if code in seen:
                    continue

                logger.info(
                    "[IMAP] xAI code=%s for %s sent_at=%s subj=%s (baseline=%.0f)",
                    code,
                    self.email,
                    m.get("sent_at") or "-",
                    subject[:80],
                    baseline,
                )
                return code

            time.sleep(poll)

        raise MailError(
            f"等待 {self.email} xAI 验证码超时（>{timeout}s）via IMAP API"
            + (f" baseline={baseline:.0f}" if baseline else ""),
            code="mail_timeout",
            retryable=True,
        )


class ImapMailBackend:
    """MailBackend adapter over OutlookIMAPClient."""

    def __init__(
        self,
        account: dict[str, Any],
        *,
        api_url: str = "https://outlook.xdauv.xyz",
        proxy: str | None = None,
        timeout: int = 120,
        interval: int = 3,
    ) -> None:
        self._client = OutlookIMAPClient(
            account,
            api_url=api_url,
            proxy=proxy,
            timeout=timeout,
            interval=interval,
        )

    def wait_for_xai_code(
        self,
        after_ts: float = 0,
        timeout: int = 120,
        interval: int = 3,
        exclude_codes: set[str] | None = None,
    ) -> str:
        return self._client.wait_for_xai_code(
            after_ts=after_ts,
            timeout=timeout,
            interval=interval,
            exclude_codes=exclude_codes,
        )
