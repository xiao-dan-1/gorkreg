"""CPA (CLIProxy auth JSON) ↔ sub2api (UI import envelope) converters.

Supported route (this repo, P0):
  CPA type=xai  ↔  sub2api platform=grok + type=oauth

NOT supported (must skip / fail — never silently rewrite as xai/grok):
  - CPA type=codex (OpenAI OAuth in CLIProxy)
  - sub2api platform=openai (+ oauth | apikey)
  - other platforms (claude, gemini, …)

Evidence:
  - CLIProxy: internal/auth/xai → type \"xai\"; internal/auth/codex → type \"codex\"
  - sub2api schema: platform + type; openai oauth ≠ openai apikey
  - upload match key is (platform, email) — never cross-platform

This module does **not** mint/refresh tokens and does **not** upload.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from .xai_pack.schema import (
    CLIENT_ID as XAI_CLIENT_ID,
    DEFAULT_BASE_URL,
    DEFAULT_CLIENT_HEADERS,
    DEFAULT_REDIRECT_URI,
    DEFAULT_TOKEN_ENDPOINT,
    build_xai_auth,
    credential_file_name,
)
from .xai_pack.writer import write_xai_auth
from ...errors import ConfigError
from ...token_clock import parse_entry_exp
from .cpa_files import cpa_filename
from .sub2api import (
    account_filename as sub2api_filename,
    entry_to_sub2api_account,
    wrap_export_document,
    write_account_json,
)

Kind = Literal["cpa", "sub2api_envelope", "sub2api_account", "unknown"]
# Logical product family after detection (not always equal to file type field).
Provider = Literal[
    "xai",  # CPA xai / sub grok oauth — supported
    "openai",  # sub openai (oauth or apikey) or CPA codex
    "claude",
    "gemini",
    "other",
    "unknown",
]

# First-period convert only allows this pair.
SUPPORTED_CPA_TYPES = frozenset({"xai"})
SUPPORTED_SUB_PLATFORMS = frozenset({"grok"})
XAI_CLIENT_ID_PREFIX = "b1a00492-"  # xAI device-code client
OPENAI_CODEX_CLIENT_HINTS = ("app_EMoamEEZ", "chatgpt", "auth.openai.com")


def _strip(s: Any) -> str:
    return str(s or "").strip()


def detect_kind(data: Any) -> Kind:
    """Classify a loaded JSON object (shape only; not provider)."""
    if not isinstance(data, dict):
        return "unknown"
    # envelope first
    if "accounts" in data and isinstance(data.get("accounts"), list):
        if "exported_at" in data or "proxies" in data:
            return "sub2api_envelope"
        if data["accounts"] and isinstance(data["accounts"][0], dict):
            acc0 = data["accounts"][0]
            if acc0.get("platform") or acc0.get("credentials"):
                return "sub2api_envelope"
    # bare sub2api account — any platform with credentials nest
    if isinstance(data.get("credentials"), dict) and (
        data.get("platform")
        or data.get("type") in {"oauth", "apikey", "api_key", "cookie"}
    ):
        return "sub2api_account"
    # CPA / CLIProxy flat auth (type field is provider: xai|codex|claude|…)
    pack_type = _strip(data.get("type")).lower()
    if pack_type in {"xai", "codex", "claude", "kimi", "antigravity", "vertex"}:
        if data.get("access_token") or data.get("refresh_token") or data.get("email"):
            return "cpa"
    if data.get("auth_kind") == "oauth" and "credentials" not in data:
        if data.get("access_token") or data.get("refresh_token") or data.get("email"):
            return "cpa"
    # heuristic flat tokens + email (ambiguous — still xai-pack-shaped)
    if (
        "credentials" not in data
        and (data.get("access_token") or data.get("key"))
        and data.get("refresh_token")
        and data.get("email")
    ):
        return "cpa"
    return "unknown"


def detect_cpa_provider(cpa: dict[str, Any], *, path: Path | str | None = None) -> Provider:
    """Detect CPA / CLIProxy provider. Prefer explicit type, then filename/hints."""
    if not isinstance(cpa, dict):
        return "unknown"
    t = _strip(cpa.get("type")).lower()
    if t == "xai":
        return "xai"
    if t == "codex":
        return "openai"
    if t in {"claude", "anthropic"}:
        return "claude"
    if t in {"gemini", "vertex"}:
        return "gemini"
    if t and t not in {"oauth", "apikey", "api_key", "cookie", "bearer"}:
        # unknown explicit provider type — do not treat as xai
        return "other"

    name = Path(path).name.lower() if path else ""
    if name.startswith("xai-"):
        return "xai"
    if name.startswith("codex-"):
        return "openai"
    if name.startswith("claude-"):
        return "claude"

    base = _strip(cpa.get("base_url")).lower()
    if "cli-chat-proxy.grok.com" in base or "auth.x.ai" in base:
        return "xai"
    if "api.openai.com" in base or "chatgpt.com" in base:
        return "openai"

    # OpenAI-ish extra fields on flat files
    if cpa.get("account_id") or cpa.get("chatgpt_account_id"):
        return "openai"

    # auth_kind=oauth + xai headers → xai
    headers = cpa.get("headers") if isinstance(cpa.get("headers"), dict) else {}
    hdr = " ".join(str(v) for v in headers.values()).lower()
    if "xai-grok-cli" in hdr or "grok-shell" in hdr:
        return "xai"

    if cpa.get("auth_kind") == "oauth" and (cpa.get("access_token") or cpa.get("refresh_token")):
        # ambiguous oauth flat — still unknown (do NOT default xai)
        return "unknown"
    return "unknown"


def detect_sub_provider(account: dict[str, Any]) -> Provider:
    """Detect sub2api account provider from platform + credentials hints."""
    if not isinstance(account, dict):
        return "unknown"
    plat = _strip(account.get("platform")).lower()
    if plat in {"grok", "xai"}:
        return "xai"
    if plat in {"openai", "gpt", "codex"}:
        return "openai"
    if plat in {"claude", "anthropic"}:
        return "claude"
    if plat in {"gemini", "google"}:
        return "gemini"
    if plat:
        return "other"

    creds = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    if not creds:
        return "unknown"
    if creds.get("chatgpt_account_id") or creds.get("plan_type") or creds.get("organization_id"):
        return "openai"
    if creds.get("api_key") and not creds.get("refresh_token"):
        return "openai"
    cid = _strip(creds.get("client_id")).lower()
    if cid.startswith(XAI_CLIENT_ID_PREFIX) or cid == XAI_CLIENT_ID.lower():
        return "xai"
    base = _strip(creds.get("base_url")).lower()
    if "cli-chat-proxy.grok.com" in base:
        return "xai"
    if "api.openai.com" in base:
        return "openai"
    for h in OPENAI_CODEX_CLIENT_HINTS:
        if h in cid or h in base:
            return "openai"
    return "unknown"


def is_supported_cpa(cpa: dict[str, Any], *, path: Path | str | None = None) -> tuple[bool, str, Provider]:
    """Return (ok, reason, provider). Only xai is convertible."""
    prov = detect_cpa_provider(cpa, path=path)
    if prov == "xai":
        return True, "xai", prov
    if prov == "openai":
        return False, "unsupported_platform provider=openai (CPA type=codex; use codex↔openai route later)", prov
    if prov in {"claude", "gemini", "other"}:
        return False, f"unsupported_platform provider={prov}", prov
    return False, "unknown_platform (refusing silent xai default)", prov


def is_supported_sub_account(account: dict[str, Any]) -> tuple[bool, str, Provider]:
    """Return (ok, reason, provider). Only grok/xai oauth is convertible."""
    prov = detect_sub_provider(account)
    auth_type = _strip(account.get("type")).lower()
    if prov == "xai":
        if auth_type in {"", "oauth"}:
            return True, "grok_oauth", prov
        return False, f"unsupported_auth_type platform=grok type={auth_type or '?'}", prov
    if prov == "openai":
        return (
            False,
            f"unsupported_platform provider=openai type={auth_type or '?'} "
            "(do not rewrite as xai; codex route not implemented)",
            prov,
        )
    if prov in {"claude", "gemini", "other"}:
        return False, f"unsupported_platform provider={prov} type={auth_type or '?'}", prov
    return False, "unknown_platform (refusing silent xai default)", prov


def load_json(path: Path | str) -> Any:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"bad json: {p}: {e}", code="pack_convert") from e


def cpa_to_auth_entry(cpa: dict[str, Any], *, path: Path | str | None = None) -> dict[str, Any]:
    """Map CPA payload → auth-pool-like entry (for entry_to_sub2api_account).

    Raises if provider is not xai.
    """
    if not isinstance(cpa, dict):
        raise ConfigError("xai pack payload must be dict", code="pack_convert")
    ok, reason, _prov = is_supported_cpa(cpa, path=path)
    if not ok:
        raise ConfigError(f"xai→sub2api blocked: {reason}", code="pack_convert_platform")

    email = _strip(cpa.get("email"))
    at = _strip(cpa.get("access_token") or cpa.get("key"))
    rt = _strip(cpa.get("refresh_token"))
    if not email:
        raise ConfigError("xai→sub2api: missing email", code="pack_convert")
    if not at and not rt:
        raise ConfigError(f"xai→sub2api: {email} has no tokens", code="pack_convert")

    entry: dict[str, Any] = {
        "email": email,
        "access_token": at,
        "refresh_token": rt,
        "id_token": _strip(cpa.get("id_token")),
        "base_url": _strip(cpa.get("base_url")) or DEFAULT_BASE_URL,
        "token_type": _strip(cpa.get("token_type")) or "Bearer",
        "disabled": bool(cpa.get("disabled")),
        "sub": _strip(cpa.get("sub")),
    }
    if cpa.get("expires_at") is not None:
        entry["expires_at"] = cpa["expires_at"]
    if cpa.get("expired") is not None:
        entry["expired"] = cpa["expired"]
    if cpa.get("expires") is not None:
        entry["expires"] = cpa["expires"]
    if cpa.get("expires_in") is not None:
        entry["expires_in"] = cpa["expires_in"]
    return entry


def cpa_to_sub2api_account(
    cpa: dict[str, Any],
    *,
    include_model_mapping: bool = True,
    name: str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """CPA xai dict → one sub2api accounts[] element (platform=grok)."""
    entry = cpa_to_auth_entry(cpa, path=path)
    return entry_to_sub2api_account(
        entry,
        name=name,
        include_model_mapping=include_model_mapping,
        auto_pause_on_expired=True,
    )


def cpa_to_sub2api_document(
    cpa: dict[str, Any],
    *,
    include_model_mapping: bool = True,
    proxies: list[dict[str, Any]] | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """CPA xai dict → full sub2api import envelope (accounts len=1)."""
    acc = cpa_to_sub2api_account(
        cpa, include_model_mapping=include_model_mapping, path=path
    )
    return wrap_export_document([acc], proxies=proxies)


def iter_sub2api_accounts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract accounts[] from envelope or wrap bare account."""
    kind = detect_kind(data)
    if kind == "sub2api_envelope":
        out = []
        for a in data.get("accounts") or []:
            if isinstance(a, dict):
                out.append(a)
        return out
    if kind == "sub2api_account":
        return [data]
    raise ConfigError(
        f"not a sub2api payload (kind={kind})",
        code="pack_convert",
    )


def sub2api_account_to_cpa(account: dict[str, Any]) -> dict[str, Any]:
    """One sub2api grok account → CPA xai payload via build_xai_auth.

    Raises on non-grok platforms (openai/codex/…) instead of rewriting as xai.
    """
    if not isinstance(account, dict):
        raise ConfigError("sub2api account must be dict", code="pack_convert")
    ok, reason, _prov = is_supported_sub_account(account)
    if not ok:
        raise ConfigError(f"sub2api→cpa blocked: {reason}", code="pack_convert_platform")

    creds = account.get("credentials")
    if not isinstance(creds, dict):
        raise ConfigError("sub2api account missing credentials", code="pack_convert")

    email = (
        _strip(creds.get("email"))
        or _strip(
            (account.get("extra") or {}).get("email")
            if isinstance(account.get("extra"), dict)
            else ""
        )
        or _strip(account.get("name"))
    )
    at = _strip(creds.get("access_token") or creds.get("key"))
    rt = _strip(creds.get("refresh_token"))
    if not email:
        raise ConfigError("sub2api→cpa: missing email", code="pack_convert")
    if not at:
        raise ConfigError(
            f"sub2api→cpa: {email} missing access_token (CPA requires AT+RT)",
            code="pack_convert",
        )
    if not rt:
        raise ConfigError(
            f"sub2api→cpa: {email} missing refresh_token (CPA requires RT to renew)",
            code="pack_convert",
        )

    base_url = _strip(creds.get("base_url")) or DEFAULT_BASE_URL
    expired: str | None = None
    expires_in: int | None = None
    exp_unix = parse_entry_exp(
        {
            "expires_at": creds.get("expires_at"),
            "expired": creds.get("expired"),
            "access_token": at,
        }
    )
    if exp_unix and exp_unix > 0:
        expired = datetime.fromtimestamp(exp_unix, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        left = int(exp_unix - datetime.now(tz=timezone.utc).timestamp())
        expires_in = max(left, 0) if left > 0 else int(creds.get("expires_in") or 21600)

    disabled = bool(account.get("disabled"))

    payload = build_xai_auth(
        email=email,
        access_token=at,
        refresh_token=rt,
        id_token=_strip(creds.get("id_token")) or None,
        expires_in=expires_in,
        expired=expired,
        base_url=base_url,
        token_endpoint=DEFAULT_TOKEN_ENDPOINT,
        redirect_uri=DEFAULT_REDIRECT_URI,
        headers=dict(DEFAULT_CLIENT_HEADERS),
        disabled=disabled,
        extra={
            "expires_at": exp_unix if exp_unix else None,
        },
    )
    if payload.get("expires_at") is None:
        payload.pop("expires_at", None)
    return payload


def sub2api_to_cpa_payloads(
    data: dict[str, Any],
    *,
    skip_unsupported: bool = False,
) -> list[dict[str, Any]]:
    """Envelope or bare account → list of CPA xai payloads.

    By default raises on first unsupported account.
    skip_unsupported=True returns only supported accounts (batch use).
    """
    out: list[dict[str, Any]] = []
    for a in iter_sub2api_accounts(data):
        ok, reason, _prov = is_supported_sub_account(a)
        if not ok:
            if skip_unsupported:
                continue
            raise ConfigError(
                f"sub2api→cpa blocked: {reason}",
                code="pack_convert_platform",
            )
        out.append(sub2api_account_to_cpa(a))
    return out


# ─── batch file I/O ───────────────────────────────────────────────────────────


def _iter_input_paths(paths: Iterable[Path | str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise ConfigError(f"path not found: {p}", code="pack_convert")
        if p.is_file():
            if p.suffix.lower() == ".json":
                out.append(p)
            continue
        out.extend(sorted(p.rglob("*.json")))
    return out


def _email_match(email: str, only: str | None) -> bool:
    if not only:
        return True
    o = only.strip().lower()
    e = (email or "").strip().lower()
    return o in e or e == o


def _platform_filter_allows(prov: Provider, platform_filter: str | None) -> bool:
    """--platform filter: only process matching provider family."""
    if not platform_filter:
        return True
    want = platform_filter.strip().lower()
    aliases = {
        "xai": {"xai", "grok"},
        "grok": {"xai", "grok"},
        "openai": {"openai", "codex", "gpt"},
        "codex": {"openai", "codex", "gpt"},
        "claude": {"claude", "anthropic"},
        "gemini": {"gemini", "google"},
    }
    allowed = aliases.get(want, {want})
    return prov in allowed or (prov == "xai" and want in {"xai", "grok"})


def convert_paths(
    direction: Literal["cpa-to-sub", "sub-to-cpa", "auto"],
    paths: Iterable[Path | str],
    *,
    out_dir: Path | str,
    dry_run: bool = False,
    only: str | None = None,
    limit: int = 0,
    include_model_mapping: bool = True,
    merge_envelope: bool = False,
    platform: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Batch convert files with provider guards.

    direction:
      cpa-to-sub  — xai-*.json → grok-*.json (envelope)
      sub-to-cpa  — grok envelope → xai-*.json
      auto        — detect per file

    Unsupported platforms are **skipped** by default (counted in skip_unsupported).
    strict=True counts them as fail instead.
    platform=  optional filter (xai|grok|openai|codex|…)
    """
    out_dir = Path(out_dir)
    inputs = _iter_input_paths(paths)
    stats: dict[str, Any] = {
        "direction": direction,
        "inputs": len(inputs),
        "ok": 0,
        "skip": 0,
        "skip_unsupported": 0,
        "fail": 0,
        "written": [],
        "errors": [],
        "dry_run": dry_run,
        "out_dir": str(out_dir),
        "platform_filter": platform,
        "strict": strict,
        "providers_seen": {},
    }

    def _bump_prov(p: str) -> None:
        m = stats["providers_seen"]
        m[p] = int(m.get(p) or 0) + 1

    def _unsupported(path: Path, reason: str, prov: str) -> None:
        # caller already bumped providers_seen when known
        if strict:
            stats["fail"] += 1
            stats["errors"].append(
                {"path": str(path), "error": reason, "provider": prov}
            )
        else:
            stats["skip"] += 1
            stats["skip_unsupported"] += 1
            stats["errors"].append(
                {"path": str(path), "error": reason, "provider": prov, "action": "skip"}
            )

    merged_accounts: list[dict[str, Any]] = []
    processed = 0

    for path in inputs:
        if limit and processed >= limit:
            break
        try:
            data = load_json(path)
            kind = detect_kind(data)

            dir_ = direction
            if dir_ == "auto":
                if kind == "cpa":
                    dir_ = "cpa-to-sub"
                elif kind in {"sub2api_envelope", "sub2api_account"}:
                    dir_ = "sub-to-cpa"
                else:
                    stats["skip"] += 1
                    stats["errors"].append(
                        {"path": str(path), "error": f"unknown kind={kind}"}
                    )
                    processed += 1
                    continue

            if dir_ == "cpa-to-sub":
                if kind in {"sub2api_envelope", "sub2api_account"}:
                    stats["skip"] += 1
                    stats["errors"].append(
                        {"path": str(path), "error": "already sub2api, skip"}
                    )
                    processed += 1
                    continue
                if kind == "unknown":
                    raise ConfigError(
                        f"{path.name}: not CPA (kind={kind})", code="pack_convert"
                    )
                if not isinstance(data, dict):
                    raise ConfigError(f"{path.name}: not a dict", code="pack_convert")

                ok, reason, prov = is_supported_cpa(data, path=path)
                _bump_prov(prov)
                if not _platform_filter_allows(prov, platform):
                    stats["skip"] += 1
                    processed += 1
                    continue
                if not ok:
                    _unsupported(path, reason, prov)
                    processed += 1
                    continue

                email = _strip(data.get("email"))
                if not _email_match(email, only):
                    stats["skip"] += 1
                    processed += 1
                    continue
                acc = cpa_to_sub2api_account(
                    data,
                    include_model_mapping=include_model_mapping,
                    path=path,
                )
                if merge_envelope:
                    merged_accounts.append(acc)
                    stats["ok"] += 1
                    processed += 1
                    continue
                if dry_run:
                    dest = out_dir / sub2api_filename(email)
                    stats["ok"] += 1
                    stats["written"].append(str(dest))
                    processed += 1
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = write_account_json(out_dir, acc)
                stats["ok"] += 1
                stats["written"].append(str(dest))
                processed += 1

            elif dir_ == "sub-to-cpa":
                if kind == "cpa":
                    stats["skip"] += 1
                    stats["errors"].append(
                        {"path": str(path), "error": "already cpa, skip"}
                    )
                    processed += 1
                    continue
                if kind == "unknown":
                    raise ConfigError(
                        f"{path.name}: not sub2api (kind={kind})", code="pack_convert"
                    )
                accounts = iter_sub2api_accounts(data)
                if not accounts:
                    stats["skip"] += 1
                    processed += 1
                    continue
                any_ok = False
                for account in accounts:
                    ok, reason, prov = is_supported_sub_account(account)
                    _bump_prov(prov)
                    if not _platform_filter_allows(prov, platform):
                        stats["skip"] += 1
                        continue
                    if not ok:
                        _unsupported(path, reason, prov)
                        continue
                    # supported → convert one
                    payload = sub2api_account_to_cpa(account)
                    email = _strip(payload.get("email"))
                    if not _email_match(email, only):
                        stats["skip"] += 1
                        continue
                    dest_name = credential_file_name(
                        email, _strip(payload.get("sub"))
                    )
                    dest = out_dir / dest_name
                    if dry_run:
                        stats["ok"] += 1
                        stats["written"].append(str(dest))
                        any_ok = True
                        continue
                    written = write_xai_auth(
                        out_dir, payload, filename=dest_name
                    )
                    stats["ok"] += 1
                    stats["written"].append(str(written))
                    any_ok = True
                processed += 1
                if not any_ok and not stats["errors"]:
                    # all filtered by --only
                    pass
            else:
                raise ConfigError(f"bad direction: {dir_!r}", code="pack_convert")

        except Exception as e:
            stats["fail"] += 1
            stats["errors"].append({"path": str(path), "error": str(e)})
            processed += 1

    if merge_envelope and direction in {"cpa-to-sub", "auto"} and merged_accounts:
        doc = wrap_export_document(merged_accounts)
        dest = out_dir / "grok-merged-export.json"
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(dest)
        stats["written"].append(str(dest))
        stats["merged_accounts"] = len(merged_accounts)

    return stats


def guess_direction_from_path(
    path: Path | str,
) -> Literal["cpa-to-sub", "sub-to-cpa"] | None:
    """Filename heuristics when JSON detect is ambiguous."""
    name = Path(path).name.lower()
    if name.startswith("xai-") or re.match(r"xai-.+\.json$", name):
        return "cpa-to-sub"
    if name.startswith("codex-"):
        return "cpa-to-sub"  # still CPA-shaped; provider gate will skip/fail
    if name.startswith("grok-") or "sub2api" in name:
        return "sub-to-cpa"
    return None


__all__ = [
    "Kind",
    "Provider",
    "SUPPORTED_CPA_TYPES",
    "SUPPORTED_SUB_PLATFORMS",
    "detect_kind",
    "detect_cpa_provider",
    "detect_sub_provider",
    "is_supported_cpa",
    "is_supported_sub_account",
    "load_json",
    "cpa_to_auth_entry",
    "cpa_to_sub2api_account",
    "cpa_to_sub2api_document",
    "iter_sub2api_accounts",
    "sub2api_account_to_cpa",
    "sub2api_to_cpa_payloads",
    "convert_paths",
    "guess_direction_from_path",
    "cpa_filename",
    "sub2api_filename",
]
