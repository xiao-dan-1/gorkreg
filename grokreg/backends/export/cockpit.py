"""Cockpit Tools Grok account pack — auth.json entry → importable account JSON.

Target shape (from Cockpit Tools export sample ``grok_accounts_*.json``)::

    [ { id, email, auth_mode, access_token, refresh_token, ... }, ... ]

Same pack-export tier as cpa_files / sub2api (export factory).
  - primary: export_entry(auth_pool entry) / --export cockpit
  - does NOT mint; does NOT upload; does NOT rewrite auth.json

Per account file: ``cockpit_export/grok-<email>.json`` (single object).
Batch index:     ``cockpit_export/grok_accounts.json`` (array, upsert by email).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ...errors import ConfigError
from ...oauth.constants import AUTH, CLIENT_ID, TOKEN_URL
from ...token_clock import access_token_of, b64_jwt_claims, parse_entry_exp, refresh_token_of

DEFAULT_OUT_DIR = "cockpit_export"
BATCH_FILENAME = "grok_accounts.json"

# Stable UUID namespace for cockpit account ids (not a secret).
_COCKPIT_NS = uuid.UUID("a7c0f0e2-9b1d-4e6a-8c3f-1d2e3f4a5b6c")

OIDC_ISSUER = AUTH
OIDC_CLIENT_ID = CLIENT_ID
TOKEN_ENDPOINT = TOKEN_URL


def sanitize_email_for_filename(email: str) -> str:
    s = (email or "").strip()
    if not s:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9@._-]+", "-", s)


def account_filename(email: str) -> str:
    return f"grok-{sanitize_email_for_filename(email)}.json"


def stable_account_id(email: str, *, sub: str = "") -> str:
    """Deterministic id so re-export does not invent a new Cockpit account row."""
    key = (email or "").strip().lower() or (sub or "").strip() or "unknown"
    return str(uuid.uuid5(_COCKPIT_NS, f"cockpit-grok:{key}"))


def _expires_at_raw(exp_unix: int) -> str:
    """Match Cockpit sample: ``2026-07-18T14:59:29+00:00`` (not Z)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(int(exp_unix)))


def _now_ms() -> int:
    return int(time.time() * 1000)


def entry_to_cockpit_account(
    entry: dict[str, Any],
    *,
    account_id: str | None = None,
    include_auth_raw: bool = True,
    include_empty_shells: bool = True,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Map one auth.json / mint payload entry → Cockpit Grok account object."""
    if not isinstance(entry, dict):
        raise ConfigError("cockpit export entry must be dict", code="export_cockpit")

    email = (entry.get("email") or "").strip()
    at = access_token_of(entry)
    rt = refresh_token_of(entry)
    if not email:
        raise ConfigError("cockpit export: missing email", code="export_cockpit")
    if not rt and not at:
        raise ConfigError(
            f"cockpit export: {email} has neither access_token nor refresh_token",
            code="export_cockpit",
        )

    exp_f = parse_entry_exp(entry)
    exp_unix = int(exp_f) if exp_f and exp_f > 0 else 0
    if exp_unix <= 0 and at:
        claims0 = b64_jwt_claims(at)
        try:
            exp_unix = int(claims0.get("exp") or 0)
        except (TypeError, ValueError):
            exp_unix = 0

    at_claims = b64_jwt_claims(at) if at else {}
    id_tok = (entry.get("id_token") or "").strip()
    id_claims = b64_jwt_claims(id_tok) if id_tok else {}

    sub = (
        (entry.get("sub") or "").strip()
        or str(at_claims.get("sub") or at_claims.get("principal_id") or "").strip()
        or str(id_claims.get("sub") or "").strip()
    )
    principal_id = (
        str(at_claims.get("principal_id") or sub or "").strip() or sub
    )
    principal_type = str(at_claims.get("principal_type") or "User").strip() or "User"
    team_id = str(at_claims.get("team_id") or entry.get("team_id") or "").strip()
    user_id = str(at_claims.get("user_id") or principal_id or sub).strip()

    first_name = str(
        id_claims.get("given_name")
        or entry.get("first_name")
        or ""
    ).strip()
    last_name = str(
        id_claims.get("family_name")
        or entry.get("last_name")
        or ""
    ).strip()

    ts_ms = int(now_ms) if now_ms is not None else _now_ms()
    acc_id = (account_id or "").strip() or stable_account_id(email, sub=sub)

    exp_raw = _expires_at_raw(exp_unix) if exp_unix > 0 else ""

    account: dict[str, Any] = {
        "id": acc_id,
        "email": email,
        "auth_mode": "oauth",
        "first_name": first_name,
        "last_name": last_name,
        "user_id": user_id,
        "principal_id": principal_id,
        "principal_type": principal_type,
        "team_id": team_id,
        "access_token": at,
        "refresh_token": rt,
        "token_type": (entry.get("token_type") or "Bearer").strip() or "Bearer",
        "expires_at": exp_unix,
        "expires_at_raw": exp_raw,
        "oidc_issuer": OIDC_ISSUER,
        "oidc_client_id": (
            str(at_claims.get("client_id") or at_claims.get("aud") or OIDC_CLIENT_ID).strip()
            or OIDC_CLIENT_ID
        ),
        "token_endpoint": (
            (entry.get("token_endpoint") or TOKEN_ENDPOINT).strip() or TOKEN_ENDPOINT
        ),
        "has_grok_code_access": True,
        "created_at": ts_ms,
        "last_used": ts_ms,
    }
    if id_tok:
        account["id_token"] = id_tok

    if include_auth_raw:
        auth_raw: dict[str, Any] = {
            "auth_mode": "oidc",
            "coding_data_retention_opt_out": False,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "key": at,
            "oidc_client_id": account["oidc_client_id"],
            "oidc_issuer": OIDC_ISSUER,
            "principal_id": principal_id,
            "principal_type": principal_type,
            "profile_image_asset_id": None,
            "refresh_token": rt,
            "team_id": team_id or None,
            "user_id": user_id,
        }
        if exp_raw:
            auth_raw["expires_at"] = exp_raw
            # create_time-ish: now ISO with subsecond-ish pad (Cockpit sample has ns)
            auth_raw["create_time"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime()
            )
        account["auth_raw"] = auth_raw

    if include_empty_shells:
        # Optional shells so re-import looks closer to a full Cockpit export.
        # No remote probe — do not invent real quota/billing numbers.
        account["subscription_raw"] = {"subscriptions": []}

    return account


def _atomic_write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def merge_into_batch_array(batch_path: Path, account: dict[str, Any]) -> Path:
    """Upsert one account into ``grok_accounts.json`` array (by email, else id)."""
    email = (account.get("email") or "").strip().lower()
    acc_id = (account.get("id") or "").strip()
    existing: list[Any] = []
    if batch_path.is_file():
        try:
            raw = json.loads(batch_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
            elif isinstance(raw, dict):
                # tolerate single-object file
                existing = [raw]
        except Exception:
            existing = []

    out: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        if not isinstance(item, dict):
            continue
        ie = (item.get("email") or "").strip().lower()
        iid = (item.get("id") or "").strip()
        if (email and ie == email) or (acc_id and iid == acc_id):
            out.append(account)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(account)
    return _atomic_write_json(batch_path, out)


class CockpitExport:
    """Write Cockpit Tools–shaped Grok account JSON under out_dir."""

    name = "cockpit"
    kind = "pack"
    source = "auth_pool"

    def __init__(
        self,
        out_dir: str | Path = DEFAULT_OUT_DIR,
        *,
        include_auth_raw: bool = True,
        include_empty_shells: bool = True,
        write_batch_index: bool = True,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.include_auth_raw = include_auth_raw
        self.include_empty_shells = include_empty_shells
        self.write_batch_index = write_batch_index

    def preview_path(self, email: str) -> Path:
        return self.out_dir / account_filename(email)

    def batch_path(self) -> Path:
        return self.out_dir / BATCH_FILENAME

    def export_entry(
        self,
        entry: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        account = entry_to_cockpit_account(
            entry,
            include_auth_raw=self.include_auth_raw,
            include_empty_shells=self.include_empty_shells,
        )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if filename:
            fname = filename if filename.endswith(".json") else f"{filename}.json"
            path = self.out_dir / fname
        else:
            path = self.out_dir / account_filename(account["email"])
        _atomic_write_json(path, account)
        if self.write_batch_index:
            merge_into_batch_array(self.batch_path(), account)
        return path

    def save_cpa(
        self,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        """Alias for export_entry (mint-shaped payload treated as pool entry)."""
        return self.export_entry(payload, filename=filename)

    def upsert_cpa(self, cpa_path: Path | str) -> str:
        raise ConfigError(
            "cockpit backend does not upsert auth.json; use auth_pool",
            code="export_backend",
        )


def export_auth_pool_to_cockpit(
    auth_path: Path | str,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    *,
    only: str | None = None,
    include_disabled: bool = False,
    require_refresh_token: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Thin wrapper on factory.export_auth_pool for cockpit pack."""
    from .factory import export_auth_pool

    return export_auth_pool(
        "cockpit",
        auth_path,
        out_dir=out_dir,
        only=only,
        include_disabled=include_disabled,
        require_refresh_token=require_refresh_token,
        dry_run=dry_run,
    )
