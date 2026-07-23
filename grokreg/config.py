"""配置加载。"""
from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
_ENV_LOADED = False


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """从项目根 .env 注入环境变量（默认不覆盖已有 env）。

    支持 KEY=VALUE / export KEY=VALUE；# 注释；引号可选。
    密钥放 .env，勿提交 git（.gitignore 已忽略）。
    """
    global _ENV_LOADED
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if not override and key in os.environ and os.environ.get(key, "") != "":
            continue
        os.environ[key] = val
    _ENV_LOADED = True
    return env_path


def ensure_dotenv() -> None:
    if not _ENV_LOADED:
        load_dotenv()


_DEFAULTS: dict[str, Any] = {
    "proxy": {
        "default": "http://127.0.0.1:7890",
        "pool": [],
        "dynamic": {
            "enabled": False,
            "template": "",
            "region": "US",
            "rotate_sid": True,
            "sid_len": 8,
            "sticky": 5,
            "chain_via": "http://127.0.0.1:7890",
        },
    },
    "browser": {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
        "impersonate": "chrome131",
        "accept_language": "zh-CN,zh;q=0.9",
        "request_timeout": 60,
    },
    "protocol": {
        "accounts_origin": "https://accounts.x.ai",
        "signup_url": "https://accounts.x.ai/sign-up",
        "turnstile_sitekey": "0x4AAAAAAAhr9JGVDZbrZOo0",
        "castle_pk": "pk_p8GGWvD3TmFJZRsX3BQcqAv9aFVispNz",
        "connect_es": "connect-es/2.1.1",
    },
    "yescaptcha": {
        "api_key": "",
        "endpoint": "",
        "premium": True,
        "timeout": 180,
    },
    "capsolver": {
        "api_key": "",
        "endpoint": "",  # empty → https://api.capsolver.com; or CAPSOLVER_ENDPOINT
        "timeout": 180,
    },
    "twocaptcha": {
        "api_key": "",
        "endpoint": "https://2captcha.com",
        "timeout": 180,
    },
    "browser_turnstile": {
        "enabled": False,
        "channel": "chrome",
        "headless": False,
        "timeout": 120,
        "local_solver_url": "",
    },
    "register": {
        "given_name": "Jennifer",
        "family_name": "Mitchell",
    },
    # Captcha plugin: auto | yescaptcha | capsolver | twocaptcha
    # CLI --captcha-backend > env CAPTCHA_BACKEND > config captcha.backend > auto
    "captcha": {
        "backend": "auto",
    },
    # Mail account pool (files of email----pass----cid----rt). Receive backend is separate.
    "mail": {
        "sources": ["mails.txt"],
    },
    "output": {
        "dir": "output",
    },
    # 上传凭证到 CPA（密钥优先 .env）
    "cpa": {
        "base_url": "http://127.0.0.1:8317",
        "secret_key": "",
        "auth_dir": "cpa_export",
    },
    # sub2api admin 远程导入（密钥优先 .env；本地 pack 仍用 --export sub2api）
    "sub2api": {
        "base_url": "",
        "admin_email": "",
        "admin_password": "",
        "export_dir": "sub2api_export",
        "skip_default_group_bind": True,
        "timeout": 30,
    },
    # 自建 CloudMail（maillab/cloud-mail）；密钥优先 .env
    "cloudmail": {
        "url": "",
        "admin_email": "",
        "password": "",
        "domains": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_cpa_env(cfg: dict[str, Any]) -> None:
    """Overlay CPA settings from env onto in-memory cfg only (never writes yaml).

    Precedence per field:
      - env key non-empty  → use env (covers config.yaml for this process)
      - env missing/blank  → keep value from config.yaml / defaults
    Keys: CPA_BASE_URL, CPA_SECRET_KEY, CPA_AUTH_DIR
    """
    cpa = cfg.setdefault("cpa", {})
    if not isinstance(cpa, dict):
        return
    key = (os.environ.get("CPA_SECRET_KEY") or "").strip()
    if key:
        cpa["secret_key"] = key
    base = (os.environ.get("CPA_BASE_URL") or "").strip()
    if base:
        # accept host, http://host:8317, or .../v0/management
        b = base.rstrip("/")
        if "://" not in b:
            b = "https://" + b
        if b.endswith("/v0/management"):
            b = b[: -len("/v0/management")]
        elif b.endswith("/v1"):
            b = b[: -len("/v1")]
        cpa["base_url"] = b
    auth_dir = (os.environ.get("CPA_AUTH_DIR") or "").strip()
    if auth_dir:
        cpa["auth_dir"] = auth_dir



def _apply_cloudmail_env(cfg: dict[str, Any]) -> None:
    """Env overrides for CloudMail (secrets stay in .env)."""
    cm = cfg.setdefault("cloudmail", {})
    if not isinstance(cm, dict):
        return
    url = (os.environ.get("CLOUDMAIL_URL") or "").strip()
    if url:
        cm["url"] = url.rstrip("/")
    admin = (os.environ.get("CLOUDMAIL_ADMIN_EMAIL") or "").strip()
    if admin:
        cm["admin_email"] = admin
    pw = (os.environ.get("CLOUDMAIL_PASSWORD") or "").strip()
    if pw:
        cm["password"] = pw
    domains = (
        (os.environ.get("CLOUDMAIL_DOMAINS") or "").strip()
        or (os.environ.get("GROK_MAIL_DOMAINS") or "").strip()
    )
    if domains:
        cm["domains"] = domains


def normalize_captcha_backend(raw: str | None, *, default: str = "auto") -> str:
    """Normalize captcha backend name; unknown → default (auto).

    Accepted: auto | yescaptcha | capsolver | twocaptcha (+ aliases yes/yc/cap/cs/2captcha/tc).
    """
    s = (raw or "").strip().lower()
    if not s:
        return default
    if s in {"yes", "yc"}:
        return "yescaptcha"
    if s in {"cap", "cs"}:
        return "capsolver"
    if s in {"2captcha", "tc", "2c"}:
        return "twocaptcha"
    if s in {"auto", "yescaptcha", "capsolver", "twocaptcha"}:
        return s
    return default


def _apply_captcha_env(cfg: dict[str, Any]) -> None:
    """Overlay captcha.backend from env (non-empty only; keeps auto as valid value).

    Precedence (applied at load; CLI still wins later):
      CAPTCHA_BACKEND | GROK_CAPTCHA_BACKEND  non-empty → captcha.backend
      blank/missing → keep config.yaml / defaults (auto)
    """
    cap = cfg.setdefault("captcha", {})
    if not isinstance(cap, dict):
        return
    raw = (
        (os.environ.get("CAPTCHA_BACKEND") or "").strip()
        or (os.environ.get("GROK_CAPTCHA_BACKEND") or "").strip()
    )
    if not raw:
        return
    # empty after strip already handled; allow explicit "auto"
    cap["backend"] = normalize_captcha_backend(raw, default="auto")


def _apply_sub2api_env(cfg: dict[str, Any]) -> None:
    """Env overrides for sub2api admin upload (secrets stay in .env)."""
    s2 = cfg.setdefault("sub2api", {})
    if not isinstance(s2, dict):
        return
    base = (os.environ.get("SUB2API_BASE_URL") or "").strip()
    if base:
        b = base.rstrip("/")
        if "://" not in b:
            b = "https://" + b
        for suf in ("/api/v1", "/api"):
            if b.endswith(suf):
                b = b[: -len(suf)].rstrip("/")
        s2["base_url"] = b
    email = (os.environ.get("SUB2API_ADMIN_EMAIL") or "").strip()
    if email:
        s2["admin_email"] = email
    pw = (os.environ.get("SUB2API_ADMIN_PASSWORD") or "").strip()
    if pw:
        s2["admin_password"] = pw
    ex = (os.environ.get("SUB2API_EXPORT_DIR") or "").strip()
    if ex:
        s2["export_dir"] = ex


def _parse_dynamic_template(tmpl: str) -> dict[str, Any]:
    """Normalize template URL and extract region/sid_len/sticky from username.

    Accepts with or without scheme::

        user-region-US-sid-XXXX-t-5:pass@host:2000
        http://user-region-US-sid-XXXX-t-5:pass@host:2000

    Returns keys: template, region?, sticky?, sid_len?
    """
    from urllib.parse import unquote, urlparse

    raw = (tmpl or "").strip()
    if not raw:
        return {}
    if "://" not in raw:
        raw = "http://" + raw
    out: dict[str, Any] = {"template": raw}
    try:
        p = urlparse(raw)
        user = unquote(p.username or "")
    except Exception:
        return out
    if not user:
        return out
    m = re.search(r"region-([A-Za-z0-9]+)", user, re.I)
    if m:
        out["region"] = m.group(1).upper()
    m = re.search(r"sid-([^-]+)", user, re.I)
    if m:
        out["sid_len"] = max(1, len(m.group(1)))
    m = re.search(r"-t-(\d+)", user, re.I)
    if m:
        out["sticky"] = max(0, int(m.group(1)))
    return out


def _apply_proxy_env(cfg: dict[str, Any]) -> None:
    """Overlay proxy settings from env (memory only; never writes yaml).

    Local hop1:
      LOCAL_PROXY       → BOTH proxy.default + proxy.dynamic.chain_via
      PROXY_DEFAULT     → proxy.default only
      PROXY_CHAIN_VIA   → proxy.dynamic.chain_via only

    Dynamic residential — **usually only one key**::

      PROXY_DYNAMIC_TEMPLATE=user-region-US-sid-xxxx-t-5:pass@host:2000
      # http:// optional; region / sid_len / sticky parsed from username
      # presence of template auto-enables dynamic (unless PROXY_DYNAMIC_ENABLED=false)

    Optional overrides (rarely needed)::
      PROXY_DYNAMIC_ENABLED / REGION / ROTATE_SID / SID_LEN / STICKY

    Empty env → config.yaml; blank hop1 yaml → code default 7890.
    """
    px = cfg.setdefault("proxy", {})
    if not isinstance(px, dict):
        return
    dyn = px.setdefault("dynamic", {})
    if not isinstance(dyn, dict):
        dyn = {}
        px["dynamic"] = dyn

    def _norm(url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if "://" not in u:
            u = "http://" + u
        return u.rstrip("/")

    def _as_bool(raw: str) -> bool | None:
        s = (raw or "").strip().lower()
        if not s:
            return None
        if s in {"1", "true", "yes", "on", "y"}:
            return True
        if s in {"0", "false", "no", "off", "n"}:
            return False
        return None

    local = _norm(os.environ.get("LOCAL_PROXY") or "")
    default = _norm(os.environ.get("PROXY_DEFAULT") or "")
    via = _norm(os.environ.get("PROXY_CHAIN_VIA") or "")

    if local:
        px["default"] = local
        dyn["chain_via"] = local
    if default:
        px["default"] = default
    if via:
        dyn["chain_via"] = via

    # --- dynamic: template-first ---
    tmpl_raw = (os.environ.get("PROXY_DYNAMIC_TEMPLATE") or "").strip()
    parsed = _parse_dynamic_template(tmpl_raw) if tmpl_raw else {}
    if parsed.get("template"):
        dyn["template"] = parsed["template"]
        # auto-enable when template is set (explicit false can still disable)
        en = _as_bool(os.environ.get("PROXY_DYNAMIC_ENABLED") or "")
        if en is None:
            dyn["enabled"] = True
        else:
            dyn["enabled"] = en
        # fill from username unless explicit env override later
        if parsed.get("region") and not (os.environ.get("PROXY_DYNAMIC_REGION") or "").strip():
            dyn["region"] = parsed["region"]
        if "sticky" in parsed and not (os.environ.get("PROXY_DYNAMIC_STICKY") or "").strip():
            dyn["sticky"] = parsed["sticky"]
        if "sid_len" in parsed and not (os.environ.get("PROXY_DYNAMIC_SID_LEN") or "").strip():
            dyn["sid_len"] = parsed["sid_len"]
        # default rotate when template present
        if _as_bool(os.environ.get("PROXY_DYNAMIC_ROTATE_SID") or "") is None:
            dyn.setdefault("rotate_sid", True)
    else:
        en = _as_bool(os.environ.get("PROXY_DYNAMIC_ENABLED") or "")
        if en is not None:
            dyn["enabled"] = en

    region = (os.environ.get("PROXY_DYNAMIC_REGION") or "").strip()
    if region:
        dyn["region"] = region

    rot = _as_bool(os.environ.get("PROXY_DYNAMIC_ROTATE_SID") or "")
    if rot is not None:
        dyn["rotate_sid"] = rot

    sid_raw = (os.environ.get("PROXY_DYNAMIC_SID_LEN") or "").strip()
    if sid_raw.isdigit():
        dyn["sid_len"] = max(1, int(sid_raw))

    sticky_raw = (os.environ.get("PROXY_DYNAMIC_STICKY") or "").strip()
    if sticky_raw.isdigit():
        dyn["sticky"] = max(0, int(sticky_raw))

    code_default = "http://127.0.0.1:7890"
    if not str(px.get("default") or "").strip():
        px["default"] = code_default
    if not str(dyn.get("chain_via") or "").strip():
        dyn["chain_via"] = code_default


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    ensure_dotenv()
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"配置必须是 mapping: {cfg_path}")
            data = loaded
    cfg = _deep_merge(_DEFAULTS, data)
    _apply_proxy_env(cfg)
    _apply_cpa_env(cfg)
    _apply_cloudmail_env(cfg)
    _apply_sub2api_env(cfg)
    _apply_captcha_env(cfg)
    cfg["_root"] = str(ROOT)
    cfg["_config_path"] = str(cfg_path)
    return cfg
