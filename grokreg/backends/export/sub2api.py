"""Sub2API local JSON export — auth.json entry → UI import-shaped file.

UI 数据导入 (accounts.importData) 认的是 export 整包，不是裸账号：

  {
    "exported_at": "ISO-UTC",
    "proxies": [],
    "accounts": [ { name, platform, type, credentials, extra, ... } ]
  }

证据：
  - 用户样例 sub2api-account-*.json 顶层 keys = exported_at/proxies/accounts
  - 前端 exportData → 下载该形态；importData POST /admin/accounts/data {data: parsed}

裸账号对象会报「不是受支持的导出数据文件」。

Remote admin import is ops via --sub2api-upload
(POST /api/v1/admin/accounts/data; ≠ --cpa-upload / CLIProxy).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ...oauth import BASE_URL, CLIENT_ID, SCOPES
from ...errors import ConfigError

DEFAULT_OUT_DIR = "sub2api_export"

# From real sub2api account export sample (2026-07); identity map is fine for free Build.
DEFAULT_MODEL_MAPPING: dict[str, str] = {
    "composer-2.5": "composer-2.5",
    "grok": "grok",
    "grok-4.20-0309-non-reasoning": "grok-4.20-0309-non-reasoning",
    "grok-4.20-0309-reasoning": "grok-4.20-0309-reasoning",
    "grok-4.20-multi-agent-0309": "grok-4.20-multi-agent-0309",
    "grok-4.20-non-reasoning": "grok-4.20-non-reasoning",
    "grok-4.20-reasoning": "grok-4.20-reasoning",
    "grok-4.3": "grok-4.3",
    "grok-4.5": "grok-4.5",
    "grok-4.5-latest": "grok-4.5-latest",
    "grok-build": "grok-build",
    "grok-build-0.1": "grok-build-0.1",
    "grok-build-latest": "grok-build-latest",
    "grok-composer": "grok-composer",
    "grok-composer-2.5-fast": "grok-composer-2.5-fast",
    "grok-imagine": "grok-imagine",
    "grok-imagine-edit": "grok-imagine-edit",
    "grok-imagine-image": "grok-imagine-image",
    "grok-imagine-image-quality": "grok-imagine-image-quality",
    "grok-imagine-video": "grok-imagine-video",
    "grok-imagine-video-1.5": "grok-imagine-video-1.5",
    "grok-latest": "grok-latest",
}


def sanitize_email_for_filename(email: str) -> str:
    """Keep A-Za-z0-9@._- ; other chars → - (same idea as CPA writer)."""
    s = (email or "").strip()
    if not s:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9@._-]+", "-", s)


def account_filename(email: str) -> str:
    return f"grok-{sanitize_email_for_filename(email)}.json"


def _expires_at_unix(entry: dict[str, Any]) -> int | None:
    """Unix seconds for credentials.expires_at (sub2api sample uses int)."""
    from ...token_clock import parse_entry_exp

    exp = parse_entry_exp(entry)
    if exp and exp > 0:
        return int(exp)
    return None


def entry_to_sub2api_account(
    entry: dict[str, Any],
    *,
    name: str | None = None,
    include_model_mapping: bool = True,
    model_mapping: dict[str, str] | None = None,
    concurrency: int = 1,
    priority: int = 1,
    rate_multiplier: float | int = 1,
    auto_pause_on_expired: bool = True,
) -> dict[str, Any]:
    """Map one auth.json entry → sub2api account object (accounts[] element)."""
    if not isinstance(entry, dict):
        raise ConfigError("sub2api export entry must be dict", code="export_sub2api")

    email = (entry.get("email") or "").strip()
    at = (entry.get("access_token") or entry.get("key") or "").strip()
    rt = (entry.get("refresh_token") or "").strip()
    if not email:
        raise ConfigError("sub2api export: missing email", code="export_sub2api")
    if not rt and not at:
        raise ConfigError(
            f"sub2api export: {email} has neither access_token nor refresh_token",
            code="export_sub2api",
        )

    exp = _expires_at_unix(entry)
    creds: dict[str, Any] = {
        "access_token": at,
        "base_url": (entry.get("base_url") or BASE_URL).strip() or BASE_URL,
        "client_id": CLIENT_ID,
        "email": email,
        "id_token": (entry.get("id_token") or "").strip(),
        "refresh_token": rt,
        "scope": SCOPES,
        "token_type": (entry.get("token_type") or "Bearer").strip() or "Bearer",
    }
    if exp is not None:
        creds["expires_at"] = exp
    if include_model_mapping:
        creds["model_mapping"] = dict(model_mapping or DEFAULT_MODEL_MAPPING)

    return {
        "name": (name or email).strip() or email,
        "platform": "grok",
        "type": "oauth",
        "credentials": creds,
        "extra": {"email": email},
        "concurrency": int(concurrency),
        "priority": int(priority),
        "rate_multiplier": rate_multiplier,
        "auto_pause_on_expired": bool(auto_pause_on_expired),
    }


def wrap_export_document(
    accounts: list[dict[str, Any]],
    *,
    proxies: list[dict[str, Any]] | None = None,
    exported_at: str | None = None,
) -> dict[str, Any]:
    """sub2api admin export/import envelope (required for UI importData)."""
    return {
        "exported_at": exported_at
        or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proxies": list(proxies or []),
        "accounts": list(accounts or []),
    }


def write_account_json(out_dir: Path | str, account: dict[str, Any]) -> Path:
    """Atomically write one importable export doc (accounts len=1) to grok-<email>.json."""
    out_dir = Path(out_dir)
    email = (
        (account.get("credentials") or {}).get("email")
        or account.get("name")
        or "unknown"
    )
    doc = wrap_export_document([account])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / account_filename(str(email))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


class Sub2ApiExport:
    """Write sub2api-shaped account JSON under out_dir (one file per account)."""

    name = "sub2api"
    kind = "pack"
    source = "auth_pool"  # reads auth.json entries; does not mint

    def __init__(
        self,
        out_dir: str | Path = DEFAULT_OUT_DIR,
        *,
        include_model_mapping: bool = True,
        model_mapping: dict[str, str] | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.include_model_mapping = include_model_mapping
        self.model_mapping = model_mapping

    def preview_path(self, email: str) -> Path:
        return self.out_dir / account_filename(email)

    def export_entry(
        self,
        entry: dict[str, Any],
        *,
        filename: str | None = None,
        name: str | None = None,
    ) -> Path:
        """Preferred: auth-pool entry → UI import envelope file."""
        account = entry_to_sub2api_account(
            entry,
            name=name,
            include_model_mapping=self.include_model_mapping,
            model_mapping=self.model_mapping,
        )
        if filename:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            fname = filename if filename.endswith(".json") else f"{filename}.json"
            path = self.out_dir / fname
            doc = wrap_export_document([account])
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(path)
            return path
        return write_account_json(self.out_dir, account)

    def save_cpa(
        self,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        """Alias for export_entry (payload treated as auth-pool entry)."""
        return self.export_entry(payload, filename=filename)

    def upsert_cpa(self, cpa_path: Path | str) -> str:
        raise ConfigError(
            "sub2api backend does not upsert auth.json; use auth_pool",
            code="export_backend",
        )


def export_auth_pool_to_sub2api(
    auth_path: Path | str,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    *,
    only: str | None = None,
    include_disabled: bool = False,
    require_refresh_token: bool = True,
    dry_run: bool = False,
    include_model_mapping: bool = True,
) -> dict[str, Any]:
    """Export auth.json → sub2api_export/grok-*.json (thin wrapper on export_auth_pool)."""
    from .factory import export_auth_pool

    return export_auth_pool(
        "sub2api",
        auth_path,
        out_dir=out_dir,
        only=only,
        include_disabled=include_disabled,
        require_refresh_token=require_refresh_token,
        dry_run=dry_run,
        include_model_mapping=include_model_mapping,
    )
