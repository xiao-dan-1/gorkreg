"""CloudMail catch-all receive-code (canonical implementation + adapter)."""
from __future__ import annotations

import logging
import os
import random
import re
import string
import threading
import time
from typing import Any, Optional

from curl_cffi import requests as cr

from ...errors import MailError
from .codes import extract_xai_code, normalize_xai_code

log = logging.getLogger(__name__)

_TOKEN_LOCK = threading.Lock()
_SHARED_PUBLIC_TOKEN: str | None = None
_SHARED_TOKEN_URL: str | None = None


class CloudMailError(MailError):
    """CloudMail API / wait failures."""

    code = "mail"


def _env_or(cfg_val: Any, *env_keys: str) -> str:
    for k in env_keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return str(cfg_val or "").strip()


def resolve_cloudmail_settings(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """Merge config.cloudmail + env (env wins)."""
    c = (cfg or {}).get("cloudmail") if isinstance(cfg, dict) else {}
    if not isinstance(c, dict):
        c = {}
    url = _env_or(c.get("url") or c.get("cloudmail_url"), "CLOUDMAIL_URL").rstrip("/")
    admin = _env_or(
        c.get("admin_email") or c.get("cloudmail_admin_email"),
        "CLOUDMAIL_ADMIN_EMAIL",
    )
    password = _env_or(
        c.get("password") or c.get("cloudmail_password"),
        "CLOUDMAIL_PASSWORD",
    )
    domains_raw = _env_or(
        c.get("domains") or c.get("defaultDomains") or c.get("default_domains"),
        "CLOUDMAIL_DOMAINS",
        "GROK_MAIL_DOMAINS",
    )
    return {
        "url": url,
        "admin_email": admin,
        "password": password,
        "domains": domains_raw,
    }


def parse_domains(raw: str) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[,，\s]+", raw or "") if x.strip()]


def generate_local_part(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(max(6, length)))


def allocate_cloudmail_address(cfg: dict[str, Any] | None = None, *, domain: str | None = None) -> str:
    """Catch-all: random user@domain from config domains (or optional domain).

    Domain pool resolution order for multi-domain batching:
      1) explicit ``domain=`` argument
      2) env ``CLOUDMAIL_DOMAIN_POOL`` (comma-separated; GrokX UI injects this)
      3) config / env ``CLOUDMAIL_DOMAINS``
    """
    st = resolve_cloudmail_settings(cfg)
    pool_raw = (os.environ.get("CLOUDMAIL_DOMAIN_POOL") or "").strip()
    domains = parse_domains(domain or pool_raw or st["domains"])
    if not domains:
        raise CloudMailError(
            "CloudMail domains empty (config cloudmail.domains / CLOUDMAIL_DOMAINS)",
            code="mail_config",
            retryable=False,
        )
    if domain and str(domain).strip():
        d = str(domain).strip().lower()
    else:
        mode = (os.environ.get("CLOUDMAIL_DOMAIN_MODE") or "random").strip().lower()
        if mode in {"round", "roundrobin", "rr", "轮询"}:
            # process-local round-robin via env counter (best-effort for multi-worker)
            try:
                idx = int(os.environ.get("CLOUDMAIL_DOMAIN_RR_I") or "0")
            except ValueError:
                idx = 0
            d = domains[idx % len(domains)]
            os.environ["CLOUDMAIL_DOMAIN_RR_I"] = str(idx + 1)
        else:
            d = random.choice(domains)
    if "@" in d:
        d = d.split("@", 1)[-1]
    return f"{generate_local_part(10)}@{d}"


def _post_json(
    url: str,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    impersonate: str = "chrome131",
) -> Any:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    r = cr.post(
        f"{url.rstrip('/')}{path}",
        json=payload,
        headers=h,
        timeout=timeout,
        impersonate=impersonate,
        proxies=None,  # 自建邮直连，与 graph 收码一致
    )
    try:
        data = r.json() if r.text else {}
    except Exception:
        data = {"_raw": (r.text or "")[:300], "_status": r.status_code}
    if r.status_code >= 400:
        raise CloudMailError(
            f"http_{r.status_code}:{(r.text or '')[:200]}",
            code="mail",
            retryable=r.status_code >= 500,
        )
    return data


def _get_json(
    url: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    impersonate: str = "chrome131",
) -> Any:
    h: dict[str, str] = {}
    if headers:
        h.update(headers)
    r = cr.get(
        f"{url.rstrip('/')}{path}",
        headers=h,
        timeout=timeout,
        impersonate=impersonate,
        proxies=None,
    )
    try:
        data = r.json() if r.text else {}
    except Exception:
        data = {"_raw": (r.text or "")[:300], "_status": r.status_code}
    if r.status_code >= 400:
        raise CloudMailError(
            f"http_{r.status_code}:{(r.text or '')[:200]}",
            code="mail",
            retryable=r.status_code >= 500,
        )
    return data


def gen_public_token(
    url: str,
    admin_email: str,
    admin_password: str,
    *,
    impersonate: str = "chrome131",
) -> str:
    """POST /api/public/genToken -> public API token (UUID)."""
    data = _post_json(
        url,
        "/api/public/genToken",
        {"email": admin_email, "password": admin_password},
        impersonate=impersonate,
    )
    if isinstance(data, dict) and data.get("code") == 200:
        token_data = data.get("data") or {}
        if isinstance(token_data, dict):
            tok = token_data.get("token")
            if tok:
                return str(tok)
    raise CloudMailError(
        f"genToken failed: {str(data)[:200]}",
        code="mail_auth",
        retryable=False,
    )


def get_shared_public_token(
    url: str,
    admin_email: str,
    admin_password: str,
    *,
    force_refresh: bool = False,
    impersonate: str = "chrome131",
) -> str:
    global _SHARED_PUBLIC_TOKEN, _SHARED_TOKEN_URL
    with _TOKEN_LOCK:
        if (
            not force_refresh
            and _SHARED_PUBLIC_TOKEN
            and _SHARED_TOKEN_URL == url
        ):
            return _SHARED_PUBLIC_TOKEN
        tok = gen_public_token(
            url, admin_email, admin_password, impersonate=impersonate
        )
        _SHARED_PUBLIC_TOKEN = tok
        _SHARED_TOKEN_URL = url
        return tok


def admin_login(
    url: str,
    admin_email: str,
    admin_password: str,
    *,
    impersonate: str = "chrome131",
) -> str:
    """POST /api/login → admin JWT (for settings / domain list)."""
    data = _post_json(
        url,
        "/api/login",
        {"email": admin_email, "password": admin_password},
        impersonate=impersonate,
    )
    if isinstance(data, dict) and data.get("code") == 200:
        token_data = data.get("data")
        if isinstance(token_data, dict) and token_data.get("token"):
            return str(token_data["token"])
        if isinstance(token_data, str) and token_data.strip():
            return token_data.strip()
    raise CloudMailError(
        f"admin login failed: {str(data)[:200]}",
        code="mail_auth",
        retryable=False,
    )


def fetch_domain_list(
    url: str,
    admin_email: str,
    admin_password: str,
    *,
    impersonate: str = "chrome131",
) -> list[str]:
    """Admin setting/query → domainList (e.g. @bj01.example.com → bj01.example.com).

    Public genToken does not expose domains; admin JWT + GET /api/setting/query does.
    """
    jwt = admin_login(url, admin_email, admin_password, impersonate=impersonate)
    data = _get_json(
        url,
        "/api/setting/query",
        headers={"Authorization": jwt},
        impersonate=impersonate,
    )
    if not isinstance(data, dict) or data.get("code") != 200:
        raise CloudMailError(
            f"setting/query failed: {str(data)[:200]}",
            code="mail",
            retryable=True,
        )
    payload = data.get("data") or {}
    if not isinstance(payload, dict):
        return []
    raw = payload.get("domainList") or payload.get("domains") or []
    out: list[str] = []
    if isinstance(raw, str):
        raw = re.split(r"[,，\s]+", raw)
    if isinstance(raw, list):
        for item in raw:
            s = str(item or "").strip().lower()
            if not s:
                continue
            if s.startswith("@"):
                s = s[1:]
            if "@" in s:
                s = s.split("@", 1)[-1]
            if s and s not in out:
                out.append(s)
    return out


def public_email_list(
    url: str,
    public_token: str,
    *,
    to_email: str = "",
    size: int = 20,
    impersonate: str = "chrome131",
) -> list[dict[str, Any]]:
    """POST /api/public/emailList — Authorization is raw token (no Bearer)."""
    payload: dict[str, Any] = {"size": max(1, size)}
    if to_email:
        payload["toEmail"] = to_email
    data = _post_json(
        url,
        "/api/public/emailList",
        payload,
        headers={"Authorization": public_token},
        impersonate=impersonate,
    )
    if isinstance(data, dict):
        if data.get("code") == 200:
            rows = data.get("data") or []
            return rows if isinstance(rows, list) else []
        msg = str(data.get("message") or data)[:200]
        raise CloudMailError(f"emailList: {msg}", code="mail", retryable=True)
    return []


def _msg_blob(msg: dict[str, Any]) -> tuple[str, str]:
    subject = str(msg.get("subject") or "")
    parts: list[str] = []
    for field in (
        "content",
        "text",
        "textContent",
        "text_content",
        "body",
        "snippet",
        "intro",
    ):
        value = msg.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    html_val = msg.get("html") or msg.get("htmlContent") or msg.get("html_content")
    if isinstance(html_val, str):
        parts.append(re.sub(r"<[^>]+>", " ", html_val))
    elif isinstance(html_val, list):
        for h in html_val:
            if isinstance(h, str):
                parts.append(re.sub(r"<[^>]+>", " ", h))
    return subject, "\n".join(parts)


def _msg_epoch(msg: dict[str, Any]) -> float | None:
    """Mail timestamp → unix seconds. Naive createTime treated as UTC (timeutil)."""
    from ...timeutil import parse_to_epoch

    for k in (
        "createTime",
        "createdAt",
        "sendTime",
        "sentAt",
        "time",
        "timestamp",
        "date",
    ):
        ts = parse_to_epoch(msg.get(k))
        if ts is not None:
            return ts
    return None


class CloudMailClient:
    """Poll CloudMail public API for xAI confirmation codes."""

    def __init__(
        self,
        account: dict[str, Any],
        *,
        cfg: dict[str, Any] | None = None,
        url: str | None = None,
        admin_email: str | None = None,
        password: str | None = None,
        proxy: str | None = None,  # accepted, ignored (direct)
        impersonate: str = "chrome131",
        poll_interval: float = 0.5,
    ) -> None:
        st = resolve_cloudmail_settings(cfg)
        self.email = str(account.get("email") or "").strip()
        if not self.email or "@" not in self.email:
            raise CloudMailError("cloudmail account.email required", code="mail_config", retryable=False)
        self.url = (url or st["url"]).rstrip("/")
        self.admin_email = (admin_email or st["admin_email"]).strip()
        self.password = password if password is not None else st["password"]
        self.impersonate = impersonate
        self.poll_interval = max(0.2, float(poll_interval))
        if not self.url or not self.admin_email or not self.password:
            raise CloudMailError(
                "CloudMail config incomplete (url/admin_email/password)",
                code="mail_config",
                retryable=False,
            )
        _ = proxy  # 收码直连

    def wait_for_xai_code(
        self,
        after_ts: float = 0,
        timeout: int = 120,
        interval: int | float | None = None,
        exclude_codes: set[str] | None = None,
    ) -> str:
        exclude = {normalize_xai_code(c) for c in (exclude_codes or set()) if c}
        poll = float(interval) if interval is not None else self.poll_interval
        poll = max(0.2, poll)
        deadline = time.time() + max(5, int(timeout))
        token = get_shared_public_token(
            self.url,
            self.admin_email,
            self.password,
            impersonate=self.impersonate,
        )
        log.info(
            "cloudmail wait email=%s timeout=%s after_ts=%.0f",
            self.email,
            timeout,
            after_ts or 0,
        )
        last_err: str | None = None
        while time.time() < deadline:
            try:
                messages = public_email_list(
                    self.url,
                    token,
                    to_email=self.email,
                    size=20,
                    impersonate=self.impersonate,
                )
            except CloudMailError as exc:
                last_err = str(exc)
                low = last_err.lower()
                if "token" in low or "401" in low or "auth" in low:
                    try:
                        token = get_shared_public_token(
                            self.url,
                            self.admin_email,
                            self.password,
                            force_refresh=True,
                            impersonate=self.impersonate,
                        )
                    except Exception as e2:
                        last_err = f"{last_err}; refresh={e2}"
                time.sleep(poll)
                continue
            except Exception as exc:
                last_err = str(exc)
                time.sleep(poll)
                continue

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                ts = _msg_epoch(msg)
                if after_ts and ts is not None and ts + 2 < after_ts:
                    continue
                subject, text = _msg_blob(msg)
                code = extract_xai_code(subject, text)
                if not code:
                    continue
                code = normalize_xai_code(code)
                if code in exclude:
                    continue
                log.info("cloudmail code ok email=%s", self.email)
                return code
            time.sleep(poll)

        detail = f" last={last_err}" if last_err else ""
        raise CloudMailError(
            f"mail_timeout cloudmail {timeout}s email={self.email}{detail}",
            code="mail_timeout",
            retryable=True,
        )


class CloudMailBackend:
    """MailBackend adapter over CloudMailClient."""

    def __init__(
        self,
        account: dict[str, Any],
        *,
        cfg: dict[str, Any] | None = None,
        url: str | None = None,
        admin_email: str | None = None,
        password: str | None = None,
        proxy: str | None = None,
        impersonate: str = "chrome131",
        poll_interval: float = 0.5,
    ) -> None:
        self._client = CloudMailClient(
            account,
            cfg=cfg,
            url=url,
            admin_email=admin_email,
            password=password,
            proxy=proxy,
            impersonate=impersonate,
            poll_interval=poll_interval,
        )

    def wait_for_xai_code(
        self,
        after_ts: float = 0,
        timeout: int = 120,
        interval: int = 3,
        exclude_codes: set[str] | None = None,
    ) -> str:
        # CloudMail prefers short poll; clamp interval when caller passes graph-style 3s
        poll = float(interval) if interval and interval < 2 else 0.5
        return self._client.wait_for_xai_code(
            after_ts=after_ts,
            timeout=timeout,
            interval=poll,
            exclude_codes=exclude_codes,
        )
