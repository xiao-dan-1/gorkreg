"""Grok / accounts.x.ai 纯协议客户端。

能力:
  - load_signup_page: 刮 next-action / router-state-tree / turnstile sitekey
  - create_email_validation_code / verify_email_validation_code
  - validate_password
  - create_account (Next.js server action)
  - fetch_sso_token
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from curl_cffi.requests import Session

from . import grpcweb
from .sso import (
    SSOExtractor,
    parse_sso_from_set_cookies,
    parse_sso_jwt_payload,
    parse_sso_token_from_text,
)

logger = logging.getLogger(__name__)

GRPC_SERVICE = "auth_mgmt.AuthManagement"


@dataclass
class GrpcResult:
    ok: bool
    http_status: int
    grpc_status: Optional[int]
    messages: List[Any] = field(default_factory=list)
    trailers: Dict[str, str] = field(default_factory=dict)
    raw: bytes = b""
    error: str = ""


@dataclass
class SignupResult:
    ok: bool
    http_status: int
    set_cookies: List[str] = field(default_factory=list)
    rsc_body: str = ""
    error: str = ""


class GrokAuthClient:
    """纯协议会话：只吃 session_url（本地链式口或固定代理）。"""

    GROK_HOME = "https://grok.com/"

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        session_url: str = "",
        debug: bool = False,
    ):
        self.cfg = cfg
        self.debug = debug
        browser = cfg.get("browser") or {}
        proto = cfg.get("protocol") or {}

        self.accounts_origin = (proto.get("accounts_origin") or "https://accounts.x.ai").rstrip("/")
        self.signup_url = proto.get("signup_url") or f"{self.accounts_origin}/sign-up"
        self.turnstile_sitekey = proto.get("turnstile_sitekey") or "0x4AAAAAAAhr9JGVDZbrZOo0"
        self.connect_es = proto.get("connect_es") or "connect-es/2.1.1"
        self.timeout = float(browser.get("request_timeout") or 60)
        self.impersonate = browser.get("impersonate") or "chrome131"
        self.user_agent = browser.get("user_agent") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        )
        self.sec_ch_ua = browser.get("sec_ch_ua") or (
            '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'
        )
        self.sec_ch_ua_platform = browser.get("sec_ch_ua_platform") or '"Windows"'
        self.sec_ch_ua_mobile = browser.get("sec_ch_ua_mobile") or "?0"
        self.accept_language = browser.get("accept_language") or "zh-CN,zh;q=0.9"

        self.session_url = session_url or ""
        proxies = (
            {"http": self.session_url, "https": self.session_url} if self.session_url else None
        )
        self.s = Session(impersonate=self.impersonate, verify=False)
        if proxies:
            self.s.proxies = proxies

        self._next_action_id: Optional[str] = None
        self._next_router_state_tree: Optional[str] = None
        self._last_signup_html: str = ""
        self._last_rsc_body: str = ""
        self._last_create_set_cookies: List[str] = []

    def _reset_http_session(self) -> None:
        """Recreate curl_cffi Session after TLS/proxy death (curl 35/56).

        Keeps cookies/proxies so Verify/create can continue without full re-scrape.
        """
        try:
            old = self.s
            cookies = None
            try:
                cookies = old.cookies
            except Exception:
                cookies = None
            proxies = getattr(old, "proxies", None) or (
                {"http": self.session_url, "https": self.session_url}
                if self.session_url
                else None
            )
            self.s = Session(impersonate=self.impersonate, verify=False)
            if proxies:
                self.s.proxies = proxies
            if cookies is not None:
                try:
                    self.s.cookies = cookies
                except Exception:
                    pass
            try:
                old.close()
            except Exception:
                pass
        except Exception as exc:
            logger.debug("session reset failed: %s", exc)

    # ----------------------------------------------------------------- headers
    def _base_headers(self) -> Dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept-language": self.accept_language,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
        }

    def _grpc_headers(self) -> Dict[str, str]:
        h = self._base_headers()
        h.update(
            {
                "content-type": "application/grpc-web+proto",
                "x-grpc-web": "1",
                "x-user-agent": self.connect_es,
                "accept": "*/*",
                "origin": self.accounts_origin,
                "referer": self.signup_url,
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
        )
        return h

    # ----------------------------------------------------------------- scrape
    _RSC_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')

    def load_signup_page(
        self,
        *,
        force: bool = False,
        use_cache: bool = True,
        cache_ttl: float = 600.0,
    ) -> dict[str, Any]:
        """GET sign-up，动态刮 next-action / router-state-tree / sitekey。

        use_cache: 可复用进程内公开页元数据（非 cookie/会话）。
        force: 忽略缓存强制重刮。
        """
        from . import scrape_cache

        if use_cache and not force:
            hit = scrape_cache.get(ttl=cache_ttl)
            if hit:
                self._next_action_id = hit["next_action"]
                self._next_router_state_tree = hit["router_state_tree"]
                sk = (hit.get("turnstile_sitekey") or "").strip()
                if sk:
                    self.turnstile_sitekey = sk
                info = {
                    "http_status": 0,
                    "html_len": 0,
                    "next_action": self._next_action_id,
                    "router_tree_len": len(self._next_router_state_tree or ""),
                    "turnstile_sitekey": self.turnstile_sitekey,
                    "scrape_cache": "hit",
                }
                logger.info(
                    "[scrape] cache hit next-action=%s... sitekey=%s",
                    (self._next_action_id or "")[:16],
                    self.turnstile_sitekey,
                )
                return info

        h = self._base_headers()
        h.update(
            {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-fetch-site": "none",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
                "upgrade-insecure-requests": "1",
            }
        )
        r = self.s.get(self.signup_url, headers=h, timeout=self.timeout)
        html = r.text or ""
        self._last_signup_html = html
        if self.debug:
            logger.info("[scrape] GET %s -> %s len=%s", self.signup_url, r.status_code, len(html))

        if r.status_code != 200 or len(html) < 200:
            preview = html[:240].replace("\n", " ")
            raise RuntimeError(
                f"load_signup_page failed: HTTP {r.status_code}, body_len={len(html)}, preview={preview!r}"
            )

        self._scrape_rsc_payload(html)
        live_key = self._scrape_turnstile_sitekey(html)
        if live_key:
            self.turnstile_sitekey = live_key

        info = {
            "http_status": r.status_code,
            "html_len": len(html),
            "next_action": self._next_action_id,
            "router_tree_len": len(self._next_router_state_tree or ""),
            "turnstile_sitekey": self.turnstile_sitekey,
            "scrape_cache": "miss",
        }
        logger.info(
            "[scrape] next-action=%s... router_tree_len=%s sitekey=%s",
            (self._next_action_id or "")[:16],
            info["router_tree_len"],
            self.turnstile_sitekey,
        )
        if use_cache and self._next_action_id and self._next_router_state_tree:
            scrape_cache.put(
                next_action=self._next_action_id,
                router_state_tree=self._next_router_state_tree,
                turnstile_sitekey=self.turnstile_sitekey or "",
                ttl=cache_ttl,
            )
        return info

    @staticmethod
    def _scrape_turnstile_sitekey(html: str) -> Optional[str]:
        if not html:
            return None
        patterns = (
            r'sitekey["\']\s*[:=]\s*["\'](0x4[0-9A-Za-z_-]{10,})["\']',
            r'data-sitekey=["\'](0x4[0-9A-Za-z_-]{10,})["\']',
            r'Turnstile[^"]{0,80}["\'](0x4[0-9A-Za-z_-]{10,})["\']',
            r"(0x4AAAAA[0-9A-Za-z_-]{8,})",
        )
        for pat in patterns:
            m = re.search(pat, html, flags=re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _balanced_json_array(text: str, start: int) -> str | None:
        """从 text[start]=='[' 起切出完整 JSON 数组（括号配对，忽略字符串内括号）。"""
        if start < 0 or start >= len(text) or text[start] != "[":
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    @staticmethod
    def _extract_router_tree(node: object) -> list | None:
        """从 RSC f 节点里抽出 ["", {children...}, "$undefined", "$undefined", 16]。"""
        if not isinstance(node, list) or not node:
            return None
        if (
            node[0] == ""
            and len(node) >= 2
            and isinstance(node[1], dict)
            and "children" in node[1]
        ):
            return node
        # f 常见形态: [router_tree, rendered_tree] 或再包一层
        if isinstance(node[0], list):
            return GrokAuthClient._extract_router_tree(node[0])
        return None

    def _scrape_rsc_payload(self, html: str) -> None:
        rsc_segments = self._RSC_PUSH_RE.findall(html)
        if self.debug:
            logger.info("[scrape] RSC segments=%s", len(rsc_segments))

        router_tree = None
        source = "none"
        for seg in rsc_segments:
            unescaped = seg.replace('\\"', '"')
            # 页面: "f":[[["",{"children":...},"$undefined","$undefined",16], [rendered...]]]
            # 旧 regex 非贪婪只吃到 "["，永远解析失败 → 误用 fallback
            for m in re.finditer(r'"f":\[', unescaped):
                arr_txt = self._balanced_json_array(unescaped, m.end() - 1)
                if not arr_txt:
                    continue
                if "(app)" not in arr_txt and "sign-up" not in arr_txt:
                    continue
                try:
                    parsed = json.loads(arr_txt)
                except (json.JSONDecodeError, TypeError):
                    continue
                tree = self._extract_router_tree(parsed)
                if tree is None:
                    continue
                dump = json.dumps(tree, separators=(",", ":"))
                if "sign-up" not in dump and "(app)" not in dump:
                    continue
                router_tree = dump
                source = "rsc_f"
                break
            if router_tree is not None:
                break

        if router_tree is None:
            # 离线 fallback（当前页面常见无 redirect 形态）
            fallback = [
                "",
                {
                    "children": [
                        "(app)",
                        {
                            "children": [
                                "(auth)",
                                {
                                    "children": [
                                        "sign-up",
                                        {"children": ["__PAGE__", {}]},
                                    ]
                                },
                            ]
                        },
                    ]
                },
                "$undefined",
                "$undefined",
                16,
            ]
            router_tree = json.dumps(fallback, separators=(",", ":"))
            source = "fallback"
            logger.warning("[scrape] router-state-tree 用 fallback")
        else:
            logger.info(
                "[scrape] router-state-tree source=%s len=%s",
                source,
                len(router_tree),
            )

        self._next_router_state_tree = quote(router_tree, safe="")
        self._next_action_id = self._scrape_action_id(html)

    def _scrape_action_id(self, html: str) -> str:
        js_urls = list(set(re.findall(r'src="(/_next/static/chunks/[^"]+\.js)"', html)))
        if self.debug:
            logger.info("[scrape] JS chunks=%s", len(js_urls))
        if not js_urls:
            raise RuntimeError("sign-up 页面未找到 /_next/static/chunks/*.js")

        signup_hash: Optional[str] = None
        fallback_hash: Optional[str] = None

        def _fetch_and_search(path: str) -> Tuple[Optional[str], bool]:
            try:
                full = f"{self.accounts_origin}{path}"
                rr = self.s.get(full, headers=self._base_headers(), timeout=self.timeout)
                text = rr.text or ""
                hashes = set(re.findall(r'"([a-f0-9]{42})"', text))
                if not hashes:
                    return None, False
                is_signup = any(
                    kw in text for kw in ("createUserAndSessionRequest", "emailValidationCode")
                )
                return next(iter(hashes)), is_signup
            except Exception:
                return None, False

        workers = min(8, max(1, len(js_urls)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_and_search, u): u for u in js_urls}
            for f in as_completed(futs):
                h, is_signup = f.result()
                if not h:
                    continue
                if is_signup:
                    signup_hash = h
                    if self.debug:
                        logger.info("[scrape] SIGN-UP chunk: %s hash=%s", futs[f], h[:16])
                elif fallback_hash is None:
                    fallback_hash = h

        action_hash = signup_hash or fallback_hash
        if not action_hash:
            raise RuntimeError(
                "未能从 JS chunk 提取 next-action（42-hex）。页面结构可能已变。"
            )
        if self.debug:
            logger.info(
                "[scrape] action_id=%s (%s chars, %s)",
                action_hash[:16],
                len(action_hash),
                "signup" if signup_hash else "fallback",
            )
        return action_hash

    @property
    def next_action_id(self) -> str:
        if not self._next_action_id:
            raise RuntimeError("next_action_id 不可用，先 load_signup_page()")
        return self._next_action_id

    @property
    def next_router_state_tree(self) -> str:
        if not self._next_router_state_tree:
            raise RuntimeError("next_router_state_tree 不可用，先 load_signup_page()")
        return self._next_router_state_tree

    # ----------------------------------------------------------------- gRPC
    def _grpc_call(self, method: str, fields: List[Tuple[int, str]]) -> GrpcResult:
        """POST one gRPC-web method.

        Parse/proxy flakes (e.g. unsupported wire type from truncated body) return
        ``ok=False`` with ``parse_error:…`` — never raise — and retry once.
        """
        url = f"{self.accounts_origin}/{GRPC_SERVICE}/{method}"
        body = grpcweb.frame_request(grpcweb.encode_message(fields))
        headers = self._grpc_headers()
        last: GrpcResult | None = None
        # 3 attempts: high-j residential proxy often needs a second TLS recovery
        for attempt in range(3):
            try:
                r = self.s.post(url, headers=headers, data=body, timeout=self.timeout)
            except Exception as exc:
                last = GrpcResult(
                    ok=False,
                    http_status=0,
                    grpc_status=None,
                    error=f"request_error:{exc}",
                )
                if attempt < 2:
                    es = str(exc).lower()
                    if any(
                        x in es
                        for x in (
                            "curl: (35)",
                            "curl: (56)",
                            "tls",
                            "ssl",
                            "openssl",
                            "invalid library",
                            "connect tunnel",
                        )
                    ):
                        self._reset_http_session()
                    logger.debug(
                        "[grpc] %s request_error attempt=%s → retry: %s",
                        method,
                        attempt + 1,
                        exc,
                    )
                    continue
                return last
            raw = r.content or b""
            if not raw:
                last = GrpcResult(
                    ok=False,
                    http_status=r.status_code,
                    grpc_status=None,
                    raw=raw,
                    error="empty_body",
                )
                if attempt < 2:
                    logger.debug(
                        "[grpc] %s empty_body attempt=%s → retry", method, attempt + 1
                    )
                    continue
                return last
            try:
                parsed = grpcweb.parse_response(raw)
            except (ValueError, IndexError, OSError) as exc:
                # corrupt / non-proto frame (wire type, trunc) — retry once then soft-fail
                last = GrpcResult(
                    ok=False,
                    http_status=r.status_code,
                    grpc_status=None,
                    raw=raw,
                    error=f"parse_error:{exc}",
                )
                if attempt < 2:
                    logger.debug(
                        "[grpc] %s parse_error attempt=%s → retry: %s",
                        method,
                        attempt + 1,
                        exc,
                    )
                    continue
                return last
            grpc_status = parsed.get("grpc_status")
            ok = r.status_code == 200 and grpc_status == 0
            return GrpcResult(
                ok=ok,
                http_status=r.status_code,
                grpc_status=grpc_status,
                messages=parsed.get("messages") or [],
                trailers=parsed.get("trailers") or {},
                raw=raw,
                error="" if ok else f"http={r.status_code} grpc={grpc_status}",
            )
        return last or GrpcResult(ok=False, http_status=0, grpc_status=None, error="grpc_call_failed")

    def create_email_validation_code(self, email: str) -> GrpcResult:
        logger.debug("[grpc] CreateEmailValidationCode email=%s", email)
        res = self._grpc_call("CreateEmailValidationCode", [(1, email)])
        logger.debug(
            "[grpc] create -> ok=%s http=%s grpc=%s err=%s",
            res.ok,
            res.http_status,
            res.grpc_status,
            res.error or "-",
        )
        return res

    def verify_email_validation_code(self, email: str, code: str) -> GrpcResult:
        logger.debug("[grpc] VerifyEmailValidationCode email=%s code=%s", email, code)
        res = self._grpc_call("VerifyEmailValidationCode", [(1, email), (2, code)])
        logger.debug(
            "[grpc] verify -> ok=%s http=%s grpc=%s err=%s",
            res.ok,
            res.http_status,
            res.grpc_status,
            res.error or "-",
        )
        return res

    def validate_password(self, email: str, password: str) -> GrpcResult:
        logger.debug("[grpc] ValidatePassword email=%s", email)
        # 参考实现字段号 4/5
        res = self._grpc_call("ValidatePassword", [(4, email), (5, password)])
        logger.debug(
            "[grpc] validate_pw -> ok=%s http=%s grpc=%s err=%s",
            res.ok,
            res.http_status,
            res.grpc_status,
            res.error or "-",
        )
        return res

    # ----------------------------------------------------------------- low-level request
    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        allow_redirects: bool = False,
    ) -> Tuple[int, Dict[str, str], List[str], bytes]:
        kwargs: Dict[str, Any] = {
            "headers": headers or {},
            "timeout": self.timeout,
            "allow_redirects": allow_redirects,
        }
        if body is not None:
            kwargs["data"] = body
        r = self.s.request(method.upper(), url, **kwargs)
        # curl_cffi: headers may be multi-value via .headers.get_list if available
        set_cookies: List[str] = []
        try:
            if hasattr(r.headers, "get_list"):
                set_cookies = list(r.headers.get_list("set-cookie") or [])
            elif hasattr(r.headers, "getlist"):
                set_cookies = list(r.headers.getlist("set-cookie") or [])
        except Exception:
            set_cookies = []
        if not set_cookies:
            sc = r.headers.get("set-cookie") or r.headers.get("Set-Cookie")
            if sc:
                set_cookies = [sc]
        resp_headers = {str(k).lower(): str(v) for k, v in dict(r.headers).items()}
        return int(r.status_code), resp_headers, set_cookies, r.content or b""

    # ----------------------------------------------------------------- create account
    def create_account(
        self,
        *,
        email: str,
        given_name: str,
        family_name: str,
        password: str,
        email_validation_code: str,
        turnstile_token: str,
        castle_request_token: str = "",
        conversion_id: Optional[str] = None,
        tos_accepted_version: Optional[str] = None,
    ) -> SignupResult:
        if not self._next_action_id:
            self.load_signup_page()

        create_req = {
            "email": email,
            "givenName": given_name,
            "familyName": family_name,
            "clearTextPassword": password,
            "tosAcceptedVersion": (
                tos_accepted_version if tos_accepted_version is not None else "$undefined"
            ),
        }
        args = [
            {
                "emailValidationCode": email_validation_code,
                "createUserAndSessionRequest": create_req,
                "turnstileToken": turnstile_token,
                "conversionId": conversion_id or str(uuid.uuid4()),
                "castleRequestToken": castle_request_token or "",
            },
            {"client": "$T", "meta": "$undefined", "mutationKey": "$undefined"},
        ]
        body = json.dumps(args, separators=(",", ":")).encode("utf-8")

        h = self._base_headers()
        h.update(
            {
                "accept": "text/x-component",
                "content-type": "text/plain;charset=UTF-8",
                "next-action": self.next_action_id,
                "next-router-state-tree": self.next_router_state_tree,
                "origin": self.accounts_origin,
                "referer": self.signup_url,
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
        )
        status, resp_headers, set_cookies, raw = self._request(
            "POST", self.signup_url, headers=h, body=body
        )
        rsc_body = raw.decode("utf-8", "replace")
        self._last_rsc_body = rsc_body
        self._last_create_set_cookies = list(set_cookies or [])

        hard_error = self.extract_signup_error(rsc_body)
        looks_ok = self._signup_response_looks_ok(rsc_body, set_cookies or [], resp_headers or {})
        if status == 200 and not hard_error:
            ok = True
        else:
            ok = (status == 200) and looks_ok and not hard_error

        logger.info(
            "[create_account] HTTP %s ok=%s hard_error=%s set_cookies=%s body_len=%s",
            status,
            ok,
            hard_error or "-",
            len(set_cookies or []),
            len(rsc_body),
        )
        if self.debug and (not ok or hard_error):
            logger.debug("[create_account] body preview=%r", rsc_body[:400])

        return SignupResult(
            ok=ok,
            http_status=status,
            set_cookies=set_cookies or [],
            rsc_body=rsc_body,
            error=hard_error or ("" if ok else f"http={status}"),
        )

    @staticmethod
    def extract_signup_error(rsc_body: str) -> Optional[str]:
        text = rsc_body or ""
        if not text:
            return None
        text_l = text.lower()

        m = re.search(r"(?m)^(\d+):E\{([^}]{0,400})", text)
        if m:
            return f"next_action_error:{m.group(2)[:160]}"

        m = re.search(r"\[?\s*wke\s*=\s*([a-z0-9_.:/-]+)\s*\]?", text_l, flags=re.I)
        if m:
            return f"wke={m.group(1)}"

        m = re.search(
            r'"error"\s*:\s*"(\[invalid_argument\][^"]{0,200})"',
            text,
            flags=re.I,
        )
        if m:
            err = m.group(1)
            m2 = re.search(r"wke\s*=\s*([a-z0-9_.:/-]+)", err, flags=re.I)
            if m2:
                return f"wke={m2.group(1)}"
            return err[:160]

        code_patterns = (
            r"\b(turnstile_failed)\b",
            r"\b(account_signup_error)\b",
            r"\b(rate_limited)\b",
            r"\b(validation_error)\b",
            r"\b(invalid_verification_code)\b",
            r"\b(invalid-validation-code)\b",
            r"\b(email_already_in_use)\b",
            r"\b(user_already_exists)\b",
            r"\b(invalid-credentials)\b",
            r"\b(unauthenticated:no-credentials)\b",
            r"\b(account:invalid[a-z0-9_-]*)\b",
            r"\b(account_email_domain_rejected)\b",
            r"\b(form_invalid_disposable_email)\b",
            r"\b(account_email_malformed)\b",
        )
        for pat in code_patterns:
            m = re.search(pat, text_l, flags=re.I)
            if m:
                return m.group(1)

        framed = (
            (r"email validation code is invalid", "wke=email:invalid-validation-code"),
            (r"email already(?: in use| exists| registered)?", "email_already_in_use"),
            (r"already registered", "already_registered"),
            (r"account already exists", "account_already_exists"),
            (r"signup failed|sign-up failed", "signup_failed"),
            (r"too many requests|rate limit(?:ed)?", "rate_limited"),
            (r"password is too|weak password", "weak_password"),
        )
        for pat, code in framed:
            if re.search(pat, text_l):
                return code
        return None

    @staticmethod
    def _signup_response_looks_ok(
        rsc_body: str,
        set_cookies: List[str],
        resp_headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        text = rsc_body or ""
        text_l = text.lower()
        joined = "\n".join(set_cookies or []).lower()
        headers_l = " ".join(f"{k}:{v}" for k, v in (resp_headers or {}).items()).lower()

        if GrokAuthClient.extract_signup_error(text):
            return False
        if "sso=" in joined or "last-logged-in-with=" in joined:
            return True
        if "set-cookie" in headers_l and ("sso=" in headers_l or "last-logged-in-with=" in headers_l):
            return True
        if re.search(
            r"set-cookie\?q=eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
            text,
            flags=re.I,
        ):
            return True
        if re.search(
            r"\bsso=eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
            text,
            flags=re.I,
        ):
            return True
        if any(
            x in text_l
            for x in (
                "session_id",
                "signed_in",
                "logged_in",
                "last-logged-in-with",
                "createuserandsessionresponse",
                "principal_id",
            )
        ):
            return True
        rsc_ok = (
            r"\$Sreact\.fragment",
            r"/_next/static/chunks/",
            r"(?m)^\d+:I\[",
            r"(?m)^\d+:\"\$Sreact",
            r"(?m)^\d+:\[\"\$@",
            r"(?m)^\d+:null",
            r"react\.fragment",
        )
        if any(re.search(p, text, flags=re.I) for p in rsc_ok):
            return True
        stripped = text.strip()
        if stripped in {"0:null", "1:null", "0:true", "1:true", "null", "true"}:
            return True
        # ambiguous HTTP 200: continue, let SSO decide
        return True

    # ----------------------------------------------------------------- SSO
    def _read_sso_from_jar(self) -> Optional[str]:
        c = self.s.cookies
        if hasattr(c, "get"):
            for domain in (".grok.com", "grok.com", ".x.ai", "accounts.x.ai", None):
                try:
                    val = c.get("sso", domain=domain) if domain is not None else c.get("sso")
                    if val:
                        return str(val)
                except Exception:
                    pass
        if hasattr(c, "jar"):
            for cookie in c.jar:
                if str(getattr(cookie, "name", "")).lower() == "sso":
                    val = str(getattr(cookie, "value", "") or "")
                    if val:
                        return val
        return None

    def _fetch_sso_via_url(self, url: str, *, label: str = "fallback") -> Optional[str]:
        headers = self._base_headers()
        headers.update(
            {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-fetch-site": "cross-site",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
                "referer": self.accounts_origin + "/",
            }
        )
        try:
            status, hdrs, set_cookies, raw = self._request("GET", url, headers=headers)
            if self.debug:
                logger.info(
                    "[sso] %s HTTP %s %s set-cookies=%s",
                    label,
                    status,
                    url[:64],
                    len(set_cookies or []),
                )
            token = parse_sso_from_set_cookies(set_cookies or [])
            if token:
                return token
            body = (raw or b"").decode("utf-8", "replace")
            token = parse_sso_token_from_text(body)
            if token:
                return token
            loc = str(hdrs.get("location") or "")
            if loc.startswith("http"):
                status2, _h2, sc2, raw2 = self._request("GET", loc, headers=headers)
                if self.debug:
                    logger.info(
                        "[sso] %s redirect HTTP %s set-cookies=%s",
                        label,
                        status2,
                        len(sc2 or []),
                    )
                token = parse_sso_from_set_cookies(sc2 or []) or parse_sso_token_from_text(
                    (raw2 or b"").decode("utf-8", "replace")
                )
                if token:
                    return token
        except Exception as exc:
            if self.debug:
                logger.info("[sso] %s failed: %s", label, exc)
        return self._read_sso_from_jar()

    def obtain_session_via_password(
        self,
        *,
        email: str,
        password: str,
        turnstile_token: str,
        retries: int = 2,
    ) -> Optional[str]:
        """Password CreateSession when create_account left no sso cookie chain.

        Pattern from xconsole: RSC may omit set-cookie JWT; CreateSession with
        email+password+fresh Turnstile can still yield session JWT as ``sso``.
        """
        email_n = (email or "").strip()
        password_n = password or ""
        tok = (turnstile_token or "").strip()
        if not email_n or not password_n or not tok:
            return None

        signin = f"{self.accounts_origin}/sign-in?redirect=grok-com"
        try:
            self.s.get(
                signin,
                headers={
                    **self._base_headers(),
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-dest": "document",
                    "referer": self.accounts_origin + "/",
                },
                timeout=self.timeout,
            )
        except Exception as exc:
            logger.debug("[sso] sign-in warm failed: %s", exc)

        url = f"{self.accounts_origin}/{GRPC_SERVICE}/CreateSession"
        emails: List[str] = []
        for e in (email_n, email_n.lower()):
            if e and e not in emails:
                emails.append(e)

        for attempt in range(1, max(1, int(retries)) + 1):
            for em in emails:
                try:
                    from urllib.parse import unquote

                    body = grpcweb.frame_request(
                        grpcweb.encode_create_session_request(
                            em, password_n, turnstile_token=tok
                        )
                    )
                    headers = self._grpc_headers()
                    headers["referer"] = signin
                    status, hdrs, set_cookies, raw = self._request(
                        "POST", url, headers=headers, body=body
                    )
                    token = (
                        parse_sso_from_set_cookies(set_cookies or [])
                        or self._read_sso_from_jar()
                    )
                    if token:
                        logger.info(
                            "[sso] CreateSession OK email=%s attempt=%s", em, attempt
                        )
                        return token
                    grpc_st = hdrs.get("grpc-status")
                    grpc_msg = unquote(str(hdrs.get("grpc-message") or ""))
                    parsed = {"messages": [], "grpc_status": None}
                    if raw:
                        try:
                            parsed = grpcweb.parse_response(raw)
                        except Exception as exc:
                            logger.warning(
                                "[sso] CreateSession parse_error attempt=%s: %s",
                                attempt,
                                exc,
                            )
                    if parsed.get("grpc_status") is not None:
                        grpc_st = str(parsed.get("grpc_status"))
                    for msg in parsed.get("messages") or []:
                        for f in msg:
                            if f.get("type") == "string":
                                val = str(f.get("value") or "")
                                if val.startswith("eyJ") and val.count(".") >= 2:
                                    logger.info(
                                        "[sso] CreateSession JWT field=%s attempt=%s",
                                        f.get("field"),
                                        attempt,
                                    )
                                    return val
                    logger.warning(
                        "[sso] CreateSession no-token HTTP=%s grpc=%s msg=%s "
                        "attempt=%s body_len=%s",
                        status,
                        grpc_st,
                        (grpc_msg or "-")[:120],
                        attempt,
                        len(raw or b""),
                    )
                    # hard credential fail: no point retrying same password
                    if "invalid-credentials" in (grpc_msg or "").lower():
                        return None
                except Exception as exc:
                    logger.warning(
                        "[sso] CreateSession request_error attempt=%s: %s",
                        attempt,
                        exc,
                    )
            if attempt < max(1, int(retries)):
                time.sleep(0.6 * attempt)
        return None

    def fetch_sso_token(
        self,
        *,
        retries: int = 3,
        email: str = "",
        password: str = "",
        turnstile_token: str = "",
    ) -> Optional[str]:
        """create_account 成功后提取 sso cookie JWT.

        If hop extraction fails and email/password/turnstile_token are provided,
        try CreateSession password rescue once (does not re-register).
        """
        token = parse_sso_from_set_cookies(self._last_create_set_cookies or [])
        if token and self.debug:
            logger.info("[sso] found in create_account Set-Cookie")

        rsc_text = self._last_rsc_body or ""
        if not token and rsc_text:
            token = parse_sso_token_from_text(rsc_text)
            if token and self.debug:
                logger.info("[sso] found raw sso token in create_account RSC body")

        attempts = max(1, int(retries))
        for attempt in range(1, attempts + 1):
            if token:
                break
            if rsc_text:
                extractor = SSOExtractor(
                    transport_request=self._request,
                    base_headers=self._base_headers,
                    cookie_jar=self.s.cookies,
                    debug=self.debug,
                )
                token = extractor.extract(rsc_text)
            if not token:
                for url, label in (
                    ("https://auth.x.ai/set-cookie", "auth.x.ai/set-cookie"),
                    (
                        "https://auth.grokusercontent.com/set-cookie",
                        "grokusercontent/set-cookie",
                    ),
                    ("https://grok.com/", "grok.com.home"),
                    ("https://accounts.x.ai/", "accounts.home"),
                    (
                        "https://accounts.x.ai/sign-in?redirect=grok-com",
                        "accounts.signin",
                    ),
                ):
                    token = self._fetch_sso_via_url(url, label=label)
                    if token:
                        break
            if not token:
                token = self._read_sso_from_jar()
            if token:
                break
            if attempt < attempts:
                if self.debug:
                    logger.info(
                        "[sso] attempt %s/%s failed, retrying...", attempt, attempts
                    )
                time.sleep(0.8 * attempt)

        # Password CreateSession rescue (industry fallback when hop chain empty)
        if not token and email and password and turnstile_token:
            logger.warning(
                "[sso] hop path empty → CreateSession password rescue email=%s",
                email,
            )
            token = self.obtain_session_via_password(
                email=email,
                password=password,
                turnstile_token=turnstile_token,
                retries=2,
            )

        if token:
            payload = parse_sso_jwt_payload(token) or {}
            logger.info(
                "[sso] ok session_id=%s",
                str(payload.get("session_id") or "?")[:16],
            )
        else:
            logger.warning("[sso] extraction failed")
        return token


    def close(self) -> None:
        try:
            self.s.close()
        except Exception:
            pass
