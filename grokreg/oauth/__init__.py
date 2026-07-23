"""xAI OAuth / device-code protocol (mint · refresh_tokens · probe).

Not CLIProxy xAI packs — those are export/upload (xai_pack / cpa_export / --cpa-upload (export)).
File-path cpa refresh has been removed (no compat). Use refresh_tokens + auth.json.

    from grokreg.oauth import mint, refresh_tokens, probe_quota
"""
from __future__ import annotations

from .constants import (
    ACCOUNTS,
    AUTH,
    BASE_URL,
    CLIENT_ID,
    COOKIE_SETTER,
    DEVICE_CODE_URL,
    SCOPES,
    TOKEN_URL,
    set_sso,
)
from .mint import mint
from .probe_billing import (
    grok_quota_from_ratelimit_headers,
    merge_billing_snapshots,
    parse_billing_payload,
)
from .probe_quota import probe_quota
from .probe_classify import (
    classify_chat_probe,
    classify_from_http_body,
    is_free_usage_exhausted,
)
from .probe_support import (
    _cli_headers,
    _post_write_probe,
    _probe_chat,
    _probe_models,
    _resolve_probe_mode,
)
from .refresh import refresh_tokens

__all__ = [
    "CLIENT_ID",
    "AUTH",
    "ACCOUNTS",
    "DEVICE_CODE_URL",
    "TOKEN_URL",
    "COOKIE_SETTER",
    "BASE_URL",
    "SCOPES",
    "set_sso",
    "mint",
    "refresh_tokens",
    "probe_quota",
    "grok_quota_from_ratelimit_headers",
    "parse_billing_payload",
    "merge_billing_snapshots",
    "classify_chat_probe",
    "classify_from_http_body",
    "is_free_usage_exhausted",
]
