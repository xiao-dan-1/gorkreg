"""Graph / Outlook OAuth mail backend (canonical implementation)."""
from __future__ import annotations

import logging
import time
from typing import Any

from curl_cffi import requests as cr

from ...errors import MailError
from .codes import extract_xai_code, normalize_xai_code

logger = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://login.live.com/oauth20_token.srf"
MAIL_ENDPOINT = "https://outlook.office.com/api/v2.0/me/messages"


class MailClientError(MailError):
    """Outlook Graph/IMAP mail backend failures."""

    code = "mail"


# Permanent AAD / token failures — do not burn full OTP wait timeout.
_PERM_TOKEN_MARKERS = (
    "aadsts70000",
    "aadsts50034",  # user not found
    "aadsts50076",  # mfa required (won't self-heal mid-wait)
    "aadsts50126",  # invalid credentials
    "aadsts70008",  # expired refresh
    "aadsts700084",
    "aadsts9002313",
    "service abuse mode",
    "user account is found to be in service abuse",
    "invalid_grant",
    "interaction_required",
    "expired_token",
)


def _token_error_is_permanent(err: str | None) -> bool:
    low = (err or "").lower()
    if not low:
        return False
    return any(m in low for m in _PERM_TOKEN_MARKERS)


class MSMailClient:
    def __init__(
        self,
        account: dict[str, Any],
        proxy: str | None = None,
        impersonate: str = "chrome131",
        timeout: int = 30,
    ):
        self.email = account["email"]
        self.client_id = account["client_id"]
        self.refresh_token = account["refresh_token"]
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.timeout = timeout
        self._access_token: str | None = None
        self._access_token_exp = 0.0
        self._last_token_error: str | None = None
        self._token_fail_streak = 0

    def _proxies(self) -> dict | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def get_access_token(self, force: bool = False) -> str | None:
        now = time.time()
        if not force and self._access_token and now < self._access_token_exp - 60:
            return self._access_token
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            r = cr.post(
                TOKEN_ENDPOINT,
                data=data,
                timeout=self.timeout,
                impersonate=self.impersonate,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                proxies=self._proxies(),
            )
            try:
                j = r.json() if r.text else {}
            except Exception:
                j = {}
            if not isinstance(j, dict):
                j = {}
        except Exception as exc:
            self._last_token_error = f"request_failed:{exc}"
            self._token_fail_streak += 1
            logger.warning("[MSMail] token 请求失败: %s", exc)
            return None

        at = j.get("access_token")
        if not at:
            err = j.get("error") or ""
            desc = j.get("error_description") or j.get("error_codes") or ""
            if isinstance(desc, list):
                desc = ",".join(str(x) for x in desc)
            snippet = f"{err} {desc}".strip() or f"HTTP {r.status_code} body={str(j)[:180]}"
            self._last_token_error = snippet
            self._token_fail_streak += 1
            logger.warning(
                "[MSMail] 未拿到 access_token email=%s http=%s err=%s",
                self.email,
                getattr(r, "status_code", "?"),
                snippet[:240],
            )
            return None

        new_rt = j.get("refresh_token")
        if new_rt:
            self.refresh_token = new_rt
        self._access_token = at
        self._access_token_exp = now + int(j.get("expires_in") or 3600)
        self._last_token_error = None
        self._token_fail_streak = 0
        return at

    def ensure_access_token(self, *, force: bool = False) -> str:
        """Return access token or raise MailClientError (permanent → non-retryable)."""
        at = self.get_access_token(force=force)
        if at:
            return at
        err = self._last_token_error or "no access_token"
        permanent = _token_error_is_permanent(err)
        raise MailClientError(
            f"{self.email} token failed: {err[:220]}",
            code="mail_auth" if permanent else "mail_token",
            retryable=not permanent,
            detail=err,
        )

    def _fetch_messages(self, top: int = 20) -> list[dict]:
        at = self.get_access_token()
        if not at:
            return []
        params = {
            "$select": "Id,Subject,From,BodyPreview,Body,ReceivedDateTime,IsRead",
            "$top": str(top),
            "$orderby": "ReceivedDateTime desc",
        }
        headers = {"Authorization": f"Bearer {at}", "Accept": "application/json"}
        try:
            r = cr.get(
                MAIL_ENDPOINT,
                params=params,
                timeout=self.timeout,
                impersonate=self.impersonate,
                headers=headers,
                proxies=self._proxies(),
            )
            if r.status_code == 401:
                at = self.get_access_token(force=True)
                if not at:
                    return []
                headers["Authorization"] = f"Bearer {at}"
                r = cr.get(
                    MAIL_ENDPOINT,
                    params=params,
                    timeout=self.timeout,
                    impersonate=self.impersonate,
                    headers=headers,
                    proxies=self._proxies(),
                )
            j = r.json() if r.status_code == 200 else {}
        except Exception as exc:
            logger.warning("[MSMail] 拉邮件失败: %s", exc)
            return []
        return j.get("value") or []

    @staticmethod
    def _parse_ts(raw: str) -> float:
        from ...timeutil import parse_to_epoch_or_zero

        return parse_to_epoch_or_zero(raw)

    def wait_for_xai_code(
        self,
        after_ts: float | None = None,
        timeout: int = 120,
        interval: int = 3,
        exclude_codes: set[str] | None = None,
        *,
        max_token_failures: int = 3,
    ) -> str:
        """Poll Graph for a code newer than fixed baseline ``after_ts``."""
        self.ensure_access_token()

        seen: set[str] = {normalize_xai_code(c) for c in (exclude_codes or set()) if c}
        baseline = float(after_ts or 0)
        min_ts = (baseline - 30.0) if baseline > 0 else 0.0
        deadline = time.time() + timeout
        token_fail_cap = max(1, int(max_token_failures))

        while time.time() < deadline:
            at = self.get_access_token()
            if not at:
                err = self._last_token_error or "no access_token"
                if _token_error_is_permanent(err):
                    raise MailClientError(
                        f"{self.email} token failed: {err[:220]}",
                        code="mail_auth",
                        retryable=False,
                        detail=err,
                    )
                if self._token_fail_streak >= token_fail_cap:
                    raise MailClientError(
                        f"{self.email} no access_token x{self._token_fail_streak}: {err[:180]}",
                        code="mail_token",
                        retryable=True,
                        detail=err,
                    )
                time.sleep(interval)
                continue

            for msg in self._fetch_messages():
                subj = msg.get("Subject") or ""
                preview = msg.get("BodyPreview") or ""
                body = (
                    (msg.get("Body") or {}).get("Content")
                    if isinstance(msg.get("Body"), dict)
                    else ""
                ) or ""
                frm = ((msg.get("From") or {}).get("EmailAddress") or {}).get("Address") or ""
                ts = self._parse_ts(msg.get("ReceivedDateTime") or "")
                code = extract_xai_code(subj, f"{preview}\n{body}")
                if not code:
                    continue
                if min_ts > 0 and (not ts or ts < min_ts):
                    if code not in seen:
                        logger.info(
                            "[MSMail] skip stale/unknown code=%s ts=%.0f baseline=%.0f for %s",
                            code,
                            ts or 0,
                            baseline,
                            self.email,
                        )
                        seen.add(code)
                    continue
                if code in seen:
                    continue
                logger.info(
                    "[MSMail] xAI code=%s from=%s subj=%s (baseline=%.0f)",
                    code,
                    frm,
                    subj[:80],
                    baseline,
                )
                return code
            time.sleep(interval)
        raise MailClientError(
            f"等待 {self.email} xAI 验证码超时（>{timeout}s）"
            + (f" baseline={baseline:.0f}" if baseline else ""),
            code="mail_timeout",
            retryable=True,
        )


class GraphMailBackend:
    """MailBackend adapter over MSMailClient."""

    def __init__(
        self,
        account: dict[str, Any],
        *,
        proxy: str | None = None,
        impersonate: str = "chrome131",
    ) -> None:
        self._client = MSMailClient(account, proxy=proxy, impersonate=impersonate)

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
