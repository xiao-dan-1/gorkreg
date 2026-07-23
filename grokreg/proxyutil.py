"""动态代理（辣椒 lajiao 等）与本地链式隧道。

迁移自 GPT协议注册机/gptreg/proxyutil.py，接口保持一致：
  resolve_proxy(cfg) -> ResolvedProxy(session_url=本地口或上游, upstream_url=真实代理)

格式示例:
  qqfow23217-region-US-sid-2As2LXe5-t-5:dve1rnfm@us.lajiaohttp.net:2000

- 改 region-XX 换地区
- 改 sid-XXXX 换 IP（同 sid 粘性约 t-N 分钟）
- 直连常 403 时，经 chain_via（实测 127.0.0.1:7890）做 CONNECT 隧道
"""
from __future__ import annotations

import base64
import logging
import random
import re
import socket
import string
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SID_RE = re.compile(r"-sid-[^-@]+-t-")
_REGION_RE = re.compile(r"-region-[A-Za-z0-9]+-")
_SID_CHARS = string.ascii_letters + string.digits


def random_sid(length: int = 8) -> str:
    return "".join(random.choice(_SID_CHARS) for _ in range(max(4, int(length or 8))))


def ensure_http_proxy_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def proxy_label(url: str) -> str:
    """脱敏展示，保留 region/sid/host；本地口不画假账号。"""
    if not url:
        return "直连"
    try:
        p = urlparse(ensure_http_proxy_url(url))
        user = p.username or ""
        host = p.hostname or ""
        port = p.port or ""
        # local hop1 (Clash/v2rayN) — no credentials to mask
        if host in {"127.0.0.1", "localhost", "::1"} and not user:
            return f"{host}:{port}" if port else host
        region = ""
        sid = ""
        m = re.search(r"region-([A-Za-z0-9]+)", user)
        if m:
            region = m.group(1)
        m = re.search(r"sid-([^-]+)", user)
        if m:
            sid = m.group(1)
        if region or sid:
            bits = []
            if region:
                bits.append(f"region-{region}")
            if sid:
                bits.append(f"sid-{sid}")
            return f"{'+'.join(bits)}@{host}:{port}"
        if user:
            return f"***@{host}:{port}"
        return f"{host}:{port}" if port else (host or "已配置")
    except Exception:
        return "已配置"


def set_region(proxy_url: str, region: str) -> str:
    region = (region or "").strip().upper()
    if not proxy_url or not region:
        return proxy_url
    url = ensure_http_proxy_url(proxy_url)
    if _REGION_RE.search(url):
        return _REGION_RE.sub(f"-region-{region}-", url, count=1)
    try:
        p = urlparse(url)
        user = p.username or ""
        if "region-" not in user and user:
            if "-sid-" in user:
                user = user.replace("-sid-", f"-region-{region}-sid-", 1)
            else:
                user = f"{user}-region-{region}"
            pwd = p.password or ""
            auth = f"{user}:{pwd}@" if pwd else f"{user}@"
            return f"{p.scheme}://{auth}{p.hostname}:{p.port or 2000}"
    except Exception:
        pass
    return url


def set_sid(proxy_url: str, sid: str | None = None, sid_len: int = 8) -> str:
    url = ensure_http_proxy_url(proxy_url)
    if not url:
        return url
    new_sid = sid or random_sid(sid_len)
    if _SID_RE.search(url):
        return _SID_RE.sub(f"-sid-{new_sid}-t-", url, count=1)
    try:
        p = urlparse(url)
        user = p.username or ""
        if user and "-sid-" not in user:
            user = f"{user}-sid-{new_sid}-t-5"
            pwd = p.password or ""
            auth = f"{user}:{pwd}@" if pwd else f"{user}@"
            return f"{p.scheme}://{auth}{p.hostname}:{p.port or 2000}"
    except Exception:
        pass
    return url


def parse_proxy_auth(proxy_url: str) -> dict[str, Any]:
    url = ensure_http_proxy_url(proxy_url)
    p = urlparse(url)
    if not p.hostname:
        raise ValueError(f"无效代理 URL: {proxy_url}")
    user = p.username or ""
    password = p.password or ""
    return {
        "scheme": p.scheme or "http",
        "host": p.hostname,
        "port": int(p.port or 2000),
        "username": user,
        "password": password,
        "auth_header": base64.b64encode(f"{user}:{password}".encode()).decode() if user else "",
        "url": url,
    }


def build_dynamic_proxy(
    cfg: dict[str, Any], *, region: str | None = None, sid: str | None = None
) -> str:
    """根据 config.proxy.dynamic 生成一条完整代理 URL（含随机 sid）。"""
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if not dyn.get("enabled"):
        return ""

    template = (dyn.get("template") or "").strip()
    if template:
        url = ensure_http_proxy_url(template)
    else:
        user = (dyn.get("user") or "").strip()
        password = (dyn.get("password") or "").strip()
        host = (dyn.get("host") or "us.lajiaohttp.net").strip()
        port = int(dyn.get("port") or 2000)
        reg = (region or dyn.get("region") or "US").strip().upper()
        sticky = int(dyn.get("sticky") or 5)
        if user and "region-" not in user and "sid-" not in user:
            user = f"{user}-region-{reg}-sid-PLACEHOLDER-t-{sticky}"
        elif user and "sid-" not in user:
            user = f"{user}-sid-PLACEHOLDER-t-{sticky}"
        url = f"http://{user}:{password}@{host}:{port}"

    reg = (region or dyn.get("region") or "").strip()
    if reg:
        url = set_region(url, reg)

    if dyn.get("rotate_sid", True) or sid or "{sid}" in (template or ""):
        if "{sid}" in url:
            url = url.replace("{sid}", sid or random_sid(int(dyn.get("sid_len") or 8)))
        if "{region}" in url:
            url = url.replace("{region}", (reg or dyn.get("region") or "US").upper())
        if "{sticky}" in url:
            url = url.replace("{sticky}", str(int(dyn.get("sticky") or 5)))
        url = set_sid(url, sid=sid, sid_len=int(dyn.get("sid_len") or 8))
    return url


def pick_proxy(cfg: dict[str, Any], override: str | None = None) -> str:
    """选择代理字符串（可能是动态 lajiao URL，尚未套链式本地口）。

    override:
      - None: 配置逻辑
      - "": 直连
      - 其他: 指定
    """
    if override is not None:
        return override

    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if dyn.get("enabled"):
        return build_dynamic_proxy(cfg)

    pool = cfg.get("proxy", {}).get("pool") or []
    pool = [ensure_http_proxy_url(p) for p in pool if isinstance(p, str) and p.strip()]
    if pool:
        chosen = random.choice(pool)
        if dyn.get("rotate_sid", True) and _SID_RE.search(chosen):
            return set_sid(chosen, sid_len=int(dyn.get("sid_len") or 8))
        return chosen
    return str(cfg.get("proxy", {}).get("default") or "")


def needs_chain(cfg: dict[str, Any], proxy_url: str) -> bool:
    if not proxy_url:
        return False
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    chain_via = (dyn.get("chain_via") or "").strip()
    if not chain_via:
        return False
    try:
        host = urlparse(ensure_http_proxy_url(proxy_url)).hostname or ""
        if host in {"127.0.0.1", "localhost"}:
            return False
    except Exception:
        pass
    if dyn.get("enabled"):
        return True
    return "lajiaohttp" in proxy_url or bool(dyn.get("force_chain"))


@dataclass
class ResolvedProxy:
    """一次注册实际使用的代理。"""

    session_url: str
    upstream_url: str
    chain: "StickyChainTunnel | None" = None
    region: str = ""
    sid: str = ""

    def label(self) -> str:
        base = proxy_label(self.upstream_url or self.session_url)
        if self.chain:
            return f"{base} via-chain"
        return base

    def close(self) -> None:
        if self.chain is not None:
            self.chain.close()
            self.chain = None


def _extract_region_sid(url: str) -> tuple[str, str]:
    user = urlparse(ensure_http_proxy_url(url)).username or ""
    region = ""
    sid = ""
    m = re.search(r"region-([A-Za-z0-9]+)", user)
    if m:
        region = m.group(1)
    m = re.search(r"sid-([^-]+)", user)
    if m:
        sid = m.group(1)
    return region, sid


def resolve_proxy(cfg: dict[str, Any], override: str | None = None) -> ResolvedProxy:
    """生成本次注册代理；需要时启动粘性链式隧道。"""
    upstream = pick_proxy(cfg, override)
    if not upstream:
        return ResolvedProxy(session_url="", upstream_url="", region="", sid="")

    region, sid = _extract_region_sid(upstream)
    if needs_chain(cfg, upstream):
        dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
        hop1 = ensure_http_proxy_url(dyn.get("chain_via") or "http://127.0.0.1:7890")
        tunnel = StickyChainTunnel(hop1=hop1, hop2=upstream)
        tunnel.start()
        return ResolvedProxy(
            session_url=tunnel.local_url,
            upstream_url=upstream,
            chain=tunnel,
            region=region,
            sid=sid,
        )
    return ResolvedProxy(session_url=upstream, upstream_url=upstream, region=region, sid=sid)


def _read_until_headers(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    return data


def _parse_hop(url: str) -> tuple[str, int, str]:
    info = parse_proxy_auth(url)
    return info["host"], info["port"], info["auth_header"]


class StickyChainTunnel:
    """本地 HTTP 代理：client → hop1(7890) → CONNECT hop2(lajiao) → 目标。

    同一 tunnel 实例固定 hop2（含 sid），保证单次注册出口 IP 粘性。
    """

    def __init__(self, hop1: str, hop2: str, bind_host: str = "127.0.0.1"):
        self.hop1_url = ensure_http_proxy_url(hop1)
        self.hop2_url = ensure_http_proxy_url(hop2)
        self.bind_host = bind_host
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = 0
        self.local_url = ""

    def start(self) -> str:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_host, 0))
        srv.listen(64)
        srv.settimeout(1.0)
        self._sock = srv
        self.port = srv.getsockname()[1]
        self.local_url = f"http://{self.bind_host}:{self.port}"
        self._thread = threading.Thread(
            target=self._serve,
            name=f"chain-{self.port}",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            "[Proxy] 链式隧道 %s → %s → %s",
            self.local_url,
            proxy_label(self.hop1_url),
            proxy_label(self.hop2_url),
        )
        return self.local_url

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(client,), daemon=True)
            t.start()

    def _handle(self, client: socket.socket) -> None:
        hop1_sock: socket.socket | None = None
        try:
            client.settimeout(120)
            hop1_host, hop1_port, hop1_auth = _parse_hop(self.hop1_url)
            hop2_host, hop2_port, hop2_auth = _parse_hop(self.hop2_url)
            _pa = "Proxy-" + "Authorization"
            _basic = "Bas" + "ic "

            first = _read_until_headers(client)
            if not first:
                return
            first_line = first.split(b"\r\n", 1)[0].decode(errors="replace")
            parts = first_line.split()
            if len(parts) < 2:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            method = parts[0].upper()
            target = parts[1]

            hop1_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            hop1_sock.settimeout(120)
            hop1_sock.connect((hop1_host, hop1_port))

            # 1) 经 hop1 CONNECT 到 hop2 代理主机
            connect_hop2 = (
                "CONNECT " + hop2_host + ":" + str(hop2_port) + " HTTP/1.1\r\n"
                + "Host: " + hop2_host + ":" + str(hop2_port) + "\r\n"
            )
            if hop1_auth:
                connect_hop2 += _pa + ": " + _basic + hop1_auth + "\r\n"
            connect_hop2 += "\r\n"
            hop1_sock.sendall(connect_hop2.encode())
            hop1_resp = _read_until_headers(hop1_sock)
            hop1_status = hop1_resp.split(b"\r\n", 1)[0]
            if b" 200 " not in hop1_status:
                logger.warning(
                    "[Proxy] hop1 CONNECT hop2 失败: %s",
                    hop1_status.decode(errors="replace"),
                )
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return

            if method == "CONNECT":
                # 2) 对 hop2 再 CONNECT 到真实目标，注入 hop2 认证
                connect_target = "CONNECT " + target + " HTTP/1.1\r\n" + "Host: " + target + "\r\n"
                if hop2_auth:
                    connect_target += _pa + ": " + _basic + hop2_auth + "\r\n"
                connect_target += "\r\n"
                hop1_sock.sendall(connect_target.encode())
                hop2_resp = _read_until_headers(hop1_sock)
                hop2_status = hop2_resp.split(b"\r\n", 1)[0]
                if b" 200 " not in hop2_status:
                    logger.warning(
                        "[Proxy] hop2 CONNECT %s 失败: %s",
                        target,
                        hop2_status.decode(errors="replace"),
                    )
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                    return
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                marker = (_pa + ":").encode()
                if hop2_auth and marker not in first:
                    idx = first.find(b"\r\n")
                    if idx != -1:
                        auth_line = (_pa + ": " + _basic + hop2_auth + "\r\n").encode()
                        first = first[: idx + 2] + auth_line + first[idx + 2 :]
                hop1_sock.sendall(first)

            done = threading.Event()

            def relay(src: socket.socket, dst: socket.socket) -> None:
                try:
                    while not done.is_set():
                        try:
                            data = src.recv(65536)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        dst.sendall(data)
                except OSError:
                    pass
                finally:
                    done.set()
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

            t1 = threading.Thread(target=relay, args=(client, hop1_sock), daemon=True)
            t2 = threading.Thread(target=relay, args=(hop1_sock, client), daemon=True)
            t1.start()
            t2.start()
            done.wait(timeout=300)
        except Exception as exc:
            logger.debug("[Proxy] 隧道连接异常: %s", exc)
        finally:
            for s in (client, hop1_sock):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass


def probe_proxy(
    session_url: str,
    timeout: int = 20,
    impersonate: str = "chrome131",
    *,
    geo: bool = True,
) -> dict[str, Any]:
    """探测出口 IP。

    geo=True 时额外请求 ipinfo（慢、仅 enrichment；preflight 默认 False）。
    返回含 ms / ipify_ms / ipinfo_ms。
    """
    import time as _time
    from curl_cffi.requests import Session

    t0 = _time.perf_counter()
    proxies = None if not session_url else {"http": session_url, "https": session_url}
    s = Session(impersonate=impersonate, verify=False)
    if proxies:
        s.proxies = proxies

    t_ip = _time.perf_counter()
    r = s.get("https://api.ipify.org?format=json", timeout=timeout)
    ipify_ms = round((_time.perf_counter() - t_ip) * 1000, 1)
    ip = ""
    try:
        ip = (r.json() or {}).get("ip") or ""
    except Exception:
        ip = (r.text or "")[:80]
    info: dict[str, Any] = {
        "status": r.status_code,
        "ip": ip,
        "body": (r.text or "")[:200],
        "ipify_ms": ipify_ms,
        "ipinfo_ms": 0.0,
    }
    if geo:
        t_g = _time.perf_counter()
        try:
            r2 = s.get("https://ipinfo.io/json", timeout=timeout)
            info["ipinfo_ms"] = round((_time.perf_counter() - t_g) * 1000, 1)
            if r2.status_code == 200:
                info["ipinfo"] = r2.json()
        except Exception:
            info["ipinfo_ms"] = round((_time.perf_counter() - t_g) * 1000, 1)
    info["ms"] = round((_time.perf_counter() - t0) * 1000, 1)
    return info


def _tcp_connect_ok(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "ok"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def hop1_nested_connect_ok(
    hop1_url: str,
    hop2_url: str,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Probe nested CONNECT: client → hop1 → CONNECT hop2 → CONNECT example.com:443.

    Classic failure: v2rayN:10808 as chain_via — hop1 CONNECT hop2 returns 200 then
    second-layer tunnel stalls. Clash:7890 usually passes.
    """
    hop1 = ensure_http_proxy_url(hop1_url)
    hop2 = ensure_http_proxy_url(hop2_url)
    out: dict[str, Any] = {
        "ok": False,
        "hop1": proxy_label(hop1),
        "hop2": proxy_label(hop2),
        "hop1_listen": False,
        "hop1_connect_hop2": False,
        "hop2_connect_target": False,
        "error": "",
        "ms": 0.0,
    }
    if not hop1 or not hop2:
        out["error"] = "empty hop1 or hop2"
        return out

    import time as _time

    t0 = _time.perf_counter()
    sock: socket.socket | None = None
    try:
        h1_host, h1_port, h1_auth = _parse_hop(hop1)
        h2_host, h2_port, h2_auth = _parse_hop(hop2)

        listen_ok, listen_err = _tcp_connect_ok(h1_host, h1_port, timeout=min(3.0, timeout))
        out["hop1_listen"] = listen_ok
        if not listen_ok:
            out["error"] = f"hop1 not listening {h1_host}:{h1_port} ({listen_err})"
            return out

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((h1_host, h1_port))

        req1 = f"CONNECT {h2_host}:{h2_port} HTTP/1.1\r\nHost: {h2_host}:{h2_port}\r\n"
        if h1_auth:
            req1 += "Proxy-Authorization: Basic " + h1_auth + "\r\n"


        req1 += "\r\n"
        sock.sendall(req1.encode())
        resp1 = _read_until_headers(sock)
        line1 = resp1.split(b"\r\n", 1)[0].decode(errors="replace")
        out["hop1_status"] = line1
        if b" 200 " not in resp1.split(b"\r\n", 1)[0]:
            out["error"] = f"hop1 CONNECT hop2 failed: {line1}"
            return out
        out["hop1_connect_hop2"] = True

        target = "example.com:443"
        req2 = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
        if h2_auth:
            req2 += "Proxy-Authorization: Basic " + h2_auth + "\r\n"


        req2 += "\r\n"
        sock.sendall(req2.encode())
        resp2 = _read_until_headers(sock)
        line2 = (resp2.split(b"\r\n", 1)[0] if resp2 else b"").decode(errors="replace")
        out["hop2_status"] = line2 or "(empty)"
        if not resp2 or b" 200 " not in resp2.split(b"\r\n", 1)[0]:
            out["error"] = (
                "hop2 CONNECT target failed/empty via hop1 "
                f"(classic 10808 nest blackhole): {line2 or 'empty'}"
            )
            return out
        out["hop2_connect_target"] = True
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        out["ms"] = round((_time.perf_counter() - t0) * 1000, 1)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def warn_bad_chain_via(hop1_url: str) -> str | None:
    """Heuristic: 10808 is often v2rayN and fails nested CONNECT for lajiao."""
    try:
        u = urlparse(ensure_http_proxy_url(hop1_url))
        if u.port == 10808:
            return (
                "chain_via port 10808 is typically v2rayN; nested CONNECT to lajiao "
                "often blackholes. Prefer Clash 7890 as hop1."
            )
    except Exception:
        pass
    return None


def preflight_register_proxy(
    cfg: dict[str, Any],
    override: str | None = None,
    *,
    full_probe: bool = True,
    timeout: float = 12.0,
    impersonate: str = "chrome131",
    geo: bool = False,
) -> dict[str, Any]:
    """Pre-batch health check for register path.

    When chain is required: verify hop1 nested CONNECT + optional exit probe.
    geo=False by default (skip ipinfo) — pass/fail only needs ipify.
    """
    import time as _time

    t0 = _time.perf_counter()
    result: dict[str, Any] = {
        "ok": False,
        "needed_chain": False,
        "skipped": False,
        "hop1": "",
        "upstream": "",
        "nested": None,
        "probe": None,
        "warning": None,
        "error": "",
        "total_ms": 0.0,
        "nested_ms": 0.0,
        "probe_ms": 0.0,
    }

    def _finish() -> dict[str, Any]:
        result["total_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
        return result

    upstream = pick_proxy(cfg, override)
    result["upstream"] = proxy_label(upstream) if upstream else ""
    if not upstream:
        result["skipped"] = True
        result["ok"] = True
        result["error"] = "no proxy configured (direct)"
        return _finish()

    if not needs_chain(cfg, upstream):
        result["needed_chain"] = False
        if not full_probe:
            result["ok"] = True
            result["skipped"] = True
            return _finish()
        try:
            info = probe_proxy(
                upstream,
                timeout=int(timeout),
                impersonate=impersonate,
                geo=geo,
            )
            result["probe"] = {
                "status": info.get("status"),
                "ip": info.get("ip") or "",
                "ms": info.get("ms"),
                "ipify_ms": info.get("ipify_ms"),
            }
            result["probe_ms"] = float(info.get("ms") or 0)
            result["ok"] = info.get("status") == 200 and bool(info.get("ip"))
            if not result["ok"]:
                result["error"] = f"probe failed status={info.get('status')} ip={info.get('ip')}"
        except Exception as e:
            result["error"] = f"probe exception: {type(e).__name__}: {e}"
        return _finish()

    result["needed_chain"] = True
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    hop1 = ensure_http_proxy_url(dyn.get("chain_via") or "http://127.0.0.1:7890")
    result["hop1"] = proxy_label(hop1)
    warn = warn_bad_chain_via(hop1)
    if warn:
        result["warning"] = warn
        # CLI formats a single warn line — no logger here

    nested = hop1_nested_connect_ok(hop1, upstream, timeout=timeout)
    result["nested"] = nested
    result["nested_ms"] = float(nested.get("ms") or 0)
    if nested.get("hop1"):
        result["hop1"] = nested["hop1"]
    if nested.get("hop2"):
        result["upstream"] = nested["hop2"]
    if not nested.get("ok"):
        result["error"] = nested.get("error") or "nested CONNECT failed"
        return _finish()

    if not full_probe:
        result["ok"] = True
        return _finish()

    resolved = resolve_proxy(cfg, override)
    try:
        info = probe_proxy(
            resolved.session_url,
            timeout=int(max(8.0, timeout)),
            impersonate=impersonate,
            geo=geo,
        )
        result["probe"] = {
            "status": info.get("status"),
            "ip": info.get("ip") or "",
            "ms": info.get("ms"),
            "ipify_ms": info.get("ipify_ms"),
            "session": resolved.session_url,
        }
        result["probe_ms"] = float(info.get("ms") or 0)
        result["ok"] = info.get("status") == 200 and bool(info.get("ip"))
        if not result["ok"]:
            result["error"] = (
                f"full chain probe failed status={info.get('status')} ip={info.get('ip') or '?'}"
            )
    except Exception as e:
        result["error"] = f"full chain probe exception: {type(e).__name__}: {e}"
    finally:
        resolved.close()
    return _finish()
