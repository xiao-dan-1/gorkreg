"""Turnstile solver implementations (canonical under backends.captcha).

Pure protocol cannot forge Turnstile tokens. Paths:
  1) YesCaptcha / 2Captcha createTask
  2) Local browser solver (Camoufox / Playwright)
  3) Manual paste (ManualCaptcha)
Castle often left empty (needs browser JS).

Public re-export: ``grokreg.solver`` (compat). Prefer
``grokreg.backends.captcha`` for new code.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

from curl_cffi import requests as cr

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINTS = (
    "https://api.yescaptcha.com",
    "https://cn.yescaptcha.com",
)

TURNSTILE_API_JS = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"


def resolve_yescaptcha_endpoint(explicit: str | None = None) -> str:
    env = (
        os.environ.get("GROK2API_YESCAPTCHA_ENDPOINT")
        or os.environ.get("YESCAPTCHA_ENDPOINT")
        or os.environ.get("YESCAPTCHA_API_BASE")
        or ""
    ).strip()
    if env:
        return env.rstrip("/")
    if explicit:
        return explicit.rstrip("/")
    return DEFAULT_ENDPOINTS[0]


def resolve_yescaptcha_key(explicit: str | None = None) -> str:
    return (
        (explicit or "").strip()
        or os.environ.get("GROK2API_YESCAPTCHA_KEY", "").strip()
        or os.environ.get("YESCAPTCHA_API_KEY", "").strip()
        or os.environ.get("YESCAPTCHA_KEY", "").strip()
    )


def resolve_twocaptcha_key(explicit: str | None = None) -> str:
    return (
        (explicit or "").strip()
        or os.environ.get("TWOCAPTCHA_API_KEY", "").strip()
        or os.environ.get("TWO_CAPTCHA_API_KEY", "").strip()
        or os.environ.get("APIKEY_2CAPTCHA", "").strip()
        or os.environ.get("CAPTCHA_2CAPTCHA_KEY", "").strip()
    )


def resolve_capsolver_key(explicit: str | None = None) -> str:
    return (
        (explicit or "").strip()
        or os.environ.get("CAPSOLVER_API_KEY", "").strip()
        or os.environ.get("CAPSOLVER_KEY", "").strip()
        or os.environ.get("CAP_SOLVER_API_KEY", "").strip()
    )


def resolve_capsolver_endpoint(explicit: str | None = None) -> str:
    env = (
        os.environ.get("CAPSOLVER_ENDPOINT", "")
        or os.environ.get("CAPSOLVER_API_BASE", "")
        or ""
    ).strip()
    if env:
        return env.rstrip("/")
    if explicit:
        return str(explicit).strip().rstrip("/")
    return "https://api.capsolver.com"


class YesCaptchaSolver:
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str | None = None,
        timeout: float = 180.0,
        poll_interval: float = 1.5,
        debug: bool = False,
        on_progress: Optional[Callable[[str], None]] = None,
        auto_fallback_endpoint: bool = True,
        proxy: str | None = None,
    ):
        self._api_key = (api_key or "").strip()
        self._endpoint = resolve_yescaptcha_endpoint(endpoint)
        self._timeout = float(timeout)
        # Adaptive poll: start at poll_interval, back off toward 3s (token ready earlier → wall↓).
        self._poll_interval = max(0.5, float(poll_interval))
        self._debug = debug
        self._on_progress = on_progress
        self._auto_fallback_endpoint = auto_fallback_endpoint
        self._proxy = proxy or None

    def _progress(self, msg: str) -> None:
        if self._debug:
            logger.info("[YesCaptcha] %s", msg)
        if self._on_progress:
            try:
                self._on_progress(msg)
            except Exception:
                pass

    def _proxies(self) -> dict | None:
        if not self._proxy:
            return None
        return {"http": self._proxy, "https": self._proxy}

    def _post_json(self, path: str, payload: dict, *, timeout: float = 45.0) -> dict:
        url = f"{self._endpoint}{path}"
        try:
            resp = cr.post(
                url,
                json=payload,
                timeout=timeout,
                proxies=self._proxies(),
                impersonate="chrome131",
            )
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"YesCaptcha non-object response: {data!r}")
            return data
        except Exception as first_err:
            if not self._auto_fallback_endpoint:
                raise
            peer = None
            if "cn.yescaptcha.com" in self._endpoint:
                peer = "https://api.yescaptcha.com"
            elif "api.yescaptcha.com" in self._endpoint:
                peer = "https://cn.yescaptcha.com"
            if not peer or peer.rstrip("/") == self._endpoint.rstrip("/"):
                raise
            self._progress(f"endpoint {self._endpoint} failed ({first_err}); fallback {peer}")
            self._endpoint = peer
            resp = cr.post(
                f"{self._endpoint}{path}",
                json=payload,
                timeout=timeout,
                proxies=self._proxies(),
                impersonate="chrome131",
            )
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"YesCaptcha non-object response: {data!r}")
            return data

    def _create_task(self, task: dict) -> str:
        payload = {"clientKey": self._api_key, "task": task}
        self._progress(f"createTask type={task.get('type')} endpoint={self._endpoint}")
        data = self._post_json("/createTask", payload)
        if data.get("errorId", 0) != 0:
            raise RuntimeError(
                f"YesCaptcha createTask failed: {data.get('errorCode')}: {data.get('errorDescription')}"
            )
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"YesCaptcha createTask returned no taskId: {data}")
        self._progress(f"task created: {task_id}")
        return str(task_id)

    def _get_result(self, task_id: str) -> dict:
        payload = {"clientKey": self._api_key, "taskId": task_id}
        started = time.time()
        deadline = started + self._timeout
        sleep_s = float(self._poll_interval)
        while time.time() < deadline:
            data = self._post_json("/getTaskResult", payload)
            if data.get("errorId", 0) != 0:
                raise RuntimeError(
                    f"YesCaptcha getTaskResult error: {data.get('errorCode')}: {data.get('errorDescription')}"
                )
            status = str(data.get("status") or "")
            if status == "ready":
                self._progress(f"solved in ~{int(time.time() - started)}s")
                return data
            if status in ("processing", "idle", ""):
                elapsed = int(time.time() - started)
                if elapsed % 9 < sleep_s:
                    self._progress(f"still processing ({elapsed}s/{int(self._timeout)}s)...")
                time.sleep(sleep_s)
                # exponential-ish backoff: 1.5 → 2.0 → 2.5 → 3.0 cap
                sleep_s = min(3.0, sleep_s + 0.5)
                continue
            raise RuntimeError(f"YesCaptcha unexpected status: {status} body={data}")
        raise TimeoutError(
            f"YesCaptcha task {task_id} did not complete within {self._timeout}s"
        )

    def get_balance(self) -> float:
        """POST /getBalance → platform credits (not currency). Raises on API error."""
        data = self._post_json("/getBalance", {"clientKey": self._api_key}, timeout=30.0)
        if data.get("errorId", 0) != 0:
            raise RuntimeError(
                f"YesCaptcha getBalance failed: {data.get('errorCode')}: {data.get('errorDescription')}"
            )
        bal = data.get("balance")
        try:
            return float(bal)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"YesCaptcha getBalance bad balance={bal!r}") from e

    def solve_turnstile(
        self,
        website_url: str,
        website_key: str,
        *,
        premium: bool = True,
        fallback_non_premium: bool = True,
    ) -> str:
        website_url = (website_url or "").strip()
        website_key = (website_key or "").strip()
        if not website_url or not website_key:
            raise ValueError("website_url and website_key are required for Turnstile")

        task_types: list[str] = []
        if premium:
            task_types.append("TurnstileTaskProxylessM1")
            if fallback_non_premium:
                task_types.append("TurnstileTaskProxyless")
        else:
            task_types.append("TurnstileTaskProxyless")
            if fallback_non_premium:
                task_types.append("TurnstileTaskProxylessM1")

        errors: list[str] = []
        for idx, task_type in enumerate(task_types):
            task = {
                "type": task_type,
                "websiteURL": website_url,
                "websiteKey": website_key,
            }
            try:
                self._progress(
                    f"solve_turnstile try {idx + 1}/{len(task_types)} type={task_type}"
                )
                task_id = self._create_task(task)
                result = self._get_result(task_id)
                solution = result.get("solution") or {}
                token = (
                    solution.get("token")
                    or solution.get("gRecaptchaResponse")
                    or solution.get("cf_clearance")
                )
                if not token:
                    raise RuntimeError(f"YesCaptcha returned no token: {result}")
                return str(token)
            except Exception as e:
                errors.append(f"{task_type}: {e}")
                self._progress(f"failed: {task_type}: {e}")
                if idx + 1 < len(task_types):
                    time.sleep(1.0)
        raise RuntimeError(
            "YesCaptcha Turnstile solve failed: " + " | ".join(errors[:4])
        )


class TwoCaptchaSolver:
    """2Captcha Turnstile（对照 any-auto-register + 官方 in.php/res.php）。

    文档: https://2captcha.com/api-docs/cloudflare-turnstile
    标价约 $1.45 / 1000（以官网为准）。
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://2captcha.com",
        timeout: float = 180.0,
        poll_interval: float = 1.5,
        debug: bool = False,
        proxy: str | None = None,
    ):
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise ValueError("2Captcha api_key required")
        self._endpoint = (endpoint or "https://2captcha.com").rstrip("/")
        self._timeout = float(timeout)
        self._poll_interval = max(0.5, float(poll_interval))
        self._debug = debug
        self._proxy = proxy or None

    def _progress(self, msg: str) -> None:
        if self._debug:
            logger.info("[2Captcha] %s", msg)

    def _proxies(self) -> dict | None:
        if not self._proxy:
            return None
        return {"http": self._proxy, "https": self._proxy}

    def get_balance(self) -> float:
        """GET res.php?action=getbalance → USD balance. Raises on API error."""
        result = cr.get(
            f"{self._endpoint}/res.php",
            params={"key": self._api_key, "action": "getbalance", "json": 1},
            timeout=30,
            proxies=self._proxies(),
            impersonate="chrome131",
        )
        try:
            data = result.json()
        except Exception as exc:
            # plain-text balance is also common
            text = (result.text or "").strip()
            try:
                return float(text)
            except ValueError as e:
                raise RuntimeError(
                    f"2Captcha getbalance non-json HTTP={result.status_code} body={text[:200]!r}"
                ) from e
        if not isinstance(data, dict):
            raise RuntimeError(f"2Captcha getbalance bad body: {data!r}")
        # json: {"status":1,"request":"1.234"} or error request=ERROR_*
        if int(data.get("status") or 0) == 1:
            try:
                return float(data.get("request"))
            except (TypeError, ValueError) as e:
                raise RuntimeError(f"2Captcha getbalance bad amount: {data}") from e
        raise RuntimeError(f"2Captcha getbalance failed: {data}")

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        website_url = (website_url or "").strip()
        website_key = (website_key or "").strip()
        if not website_url or not website_key:
            raise ValueError("website_url and website_key required")

        self._progress(f"create turnstile sitekey={website_key} url={website_url}")
        create = cr.post(
            f"{self._endpoint}/in.php",
            data={
                "key": self._api_key,
                "method": "turnstile",
                "sitekey": website_key,
                "pageurl": website_url,
                "json": 1,
            },
            timeout=45,
            proxies=self._proxies(),
            impersonate="chrome131",
        )
        try:
            payload = create.json()
        except Exception as exc:
            raise RuntimeError(
                f"2Captcha in.php non-json HTTP={create.status_code} body={create.text[:200]!r}"
            ) from exc
        if not isinstance(payload, dict) or int(payload.get("status") or 0) != 1:
            raise RuntimeError(f"2Captcha create failed: {payload}")
        task_id = payload.get("request")
        if not task_id:
            raise RuntimeError(f"2Captcha no task id: {payload}")
        self._progress(f"task={task_id}")

        started = time.time()
        deadline = started + self._timeout
        sleep_s = float(self._poll_interval)
        while time.time() < deadline:
            time.sleep(sleep_s)
            result = cr.get(
                f"{self._endpoint}/res.php",
                params={
                    "key": self._api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1,
                },
                timeout=45,
                proxies=self._proxies(),
                impersonate="chrome131",
            )
            try:
                data = result.json()
            except Exception as exc:
                raise RuntimeError(
                    f"2Captcha res.php non-json HTTP={result.status_code} body={result.text[:200]!r}"
                ) from exc
            if not isinstance(data, dict):
                raise RuntimeError(f"2Captcha bad result: {data!r}")
            if int(data.get("status") or 0) == 1:
                token = str(data.get("request") or "")
                if not token:
                    raise RuntimeError(f"2Captcha empty token: {data}")
                self._progress(f"solved in ~{int(time.time() - started)}s len={len(token)}")
                return token
            req = str(data.get("request") or "")
            if req in {"CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"}:
                elapsed = int(time.time() - started)
                if elapsed % 9 < sleep_s:
                    self._progress(f"waiting ({elapsed}s/{int(self._timeout)}s)...")
                sleep_s = min(3.0, sleep_s + 0.5)
                continue
            raise RuntimeError(f"2Captcha error: {data}")
        raise TimeoutError(f"2Captcha task {task_id} timeout {self._timeout}s")


class ManualTurnstileSolver:
    """对照 any-auto-register ManualCaptcha：阻塞等用户粘贴 token。"""

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        print(
            f"\n[ManualTurnstile] 请用浏览器打开 {website_url}\n"
            f"  sitekey={website_key}\n"
            f"  在 DevTools 里拿到 turnstile token 后粘贴回车：\n",
            flush=True,
        )
        token = input("turnstile token> ").strip()
        if not token:
            raise RuntimeError("空 token")
        return token


class LocalHttpTurnstileSolver:
    """对照 any-auto-register LocalSolverCaptcha：调本地 /turnstile + /result。"""

    def __init__(self, solver_url: str, *, timeout: float = 180.0):
        self.solver_url = (solver_url or "").rstrip("/")
        self.timeout = float(timeout)
        if not self.solver_url:
            raise ValueError("solver_url required")

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        import requests

        r = requests.get(
            f"{self.solver_url}/turnstile",
            params={"url": website_url, "sitekey": website_key},
            timeout=15,
        )
        r.raise_for_status()
        task_id = r.json().get("taskId")
        if not task_id:
            raise RuntimeError(f"LocalSolver 未返回 taskId: {r.text}")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(2)
            res = requests.get(
                f"{self.solver_url}/result",
                params={"id": task_id},
                timeout=10,
            )
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("errorId"):
                raise RuntimeError(
                    f"LocalSolver fail: {data.get('errorDescription') or data}"
                )
            if data.get("status") == "ready":
                token = (data.get("solution") or {}).get("token")
                if token:
                    return str(token)
            if data.get("status") == "CAPTCHA_FAIL":
                raise RuntimeError("LocalSolver CAPTCHA_FAIL")
        raise TimeoutError("LocalSolver Turnstile 超时")


class BrowserTurnstileSolver:
    """用本机 Chrome/Edge + Playwright 渲染 Turnstile 拿 token。

    这是无 YesCaptcha key 时开源项目的主流替代（本地浏览器 solver）。
    不保证 100% 过；managed challenge 可能仍要人工点一次。
    """

    def __init__(
        self,
        *,
        channel: str = "chrome",
        headless: bool = False,
        timeout: float = 120.0,
        proxy: str | None = None,
        debug: bool = False,
    ):
        self.channel = (channel or "chrome").strip() or "chrome"
        self.headless = bool(headless)
        self.timeout = float(timeout)
        self.proxy = proxy or None
        self.debug = debug

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "未安装 playwright。执行: pip install -r requirements-browser.txt && playwright install chromium"
            ) from exc

        website_url = (website_url or "").strip()
        website_key = (website_key or "").strip()
        if not website_url or not website_key:
            raise ValueError("website_url / website_key required")

        logger.info(
            "[BrowserTurnstile] channel=%s headless=%s sitekey=%s url=%s",
            self.channel,
            self.headless,
            website_key,
            website_url,
        )

        # 用真实 sign-up 页 + 注入 callback 拦截 token，比空白页更贴近站点上下文
        intercept_js = f"""
(() => {{
  window.__tsToken = null;
  window.__tsReady = false;
  const mark = (t) => {{
    if (t && typeof t === 'string' && t.length > 20) {{
      window.__tsToken = t;
      window.__tsReady = true;
      try {{ document.title = 'TS_OK'; }} catch (e) {{}}
    }}
  }};
  // 拦截常见回调名
  window.onloadTurnstileCallback = function() {{
    try {{
      if (window.turnstile && window.turnstile.render) {{
        const host = document.createElement('div');
        host.id = '__ts_host';
        host.style.cssText = 'position:fixed;bottom:8px;right:8px;z-index:99999';
        document.body.appendChild(host);
        window.turnstile.render(host, {{
          sitekey: {website_key!r},
          callback: mark,
          'error-callback': (e) => console.warn('ts error', e),
          'expired-callback': () => console.warn('ts expired'),
        }});
      }}
    }} catch (e) {{ console.warn(e); }}
  }};
  // 也监听隐藏 input
  const obs = new MutationObserver(() => {{
    const el = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
    if (el && el.value) mark(el.value);
  }});
  obs.observe(document.documentElement, {{subtree:true, childList:true, attributes:true}});
  setInterval(() => {{
    const el = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
    if (el && el.value) mark(el.value);
  }}, 500);
}})();
"""

        launch_kwargs: dict = {
            "channel": self.channel,
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        if self.proxy:
            # Playwright proxy: server=http://host:port ; 带 auth 需拆
            server = self.proxy
            user = passwd = None
            if "://" in server:
                # http://user:pass@host:port
                from urllib.parse import urlparse

                u = urlparse(server)
                server = f"{u.scheme}://{u.hostname}:{u.port}"
                user = u.username
                passwd = u.password
            proxy_cfg: dict = {"server": server}
            if user:
                proxy_cfg["username"] = user
            if passwd:
                proxy_cfg["password"] = passwd
            launch_kwargs["proxy"] = proxy_cfg

        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.add_init_script(intercept_js)
            try:
                page.goto(website_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                logger.warning("[BrowserTurnstile] goto warn: %s", exc)

            # 确保 turnstile script 在页上
            try:
                page.evaluate(
                    """(src) => {
                      if (!document.querySelector('script[src*="challenges.cloudflare.com/turnstile"]')) {
                        const s = document.createElement('script');
                        s.src = src;
                        s.async = true;
                        s.defer = true;
                        document.head.appendChild(s);
                      }
                    }""",
                    TURNSTILE_API_JS + "&onload=onloadTurnstileCallback",
                )
            except Exception as exc:
                logger.warning("[BrowserTurnstile] inject script warn: %s", exc)

            # 若页面已有 widget，尝试点一下
            deadline = time.time() + self.timeout
            token = ""
            while time.time() < deadline:
                try:
                    token = page.evaluate(
                        "() => window.__tsToken || "
                        "(document.querySelector('input[name=\"cf-turnstile-response\"]')||{}).value || "
                        "(document.querySelector('textarea[name=\"cf-turnstile-response\"]')||{}).value || ''"
                    ) or ""
                except Exception:
                    token = ""
                if token and len(token) > 20:
                    logger.info("[BrowserTurnstile] got token len=%s", len(token))
                    browser.close()
                    return str(token)

                # 尝试点击 checkbox iframe
                try:
                    for frame in page.frames:
                        if "challenges.cloudflare.com" in (frame.url or ""):
                            box = frame.query_selector("input[type=checkbox], #challenge-stage, body")
                            if box:
                                box.click(timeout=1000)
                except Exception:
                    pass
                page.wait_for_timeout(800)

            # 超时前再读一次
            try:
                token = page.evaluate("() => window.__tsToken || ''") or ""
            except Exception:
                token = ""
            title = ""
            try:
                title = page.title()
            except Exception:
                pass
            browser.close()
            if token and len(token) > 20:
                return str(token)
            raise TimeoutError(
                f"BrowserTurnstile 超时 {self.timeout}s（title={title!r}）。"
                "可改 --browser-turnstile --no-headless 手动点一下，"
                "或改用 --turnstile-token / 打码 key"
            )



def _is_task_expired_error(err: object) -> bool:
    """Cap/Yes task expired or not found — safe to recreateTask once."""
    low = str(err or "").lower()
    return (
        "task_not_found" in low
        or "task data has expired" in low
        or "error_task_not_found" in low
        or ("expired" in low and "task" in low)
    )


class CapSolverSolver:
    """CapSolver Turnstile (createTask / getTaskResult).

    Docs: https://docs.capsolver.com/en/guide/getting-started/
    Task: AntiTurnstileTaskProxyLess
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str | None = None,
        timeout: float = 180.0,
        poll_interval: float = 1.5,
        debug: bool = False,
        proxy: str | None = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ):
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise ValueError("CapSolver api_key required")
        self._endpoint = resolve_capsolver_endpoint(endpoint)
        self._timeout = float(timeout)
        self._poll_interval = max(0.5, float(poll_interval))
        self._debug = debug
        self._proxy = proxy or None
        self._on_progress = on_progress

    def _progress(self, msg: str) -> None:
        if self._debug:
            logger.info("[CapSolver] %s", msg)
        if self._on_progress:
            try:
                self._on_progress(msg)
            except Exception:
                pass

    def _proxies(self) -> dict | None:
        if not self._proxy:
            return None
        return {"http": self._proxy, "https": self._proxy}

    def _post_json(self, path: str, payload: dict, *, timeout: float = 45.0) -> dict:
        url = f"{self._endpoint}{path}"
        resp = cr.post(
            url,
            json=payload,
            timeout=timeout,
            proxies=self._proxies(),
            impersonate="chrome131",
        )
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"CapSolver non-object response: {data!r}")
        return data

    def get_balance(self) -> float:
        data = self._post_json(
            "/getBalance", {"clientKey": self._api_key}, timeout=30.0
        )
        if data.get("errorId", 0) not in (0, None):
            raise RuntimeError(
                f"CapSolver getBalance failed: {data.get('errorCode')}: "
                f"{data.get('errorDescription')}"
            )
        bal = data.get("balance")
        try:
            return float(bal)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"CapSolver getBalance bad balance={bal!r}") from e

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        website_url = (website_url or "").strip()
        website_key = (website_key or "").strip()
        if not website_url or not website_key:
            raise ValueError("website_url and website_key are required for Turnstile")
        task = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        last_err: Exception | None = None
        for attempt in range(2):
            self._progress(
                f"createTask type={task['type']} endpoint={self._endpoint} "
                f"attempt={attempt + 1}/2"
            )
            data = self._post_json(
                "/createTask", {"clientKey": self._api_key, "task": task}
            )
            if data.get("errorId", 0) not in (0, None):
                err = RuntimeError(
                    f"CapSolver createTask failed: {data.get('errorCode')}: "
                    f"{data.get('errorDescription')}"
                )
                mark_captcha_provider_skip("capsolver", err)
                raise err
            task_id = data.get("taskId")
            if not task_id:
                sol = data.get("solution") or {}
                token = sol.get("token") or sol.get("gRecaptchaResponse")
                if token:
                    return str(token)
                raise RuntimeError(f"CapSolver createTask returned no taskId: {data}")
            self._progress(f"task created: {task_id}")
            started = time.time()
            deadline = started + self._timeout
            sleep_s = float(self._poll_interval)
            expired = False
            while time.time() < deadline:
                result = self._post_json(
                    "/getTaskResult",
                    {"clientKey": self._api_key, "taskId": task_id},
                )
                if result.get("errorId", 0) not in (0, None):
                    err = RuntimeError(
                        f"CapSolver getTaskResult error: {result.get('errorCode')}: "
                        f"{result.get('errorDescription')}"
                    )
                    if attempt == 0 and _is_task_expired_error(err):
                        self._progress(
                            f"task expired/not found ({result.get('errorCode')}) "
                            "-> recreateTask once"
                        )
                        last_err = err
                        expired = True
                        break
                    raise err
                status = str(result.get("status") or "")
                if status == "ready":
                    self._progress(f"solved in ~{int(time.time() - started)}s")
                    solution = result.get("solution") or {}
                    token = (
                        solution.get("token")
                        or solution.get("gRecaptchaResponse")
                        or solution.get("cf_clearance")
                    )
                    if not token:
                        raise RuntimeError(f"CapSolver returned no token: {result}")
                    return str(token)
                if status in ("processing", "idle", ""):
                    elapsed = int(time.time() - started)
                    if elapsed % 9 < sleep_s:
                        self._progress(
                            f"still processing ({elapsed}s/{int(self._timeout)}s)..."
                        )
                    time.sleep(sleep_s)
                    sleep_s = min(3.0, sleep_s + 0.5)
                    continue
                raise RuntimeError(
                    f"CapSolver unexpected status: {status} body={result}"
                )
            if expired:
                continue
            raise TimeoutError(
                f"CapSolver task {task_id} did not complete within {self._timeout}s"
            )
        if last_err:
            raise last_err
        raise TimeoutError("CapSolver solve_turnstile failed after recreate")



# ---- process-local soft-skip / preferred cloud solver (zero-balance etc.) ----
# When Yes returns ERROR_ZERO_BALANCE, mark skip so later accounts don't burn 2-5s
# creating doomed tasks. Preferred provider (from balance preflight) reorders chain.
#
# Probe single-flight: until a provider has one success, only one concurrent createTask
# (avoids j>1 stampede on empty wallets). After first success → full parallel.

_CAPTCHA_SKIP_LOCK = threading.Lock()
_CAPTCHA_SKIP_CV = threading.Condition(_CAPTCHA_SKIP_LOCK)
_CAPTCHA_SKIP_UNTIL: dict[str, float] = {}  # provider -> monotonic deadline
_CAPTCHA_PREFERRED: str | None = None
_CAPTCHA_SKIP_TTL_SEC = 600.0  # re-probe empty wallet after 10 min
_CAPTCHA_INFLIGHT: dict[str, int] = {}
_CAPTCHA_HEALTHY: set[str] = set()  # providers that returned a token this process

_ZERO_BALANCE_MARKERS = (
    "ERROR_ZERO_BALANCE",
    "ZERO_BALANCE",
    "ERROR_KEY_DOES_NOT_EXIST",
    "ERROR_WRONG_USER_KEY",
    "ERROR_KEY_DENIED_ACCESS",
    "余额不足",
    "insufficient balance",
    "balance is zero",
    "out of balance",
)


def clear_captcha_provider_skips() -> None:
    """Test/helper: clear soft-skip table, preferred provider, probe gates."""
    global _CAPTCHA_PREFERRED
    with _CAPTCHA_SKIP_CV:
        _CAPTCHA_SKIP_UNTIL.clear()
        _CAPTCHA_PREFERRED = None
        _CAPTCHA_INFLIGHT.clear()
        _CAPTCHA_HEALTHY.clear()
        _CAPTCHA_SKIP_CV.notify_all()


def set_preferred_captcha_provider(name: str | None) -> None:
    """Hint auto chain order (e.g. balance preflight primary=capsolver)."""
    global _CAPTCHA_PREFERRED
    n = (name or "").strip().lower() or None
    if n in {"yes", "yc"}:
        n = "yescaptcha"
    elif n in {"cap", "cs"}:
        n = "capsolver"
    elif n in {"2captcha", "tc", "2c"}:
        n = "twocaptcha"
    with _CAPTCHA_SKIP_CV:
        _CAPTCHA_PREFERRED = n


def get_preferred_captcha_provider() -> str | None:
    with _CAPTCHA_SKIP_CV:
        return _CAPTCHA_PREFERRED


def _is_skipped_unlocked(provider: str) -> bool:
    p = (provider or "").strip().lower()
    until = _CAPTCHA_SKIP_UNTIL.get(p)
    if until is None:
        return False
    if time.monotonic() >= until:
        _CAPTCHA_SKIP_UNTIL.pop(p, None)
        return False
    return True


def is_captcha_provider_skipped(provider: str) -> bool:
    p = (provider or "").strip().lower()
    with _CAPTCHA_SKIP_CV:
        return _is_skipped_unlocked(p)


def mark_captcha_provider_skip(
    provider: str,
    exc: BaseException | str,
    *,
    force: bool = False,
) -> bool:
    """If error is zero-balance / dead-key, soft-skip provider. Returns True if marked.

    force=True: skip regardless of message (e.g. balance preflight said empty).
    """
    msg = str(exc or "")
    upper = msg.upper()
    # Chinese markers not uppercased usefully — also check original
    hit = force or any(m in upper or m in msg for m in _ZERO_BALANCE_MARKERS)
    if not hit:
        # balance preflight style: balance=0.0 < min=0.01
        if "balance=" in msg.lower() and "< min" in msg.lower():
            hit = True
    if not hit:
        return False
    p = (provider or "").strip().lower()
    if not p:
        return False
    global _CAPTCHA_PREFERRED
    with _CAPTCHA_SKIP_CV:
        _CAPTCHA_SKIP_UNTIL[p] = time.monotonic() + float(_CAPTCHA_SKIP_TTL_SEC)
        _CAPTCHA_HEALTHY.discard(p)
        # if preferred just died, clear so chain falls through to next solvent
        if _CAPTCHA_PREFERRED == p:
            _CAPTCHA_PREFERRED = None
        _CAPTCHA_SKIP_CV.notify_all()
    logger.warning(
        "captcha soft-skip provider=%s ttl=%.0fs reason=%s",
        p,
        _CAPTCHA_SKIP_TTL_SEC,
        msg[:160],
    )
    return True


def _enter_provider_probe(provider: str) -> bool:
    """Wait for single-flight gate. Return False if provider soft-skipped."""
    p = (provider or "").strip().lower()
    with _CAPTCHA_SKIP_CV:
        while True:
            if _is_skipped_unlocked(p):
                return False
            # After first success: full parallel. Before: only one inflight probe.
            if p in _CAPTCHA_HEALTHY or _CAPTCHA_INFLIGHT.get(p, 0) == 0:
                _CAPTCHA_INFLIGHT[p] = _CAPTCHA_INFLIGHT.get(p, 0) + 1
                return True
            _CAPTCHA_SKIP_CV.wait(timeout=180.0)


def _leave_provider_probe(provider: str, *, success: bool) -> None:
    p = (provider or "").strip().lower()
    with _CAPTCHA_SKIP_CV:
        _CAPTCHA_INFLIGHT[p] = max(0, _CAPTCHA_INFLIGHT.get(p, 0) - 1)
        if success:
            _CAPTCHA_HEALTHY.add(p)
        _CAPTCHA_SKIP_CV.notify_all()


def _cloud_provider_order() -> list[str]:
    """Yes → Cap → 2C by default; preferred moves to front when set."""
    base = ["yescaptcha", "capsolver", "twocaptcha"]
    pref = get_preferred_captcha_provider()
    if pref and pref in base:
        return [pref] + [p for p in base if p != pref]
    return base


def solve_turnstile_auto(
    *,
    website_url: str,
    website_key: str,
    turnstile_token: str | None = None,
    yescaptcha_key: str | None = None,
    yescaptcha_endpoint: str | None = None,
    yescaptcha_premium: bool = True,
    yescaptcha_proxy: str | None = None,
    capsolver_key: str | None = None,
    capsolver_endpoint: str | None = None,
    capsolver_proxy: str | None = None,
    twocaptcha_key: str | None = None,
    twocaptcha_endpoint: str | None = None,
    twocaptcha_proxy: str | None = None,
    browser: bool = False,
    browser_channel: str = "chrome",
    browser_headless: bool = False,
    browser_proxy: str | None = None,
    local_solver_url: str | None = None,
    manual: bool = False,
    timeout: float = 180.0,
    debug: bool = False,
) -> str:
    """统一入口：token > cloud(Yes/Cap/2C, soft-skip + preferred) > local > browser > manual。"""
    if turnstile_token and turnstile_token.strip():
        return turnstile_token.strip()

    errors: list[str] = []
    cloud_proxy = yescaptcha_proxy or capsolver_proxy or twocaptcha_proxy

    def _try_yes() -> str | None:
        key = resolve_yescaptcha_key(yescaptcha_key)
        if not key:
            return None
        if not _enter_provider_probe("yescaptcha"):
            errors.append("yescaptcha:soft-skipped")
            return None
        try:
            tok = YesCaptchaSolver(
                key,
                endpoint=yescaptcha_endpoint,
                timeout=timeout,
                debug=debug,
                proxy=cloud_proxy,
            ).solve_turnstile(
                website_url,
                website_key,
                premium=yescaptcha_premium,
            )
            _leave_provider_probe("yescaptcha", success=True)
            return tok
        except Exception as exc:
            mark_captcha_provider_skip("yescaptcha", exc)
            _leave_provider_probe("yescaptcha", success=False)
            errors.append(f"yescaptcha:{exc}")
            logger.warning("YesCaptcha 失败: %s", exc)
            return None

    def _try_cap() -> str | None:
        cs_key = resolve_capsolver_key(capsolver_key)
        if not cs_key:
            return None
        if not _enter_provider_probe("capsolver"):
            errors.append("capsolver:soft-skipped")
            return None
        try:
            tok = CapSolverSolver(
                cs_key,
                endpoint=capsolver_endpoint,
                timeout=timeout,
                debug=debug,
                proxy=cloud_proxy,
            ).solve_turnstile(website_url, website_key)
            _leave_provider_probe("capsolver", success=True)
            return tok
        except Exception as exc:
            mark_captcha_provider_skip("capsolver", exc)
            _leave_provider_probe("capsolver", success=False)
            errors.append(f"capsolver:{exc}")
            logger.warning("CapSolver 失败: %s", exc)
            return None

    def _try_2c() -> str | None:
        tc_key = resolve_twocaptcha_key(twocaptcha_key)
        if not tc_key:
            return None
        if not _enter_provider_probe("twocaptcha"):
            errors.append("twocaptcha:soft-skipped")
            return None
        try:
            tok = TwoCaptchaSolver(
                tc_key,
                endpoint=(twocaptcha_endpoint or "https://2captcha.com"),
                timeout=timeout,
                debug=debug,
                proxy=cloud_proxy,
            ).solve_turnstile(website_url, website_key)
            _leave_provider_probe("twocaptcha", success=True)
            return tok
        except Exception as exc:
            mark_captcha_provider_skip("twocaptcha", exc)
            _leave_provider_probe("twocaptcha", success=False)
            errors.append(f"2captcha:{exc}")
            logger.warning("2Captcha 失败: %s", exc)
            return None

    try_map = {
        "yescaptcha": _try_yes,
        "capsolver": _try_cap,
        "twocaptcha": _try_2c,
    }
    for pname in _cloud_provider_order():
        tok = try_map[pname]()
        if tok:
            return tok

    if local_solver_url:
        try:
            return LocalHttpTurnstileSolver(
                local_solver_url, timeout=timeout
            ).solve_turnstile(website_url, website_key)
        except Exception as exc:
            errors.append(f"local:{exc}")
            logger.warning("LocalSolver 失败: %s", exc)

    if browser:
        try:
            return BrowserTurnstileSolver(
                channel=browser_channel,
                headless=browser_headless,
                timeout=timeout,
                proxy=browser_proxy,
                debug=debug,
            ).solve_turnstile(website_url, website_key)
        except Exception as exc:
            errors.append(f"browser:{exc}")
            logger.warning("BrowserTurnstile 失败: %s", exc)

    if manual:
        return ManualTurnstileSolver().solve_turnstile(website_url, website_key)

    hint = (
        "无可用 Turnstile 方案。可选：\n"
        "  set CAPSOLVER_API_KEY=...      # CapSolver\n"
        "  set YESCAPTCHA_API_KEY=...     # YesCaptcha\n"
        "  set TWOCAPTCHA_API_KEY=...     # 2Captcha\n"
        "  --captcha-backend capsolver|yescaptcha|twocaptcha|auto\n"
        "  --browser-turnstile           # 本机 Chrome\n"
        "  --turnstile-token TOKEN       # 手动粘贴\n"
        "  --skip-create                 # 只跑到验码"
    )
    if errors:
        raise RuntimeError(hint + "\n已尝试失败: " + " | ".join(errors[:3]))
    raise RuntimeError(hint)
