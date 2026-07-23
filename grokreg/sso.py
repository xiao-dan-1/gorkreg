"""SSO cookie 提取（对照 Grok-Register-Tool sso.py，精简版）。"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TransportRequest = Callable[..., Tuple[int, Dict[str, str], List[str], bytes]]
HeadersFactory = Callable[[], Dict[str, str]]


def _normalize_rsc_text(rsc_body: str) -> str:
    if not rsc_body:
        return ""
    text = rsc_body
    for _ in range(3):
        nxt = (
            text.replace("\\u0026", "&")
            .replace("\\u003d", "=")
            .replace("\\u003f", "?")
            .replace("\\u002F", "/")
            .replace("\\u002f", "/")
            .replace("\\/", "/")
            .replace("\\\\/", "/")
            .replace("&amp;", "&")
            .replace("\\u0026amp;", "&")
        )
        if nxt == text:
            break
        text = nxt
    return text


def parse_sso_jwt_url(rsc_body: str) -> Optional[str]:
    if not rsc_body:
        return None
    text = _normalize_rsc_text(rsc_body)
    patterns = (
        r'https?://[^\s"\'<>\\]+set-cookie/?\?q='
        r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
        r"(?:https?:)?//[^\s\"'<>\\]*set-cookie[^\s\"'<>\\]*[?&]q="
        r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
        r"/[^\s\"'<>\\]*set-cookie/?\?q="
        r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
    )
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        url = m.group(0)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://accounts.x.ai" + url
        return url
    m = re.search(
        r"set-cookie[^e]{0,80}(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return f"https://auth.grokusercontent.com/set-cookie?q={m.group(1)}"
    return None


def parse_all_set_cookie_urls(rsc_body: str) -> List[str]:
    if not rsc_body:
        return []
    text = _normalize_rsc_text(rsc_body)
    found: List[str] = []
    for m in re.finditer(
        r'https?://[^\s"\'<>\\]+set-cookie/?\?q='
        r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
        text,
        flags=re.IGNORECASE,
    ):
        if m.group(0) not in found:
            found.append(m.group(0))
    for m in re.finditer(
        r"/[^\s\"'<>\\]*set-cookie/?\?q="
        r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
        text,
        flags=re.IGNORECASE,
    ):
        url = "https://accounts.x.ai" + m.group(0)
        if url not in found:
            found.append(url)
    return found


def parse_sso_token_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    text = _normalize_rsc_text(text)
    m = re.search(
        r"(?:^|[;,\s'\"\\])sso=(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return m.group(1) if m else None


def parse_sso_from_set_cookies(set_cookies: List[str]) -> Optional[str]:
    if not set_cookies:
        return None
    for sc in set_cookies:
        if not sc:
            continue
        m = re.search(
            r"(?:^|,\s*)sso=(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
            sc,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1)
    return None


def parse_jwt_payload(jwt: str) -> Optional[Dict[str, Any]]:
    try:
        parts = jwt.split(".")
        if len(parts) < 2:
            return None
        raw = parts[1]
        raw += "=" * (4 - len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return None


def parse_sso_jwt_payload(sso_token: str) -> Optional[Dict[str, Any]]:
    return parse_jwt_payload(sso_token)


def _extract_jwt_from_url(url: str) -> Optional[str]:
    m = re.search(r"q=(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


class SSOExtractor:
    GROKUSERCONTENT_SET_COOKIE = "https://auth.grokusercontent.com/set-cookie"

    def __init__(
        self,
        transport_request: TransportRequest,
        base_headers: HeadersFactory,
        cookie_jar: Any,
        *,
        debug: bool = False,
    ) -> None:
        self._request = transport_request
        self._base_headers = base_headers
        self._cookies = cookie_jar
        self.debug = debug

    def extract(self, rsc_body: str) -> Optional[str]:
        token = parse_sso_token_from_text(rsc_body)
        if token:
            if self.debug:
                logger.info("[sso] found raw sso token in RSC body")
            return token

        hop_urls = parse_all_set_cookie_urls(rsc_body)
        primary = parse_sso_jwt_url(rsc_body)
        if primary and primary not in hop_urls:
            hop_urls.insert(0, primary)
        if not hop_urls:
            jwt_only = re.search(
                r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
                _normalize_rsc_text(rsc_body or ""),
            )
            if jwt_only:
                hop_urls = [
                    f"https://auth.grokusercontent.com/set-cookie?q={jwt_only.group(1)}"
                ]
            else:
                return None

        expanded: List[str] = []
        for sso_url in hop_urls:
            if sso_url not in expanded:
                expanded.append(sso_url)
            jwt = _extract_jwt_from_url(sso_url)
            if not jwt:
                continue
            success_url = self._resolve_success_url(jwt)
            if success_url and success_url not in expanded:
                expanded.append(success_url)
        hop_urls = expanded

        headers = self._base_headers()
        headers.update(
            {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-fetch-site": "cross-site",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
                "referer": "https://accounts.x.ai/",
            }
        )

        # Parallel extraction: try all hops concurrently, return first success
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def try_hop(hop: str) -> Optional[str]:
            try:
                status, hdrs, set_cookies, raw = self._request(
                    "GET", hop, headers=headers
                )
                if self.debug:
                    logger.info(
                        "[sso] hop HTTP %s %s set-cookies=%s",
                        status,
                        hop[:64],
                        len(set_cookies or []),
                    )
                return (
                    parse_sso_from_set_cookies(set_cookies or [])
                    or parse_sso_token_from_text((raw or b"").decode("utf-8", "replace"))
                )
            except Exception as exc:
                if self.debug:
                    logger.info("[sso] hop failed: %s", exc)
                return None

        # Try first hop immediately, then parallel others
        first_token = try_hop(hop_urls[0]) if hop_urls else None
        if first_token:
            return first_token

        if len(hop_urls) > 1:
            with ThreadPoolExecutor(max_workers=min(len(hop_urls[1:]), 4)) as ex:
                futures = {ex.submit(try_hop, hop): hop for hop in hop_urls[1:]}
                for fut in as_completed(futures):
                    token = fut.result()
                    if token:
                        return token

        return self._read_sso_from_jar()

    def _resolve_success_url(self, jwt: str) -> str:
        payload = parse_jwt_payload(jwt)
        if payload:
            cfg = payload.get("config", {})
            url = cfg.get("success_url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
        return self.GROKUSERCONTENT_SET_COOKIE

    def _read_sso_from_jar(self) -> Optional[str]:
        cj = self._cookies
        if hasattr(cj, "jar"):
            for cookie in cj.jar:
                if str(getattr(cookie, "name", "")) == "sso":
                    val = str(getattr(cookie, "value", "") or "")
                    if val:
                        return val
        if hasattr(cj, "get"):
            try:
                val = cj.get("sso")
                if val:
                    return str(val)
            except Exception:
                pass
        return None
