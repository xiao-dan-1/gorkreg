"""Upload local sub2api export packs to remote admin importData API.

Official UI path (frontend accounts.importData):
  POST /api/v1/admin/accounts/data
  { "data": <export envelope>, "skip_default_group_bind": true|false }

Envelope (same as local --export sub2api):
  { "exported_at", "proxies", "accounts": [...] }

Ops only — not an export Protocol backend (see docs/export-plugin-contract.md).

on_exists: create | skip | overwrite
Match key is always (platform, email) — uploading grok never touches openai/gpt.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from ..errors import ConfigError, GrokRegError

log = logging.getLogger(__name__)

DEFAULT_EXPORT_DIR = "sub2api_export"


class Sub2ApiError(GrokRegError):
    code = "sub2api"
    retryable = False


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only; no secrets logged)
# ---------------------------------------------------------------------------


def normalize_base_url(url: str) -> str:
    b = (url or "").strip().rstrip("/")
    if not b:
        raise ConfigError("sub2api.base_url 为空", code="sub2api_config")
    if "://" not in b:
        b = "https://" + b
    # strip accidental /api/v1 suffix — we join paths ourselves
    for suf in ("/api/v1", "/api"):
        if b.endswith(suf):
            b = b[: -len(suf)].rstrip("/")
    return b


def _api_url(base: str, path: str) -> str:
    return f"{normalize_base_url(base)}/{path.lstrip('/')}"


def _unwrap(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "code" in payload:
        code = payload.get("code")
        if code not in (0, "0", None):
            msg = (
                payload.get("message")
                or payload.get("msg")
                or payload.get("detail")
                or payload.get("error")
                or json.dumps(payload, ensure_ascii=False)[:200]
            )
            raise Sub2ApiError(f"API code={code}: {msg}", code="sub2api_api")
        if "data" in payload:
            return payload["data"]
    if payload.get("success") is False:
        raise Sub2ApiError(str(payload.get("message") or payload), code="sub2api_api")
    if payload.get("success") is True and "data" in payload:
        return payload["data"]
    return payload


def _extract_token(payload: Any) -> str:
    data = _unwrap(payload)
    if not isinstance(data, dict):
        raise Sub2ApiError("login response did not contain a token", code="sub2api_auth")
    for key in ("access_token", "token", "jwt"):
        if data.get(key):
            return str(data[key])
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("access_token", "token", "jwt"):
            if nested.get(key):
                return str(nested[key])
    raise Sub2ApiError("login response did not contain a token", code="sub2api_auth")


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "grokreg-sub2api-upload/0.1",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urlrequest.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urlerror.HTTPError as e:
        raw_err = e.read().decode("utf-8", errors="replace")
        try:
            err_body = json.loads(raw_err)
            msg = (
                err_body.get("message")
                or err_body.get("msg")
                or err_body.get("detail")
                or raw_err
            )
        except json.JSONDecodeError:
            msg = raw_err or str(e)
        raise Sub2ApiError(f"HTTP {e.code}: {msg}", code="sub2api_http") from e
    except urlerror.URLError as e:
        raise Sub2ApiError(f"network error: {e.reason}", code="sub2api_net") from e
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise Sub2ApiError(
            f"non-JSON response: {raw[:200]!r}", code="sub2api_http"
        ) from e


class Sub2ApiAdminClient:
    """Minimal admin client: login + data import/export."""

    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self.timeout = float(timeout)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return _unwrap(
            request_json(
                method,
                _api_url(self.base_url, path),
                body=body,
                token=self.token,
                timeout=self.timeout,
            )
        )

    def login(self, email: str, password: str) -> str:
        email = (email or "").strip()
        if not email or not password:
            raise ConfigError("sub2api admin email/password 为空", code="sub2api_config")
        raw = request_json(
            "POST",
            _api_url(self.base_url, "/api/v1/auth/login"),
            body={"email": email, "password": password},
            token=None,
            timeout=self.timeout,
        )
        self.token = _extract_token(raw)
        return self.token

    def export_data(self, **params: Any) -> dict[str, Any]:
        """GET /api/v1/admin/accounts/data — full envelope."""
        # stdlib: build query manually for simple params
        from urllib.parse import urlencode

        q = {k: v for k, v in params.items() if v is not None and v != ""}
        path = "/api/v1/admin/accounts/data"
        if q:
            path = path + "?" + urlencode(q)
        data = self._call("GET", path)
        if not isinstance(data, dict):
            raise Sub2ApiError(f"export data unexpected type {type(data)}", code="sub2api_api")
        return data

    def import_data(
        self,
        envelope: dict[str, Any],
        *,
        skip_default_group_bind: bool = True,
    ) -> dict[str, Any]:
        """POST /api/v1/admin/accounts/data — UI importData."""
        if not isinstance(envelope, dict):
            raise ConfigError("import envelope must be object", code="sub2api_import")
        for k in ("accounts",):
            if k not in envelope:
                raise ConfigError(
                    f"import envelope missing {k!r} (need exported_at/proxies/accounts)",
                    code="sub2api_import",
                )
        body = {
            "data": envelope,
            "skip_default_group_bind": bool(skip_default_group_bind),
        }
        data = self._call("POST", "/api/v1/admin/accounts/data", body)
        return data if isinstance(data, dict) else {"raw": data}

    def list_accounts_page(
        self, page: int = 1, page_size: int = 100, **params: Any
    ) -> dict[str, Any]:
        from urllib.parse import urlencode

        q = {"page": page, "page_size": page_size, **params}
        path = "/api/v1/admin/accounts?" + urlencode(
            {k: v for k, v in q.items() if v is not None and v != ""}
        )
        data = self._call("GET", path)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
        return {"items": []}

    def list_all_accounts(
        self, *, page_size: int = 100, platform: str | None = None
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while page < 500:
            params: dict[str, Any] = {}
            if platform:
                params["platform"] = platform
            data = self.list_accounts_page(page=page, page_size=page_size, **params)
            items = data.get("items") or data.get("accounts") or data.get("data") or []
            if not isinstance(items, list):
                break
            for it in items:
                if isinstance(it, dict):
                    out.append(it)
            total = data.get("total")
            if total is not None and len(out) >= int(total):
                break
            if len(items) < page_size:
                break
            page += 1
        return out

    def delete_account(self, account_id: int | str) -> Any:
        return self._call("DELETE", f"/api/v1/admin/accounts/{account_id}")

    def apply_oauth_credentials(
        self,
        account_id: int | str,
        *,
        credentials: dict[str, Any],
        extra: dict[str, Any] | None = None,
        type: str = "oauth",
    ) -> Any:
        body: dict[str, Any] = {"type": type, "credentials": credentials}
        if extra is not None:
            body["extra"] = extra
        return self._call(
            "POST",
            f"/api/v1/admin/accounts/{account_id}/apply-oauth-credentials",
            body,
        )

    def update_account(self, account_id: int | str, payload: dict[str, Any]) -> Any:
        return self._call("PUT", f"/api/v1/admin/accounts/{account_id}", payload)


# ---------------------------------------------------------------------------
# Settings / pack loading
# ---------------------------------------------------------------------------


def resolve_sub2api_settings(cfg: dict | None = None) -> dict[str, Any]:
    """Merge env + config.yaml sub2api section. Never log password."""
    cfg = cfg or {}
    s2 = cfg.get("sub2api") if isinstance(cfg.get("sub2api"), dict) else {}
    base = (
        (os.environ.get("SUB2API_BASE_URL") or "").strip()
        or str(s2.get("base_url") or s2.get("url") or "").strip()
    )
    email = (
        (os.environ.get("SUB2API_ADMIN_EMAIL") or "").strip()
        or str(s2.get("admin_email") or s2.get("email") or "").strip()
    )
    password = (
        (os.environ.get("SUB2API_ADMIN_PASSWORD") or "").strip()
        or str(s2.get("admin_password") or s2.get("password") or "").strip()
    )
    export_dir = (
        (os.environ.get("SUB2API_EXPORT_DIR") or "").strip()
        or str(s2.get("export_dir") or DEFAULT_EXPORT_DIR).strip()
        or DEFAULT_EXPORT_DIR
    )
    timeout = float(s2.get("timeout") or os.environ.get("SUB2API_TIMEOUT") or 30)
    return {
        "base_url": base,
        "admin_email": email,
        "admin_password": password,
        "export_dir": export_dir,
        "timeout": timeout,
        "skip_default_group_bind": bool(s2.get("skip_default_group_bind", True)),
    }


def is_sub2api_envelope(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("accounts"), list)
        and "proxies" in doc
    )


def account_email(account: dict[str, Any]) -> str:
    if not isinstance(account, dict):
        return ""
    cred = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return str(
        cred.get("email")
        or extra.get("email")
        or extra.get("email_address")
        or account.get("name")
        or ""
    ).strip()


def account_platform(account: dict[str, Any]) -> str:
    if not isinstance(account, dict):
        return ""
    return str(account.get("platform") or "").strip().lower()


def match_key(account: dict[str, Any]) -> tuple[str, str] | None:
    """(platform, email_lower). Platform-scoped — never cross-platform."""
    plat = account_platform(account)
    email = account_email(account).lower()
    if plat and email and "@" in email:
        return plat, email
    name = str(account.get("name") or "").strip().lower()
    if plat and name:
        return plat, f"name:{name}"
    return None


def index_remote_by_platform_email(
    remotes: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    idx: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for a in remotes:
        if not isinstance(a, dict):
            continue
        k = match_key(a)
        if not k:
            continue
        idx.setdefault(k, []).append(a)
    for rows in idx.values():
        rows.sort(key=lambda r: int(r.get("id") or 0))
    return idx


def _account_id(row: dict[str, Any]) -> int | str | None:
    if row.get("id") is not None:
        return row["id"]
    return None


def load_pack_file(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not is_sub2api_envelope(doc):
        raise ConfigError(
            f"{path.name}: 不是 sub2api 导出外壳（需要 exported_at/proxies/accounts）",
            code="sub2api_import",
        )
    return doc


def list_local_packs(export_dir: Path | str) -> list[Path]:
    d = Path(export_dir)
    if not d.is_dir():
        return []
    return sorted(d.glob("grok-*.json"))


def merge_packs(paths: list[Path]) -> dict[str, Any]:
    """Merge one-account pack files into a single import envelope."""
    import time

    accounts: list[dict[str, Any]] = []
    proxies: list[dict[str, Any]] = []
    seen_proxy: set[str] = set()
    for p in paths:
        doc = load_pack_file(p)
        for a in doc.get("accounts") or []:
            if isinstance(a, dict):
                accounts.append(a)
        for px in doc.get("proxies") or []:
            if not isinstance(px, dict):
                continue
            key = json.dumps(px, sort_keys=True, ensure_ascii=False)
            if key in seen_proxy:
                continue
            seen_proxy.add(key)
            proxies.append(px)
    return {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proxies": proxies,
        "accounts": accounts,
    }


def filter_envelope_by_email(envelope: dict[str, Any], only: str | None) -> dict[str, Any]:
    if not only or only.strip().lower() in {"", "all", "*"}:
        return envelope
    needle = only.strip().lower()
    accs = []
    for a in envelope.get("accounts") or []:
        if not isinstance(a, dict):
            continue
        email = (
            ((a.get("credentials") or {}).get("email") if isinstance(a.get("credentials"), dict) else None)
            or (a.get("extra") or {}).get("email")
            if isinstance(a.get("extra"), dict)
            else None
            or a.get("name")
            or ""
        )
        if needle in str(email).lower():
            accs.append(a)
    out = dict(envelope)
    out["accounts"] = accs
    return out


def build_envelope_from_auth(
    auth_path: Path | str = "auth.json",
    *,
    only: str | None = None,
    include_model_mapping: bool = True,
) -> dict[str, Any]:
    """auth.json → one import envelope (no disk write)."""
    from .auth_pool import list_entries
    from .backends.export.sub2api import entry_to_sub2api_account, wrap_export_document

    accounts: list[dict[str, Any]] = []
    only_l = (only or "").strip().lower()
    for _key, entry in list_entries(auth_path, include_disabled=False):
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("email") or "").lower()
        if only_l and only_l not in {"all", "*"} and only_l not in email:
            continue
        try:
            accounts.append(
                entry_to_sub2api_account(
                    entry,
                    include_model_mapping=include_model_mapping,
                )
            )
        except Exception as e:  # noqa: BLE001
            log.warning("skip %s: %s", email or _key, e)
    return wrap_export_document(accounts)

def summarize_import_result(result: dict[str, Any] | None) -> str:
    r = result or {}
    parts = []
    for k in (
        "account_created",
        "account_failed",
        "proxy_created",
        "proxy_reused",
        "proxy_failed",
        "skipped_shadows",
    ):
        if k in r:
            parts.append(f"{k}={r[k]}")
    return " ".join(parts) if parts else json.dumps(r, ensure_ascii=False)[:200]


def summarize_upsert_plan(plan: list[dict[str, Any]]) -> str:
    from collections import Counter

    c = Counter(p.get("action") for p in plan)
    return " ".join(f"{k}={v}" for k, v in sorted(c.items()) if k) or "empty"


def plan_upsert_actions(
    local_accounts: list[dict[str, Any]],
    remote_index: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    on_exists: str = "create",
) -> list[dict[str, Any]]:
    """Decide create/update/skip. Match key is always (platform, email)."""
    mode = (on_exists or "create").strip().lower()
    if mode in {"update", "upsert", "replace"}:
        mode = "overwrite"
    if mode not in {"create", "skip", "overwrite"}:
        mode = "create"

    plan: list[dict[str, Any]] = []
    for acc in local_accounts:
        if not isinstance(acc, dict):
            continue
        k = match_key(acc)
        email = account_email(acc)
        plat = account_platform(acc) or "?"
        if not k:
            plan.append(
                {
                    "action": "skip",
                    "reason": "no_platform_email",
                    "email": email,
                    "platform": plat,
                }
            )
            continue
        # Platform already in key; filter again as safety
        remotes = [r for r in (remote_index.get(k) or []) if account_platform(r) == k[0]]
        if mode == "create" or not remotes:
            plan.append(
                {
                    "action": "create",
                    "email": email,
                    "platform": plat,
                    "key": k,
                    "local": acc,
                    "remote_ids": [_account_id(r) for r in remotes],
                }
            )
            continue
        if mode == "skip":
            plan.append(
                {
                    "action": "skip",
                    "reason": "exists",
                    "email": email,
                    "platform": plat,
                    "key": k,
                    "remote_ids": [_account_id(r) for r in remotes],
                }
            )
            continue
        # overwrite: delete ALL same-platform+email remotes, then create from local
        delete_ids = [i for i in (_account_id(r) for r in remotes) if i is not None]
        plan.append(
            {
                "action": "replace",
                "email": email,
                "platform": plat,
                "key": k,
                "local": acc,
                "delete_ids": delete_ids,
            }
        )
    return plan


def execute_upsert_plan(
    client: Sub2ApiAdminClient,
    plan: list[dict[str, Any]],
    *,
    skip_default_group_bind: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    from .backends.export.sub2api import wrap_export_document

    created = updated = skipped = deleted = failed = 0
    details: list[dict[str, Any]] = []
    to_create: list[dict[str, Any]] = []

    for item in plan:
        action = item.get("action")
        email = item.get("email")
        plat = item.get("platform")
        if action == "skip":
            skipped += 1
            details.append(
                {
                    "email": email,
                    "platform": plat,
                    "action": "skip",
                    "reason": item.get("reason"),
                }
            )
            continue
        if action == "create":
            to_create.append(item["local"])
            continue
        if action == "replace":
            # delete all same-platform matches, then queue for create
            delete_ids = [d for d in (item.get("delete_ids") or []) if d is not None]
            if dry_run:
                deleted += len(delete_ids)
                # will create 1 via to_create accounting below for dry_run
                to_create.append(item["local"])
                details.append(
                    {
                        "email": email,
                        "platform": plat,
                        "action": "replace",
                        "delete_ids": delete_ids,
                        "dry_run": True,
                    }
                )
                continue
            try:
                for did in delete_ids:
                    client.delete_account(did)
                    deleted += 1
                to_create.append(item["local"])
                details.append(
                    {
                        "email": email,
                        "platform": plat,
                        "action": "replace",
                        "deleted": delete_ids,
                    }
                )
            except Exception as e:  # noqa: BLE001
                failed += 1
                details.append(
                    {
                        "email": email,
                        "platform": plat,
                        "action": "replace",
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
            continue

        if action != "update":
            continue
        # legacy multi-update path (unused by overwrite)
        local = item.get("local") or {}
        creds = (
            local.get("credentials")
            if isinstance(local.get("credentials"), dict)
            else {}
        )
        extra = (
            local.get("extra")
            if isinstance(local.get("extra"), dict)
            else {"email": email}
        )
        update_ids = [i for i in (item.get("remote_ids") or []) if i is not None]
        if not update_ids and item.get("remote_id") is not None:
            update_ids = [item.get("remote_id")]
        if dry_run:
            updated += len(update_ids) or 1
            details.append(
                {
                    "email": email,
                    "platform": plat,
                    "action": "update",
                    "remote_ids": update_ids,
                    "dry_run": True,
                }
            )
            continue
        if not update_ids:
            failed += 1
            details.append(
                {
                    "email": email,
                    "platform": plat,
                    "action": "update",
                    "ok": False,
                    "error": "update missing remote_ids",
                }
            )
            continue
        ok_ids = []
        err = None
        for rid in update_ids:
            try:
                client.apply_oauth_credentials(
                    rid,
                    credentials=creds,
                    extra=extra,
                    type=str(local.get("type") or "oauth"),
                )
                ok_ids.append(rid)
                updated += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                err = f"{type(e).__name__}: {e}"
        details.append(
            {
                "email": email,
                "platform": plat,
                "action": "update",
                "remote_ids": update_ids,
                "updated_ids": ok_ids,
                "ok": err is None,
                **({"error": err} if err else {}),
            }
        )

    import_result = None
    if to_create:
        if dry_run:
            created = len(to_create)
            for acc in to_create:
                details.append(
                    {
                        "email": account_email(acc),
                        "platform": account_platform(acc),
                        "action": "create",
                        "dry_run": True,
                    }
                )
        else:
            env = wrap_export_document(to_create)
            try:
                import_result = client.import_data(
                    env, skip_default_group_bind=skip_default_group_bind
                )
                c = int((import_result or {}).get("account_created") or 0)
                f = int((import_result or {}).get("account_failed") or 0)
                created += c
                failed += f
                details.append(
                    {
                        "action": "create_batch",
                        "n": len(to_create),
                        "account_created": c,
                        "account_failed": f,
                    }
                )
            except Exception as e:  # noqa: BLE001
                failed += len(to_create)
                details.append(
                    {
                        "action": "create_batch",
                        "n": len(to_create),
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

    summary = (
        f"created={created} updated={updated} skipped={skipped} "
        f"deleted_dupes={deleted} failed={failed}"
    )
    return {
        "ok": failed == 0,
        "dry_run": dry_run,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "deleted_dupes": deleted,
        "failed": failed,
        "accounts": len(plan),
        "details": details,
        "import_result": import_result,
        "summary": summary,
    }


def upload_envelope(
    envelope: dict[str, Any],
    *,
    base_url: str,
    admin_email: str,
    admin_password: str,
    skip_default_group_bind: bool = True,
    timeout: float = 30.0,
    dry_run: bool = False,
    on_exists: str = "create",
) -> dict[str, Any]:
    """Login + upload. Match (platform, email) only — never cross-platform."""
    accounts = [a for a in (envelope.get("accounts") or []) if isinstance(a, dict)]
    n = len(accounts)
    mode = (on_exists or "create").strip().lower()
    if mode in {"update", "upsert", "replace"}:
        mode = "overwrite"
    if mode not in {"create", "skip", "overwrite"}:
        mode = "create"

    if n == 0:
        return {"ok": False, "error": "no accounts in envelope", "accounts": 0}

    # Fast path: pure create live import (legacy; may duplicate)
    if mode == "create" and not dry_run:
        client = Sub2ApiAdminClient(base_url, timeout=timeout)
        client.login(admin_email, admin_password)
        result = client.import_data(
            envelope, skip_default_group_bind=skip_default_group_bind
        )
        failed = int(result.get("account_failed") or 0) if isinstance(result, dict) else 0
        return {
            "ok": failed == 0,
            "dry_run": False,
            "accounts": n,
            "on_exists": mode,
            "created": int(result.get("account_created") or 0)
            if isinstance(result, dict)
            else 0,
            "updated": 0,
            "skipped": 0,
            "result": result,
            "summary": summarize_import_result(
                result if isinstance(result, dict) else {}
            ),
        }

    if mode == "create" and dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "accounts": n,
            "on_exists": mode,
            "created": n,
            "updated": 0,
            "skipped": 0,
            "message": (
                f"would POST {n} accounts to {normalize_base_url(base_url)} "
                f"(on_exists=create)"
            ),
            "summary": f"created={n} updated=0 skipped=0 deleted_dupes=0 failed=0",
        }

    client = Sub2ApiAdminClient(base_url, timeout=timeout)
    client.login(admin_email, admin_password)

    remote_rows = client.list_all_accounts()
    if not remote_rows:
        try:
            data = client.export_data()
            remote_rows = [
                a for a in (data.get("accounts") or []) if isinstance(a, dict)
            ]
        except Exception as e:  # noqa: BLE001
            log.warning("remote index fallback empty: %s", e)
            remote_rows = []

    remote_index = index_remote_by_platform_email(remote_rows)
    plan = plan_upsert_actions(accounts, remote_index, on_exists=mode)
    out = execute_upsert_plan(
        client,
        plan,
        skip_default_group_bind=skip_default_group_bind,
        dry_run=dry_run,
    )
    out["on_exists"] = mode
    out["plan_summary"] = summarize_upsert_plan(plan)
    if dry_run:
        out["message"] = (
            f"would apply on_exists={mode} to {normalize_base_url(base_url)}: "
            f"{out.get('summary')}"
        )
    return out


def upload_from_dir(
    export_dir: Path | str,
    *,
    base_url: str,
    admin_email: str,
    admin_password: str,
    only: str | None = None,
    skip_default_group_bind: bool = True,
    timeout: float = 30.0,
    dry_run: bool = False,
    merge: bool = True,
    on_exists: str = "create",
    limit: int | None = None,
) -> dict[str, Any]:
    """Load grok-*.json packs → upload with on_exists policy."""
    paths = list_local_packs(export_dir)
    if only and only.strip().lower() not in {"", "all", "*"}:
        needle = only.strip().lower()
        paths = [p for p in paths if needle in p.name.lower()]
    if not paths:
        return {"ok": False, "error": f"no packs in {export_dir}", "files": 0}

    if merge:
        env = merge_packs(paths)
        env = filter_envelope_by_email(env, only)
        if limit is not None and int(limit) > 0:
            accs = list(env.get("accounts") or [])
            before = len(accs)
            env = dict(env)
            env["accounts"] = accs[: int(limit)]
            log.info(
                "sub2api-upload limit=%s: truncated %s -> %s",
                limit,
                before,
                len(env["accounts"]),
            )
        out = upload_envelope(
            env,
            base_url=base_url,
            admin_email=admin_email,
            admin_password=admin_password,
            skip_default_group_bind=skip_default_group_bind,
            timeout=timeout,
            dry_run=dry_run,
            on_exists=on_exists,
        )
        out["files"] = len(paths)
        return out

    ok_n = fail_n = 0
    details = []
    for pth in paths:
        try:
            env = load_pack_file(pth)
            r = upload_envelope(
                env,
                base_url=base_url,
                admin_email=admin_email,
                admin_password=admin_password,
                skip_default_group_bind=skip_default_group_bind,
                timeout=timeout,
                dry_run=dry_run,
                on_exists=on_exists,
            )
            details.append(
                {
                    "file": pth.name,
                    **{
                        k: r[k]
                        for k in r
                        if k not in ("result", "details", "import_result")
                    },
                }
            )
            if r.get("ok"):
                ok_n += 1
            else:
                fail_n += 1
        except Exception as e:  # noqa: BLE001
            fail_n += 1
            details.append({"file": pth.name, "ok": False, "error": str(e)})
    return {
        "ok": fail_n == 0,
        "files": len(paths),
        "ok_n": ok_n,
        "fail_n": fail_n,
        "details": details,
        "dry_run": dry_run,
        "on_exists": on_exists,
    }
