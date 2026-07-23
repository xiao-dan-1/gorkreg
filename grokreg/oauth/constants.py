"""OAuth / cli-proxy constants and small helpers shared by mint/refresh/probe."""
from __future__ import annotations

import threading
from typing import Any

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
AUTH = "https://auth.x.ai"
ACCOUNTS = "https://accounts.x.ai"
DEVICE_CODE_URL = f"{AUTH}/oauth2/device/code"
TOKEN_URL = f"{AUTH}/oauth2/token"
COOKIE_SETTER = f"{ACCOUNTS}/auth_mgmt.AuthManagement/CreateCookieSetterLink"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
SCOPES = "openid profile email offline_access grok-cli:access api:access"

# Parallel mint: optional spacing between device/code starts + 429 backoff.
# Adaptive interval: env GROK_DEVICE_CODE_MIN_INTERVAL overrides; else 429 raises
# spacing and successes decay it (token-bucket style, process-local).
_DEVICE_CODE_LOCK = threading.Lock()
_device_code_last_ts = 0.0
_device_code_adaptive_interval = 0.0  # seconds
_DEVICE_CODE_ADAPTIVE_MAX = 1.0
_DEVICE_CODE_ADAPTIVE_STEP = 0.25
_DEVICE_CODE_ADAPTIVE_DECAY = 0.12


def clear_device_code_throttle() -> None:
    """Test/helper: reset adaptive device/code spacing."""
    global _device_code_last_ts, _device_code_adaptive_interval
    with _DEVICE_CODE_LOCK:
        _device_code_last_ts = 0.0
        _device_code_adaptive_interval = 0.0


def note_device_code_429() -> None:
    """Raise adaptive min-interval after HTTP 429 (capped)."""
    global _device_code_adaptive_interval
    with _DEVICE_CODE_LOCK:
        _device_code_adaptive_interval = min(
            _DEVICE_CODE_ADAPTIVE_MAX,
            max(_DEVICE_CODE_ADAPTIVE_STEP, _device_code_adaptive_interval + _DEVICE_CODE_ADAPTIVE_STEP),
        )


def note_device_code_success() -> None:
    """Decay adaptive spacing after a successful device/code."""
    global _device_code_adaptive_interval
    with _DEVICE_CODE_LOCK:
        _device_code_adaptive_interval = max(
            0.0, _device_code_adaptive_interval - _DEVICE_CODE_ADAPTIVE_DECAY
        )
        if _device_code_adaptive_interval < 0.05:
            _device_code_adaptive_interval = 0.0


def _device_code_min_interval() -> float:
    """Seconds between device/code starts.

    Explicit env GROK_DEVICE_CODE_MIN_INTERVAL wins.
    Else adaptive (0 until 429, then steps up / decays on success).
    """
    import os

    raw = (os.environ.get("GROK_DEVICE_CODE_MIN_INTERVAL") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    # Read without re-entering _DEVICE_CODE_LOCK (device.py holds it while spacing).
    return float(_device_code_adaptive_interval or 0.0)

def _cpa_tools_import(name: str) -> Any:
    """Load in-tree grokreg.backends.export.xai_pack.<name> (xAI pack schema/writer)."""
    if name in {"schema", "writer"}:
        return __import__(f"grokreg.backends.export.xai_pack.{name}", fromlist=[name])
    # legacy probe etc. optional — prefer in-tree implementations below
    raise ImportError(
        f"cpa helper {name!r} is not vendored; use grokreg.backends.export.xai_pack.schema/writer only"
    )

def set_sso(session: Any, jwt: str) -> None:
    domains = ("accounts.x.ai", ".x.ai", "auth.x.ai", ".grok.com", "grok.com")
    for d in domains:
        for k in ("sso", "sso-rw"):
            try:
                session.cookies.set(k, jwt, domain=d)
            except Exception:
                pass

