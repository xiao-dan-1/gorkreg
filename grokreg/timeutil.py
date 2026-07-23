"""UTC-first time helpers for mail/auth/CPA.

Convention
----------
- Epoch seconds (`time.time()`, JWT exp) are already absolute UTC.
- ISO strings with Z / ±offset use that offset.
- **Naive** datetimes / ``YYYY-MM-DD HH:MM:SS`` (no TZ) are treated as **UTC**
  wall clock — never local. CloudMail ``createTime`` and similar APIs need this.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso(*, timespec: str = "seconds") -> str:
    return utc_now().isoformat(timespec=timespec)


def utc_epoch() -> float:
    """Unix seconds (UTC). Alias of time.time() for call-site clarity."""
    return time.time()


def parse_to_epoch(value: Any) -> float | None:
    """Best-effort parse to unix seconds (UTC). None if unparseable.

    - int/float: seconds, or ms if > 1e12
    - digit str: same
    - ISO / space-separated datetime: Z and offsets honored; **naive → UTC**
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n <= 0:
            return None
        return n / 1000.0 if n > 1e12 else n
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.isdigit() or (s[0] == "-" and s[1:].isdigit()):
        try:
            n = int(s)
            if n <= 0:
                return None
            return n / 1000.0 if n > 1e12 else float(n)
        except Exception:
            return None
    # normalize space separator → T for fromisoformat
    ss = s.replace("Z", "+00:00")
    if " " in ss and "T" not in ss[:20]:
        ss = ss.replace(" ", "T", 1)
    # trim fractional seconds quirks if needed later
    try:
        dt = datetime.fromisoformat(ss)
    except Exception:
        # last resort: YYYY-MM-DDTHH:MM:SS only
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", s)
        if not m:
            return None
        try:
            dt = datetime.strptime(f"{m.group(1)}T{m.group(2)}", "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_to_epoch_or_zero(value: Any) -> float:
    return parse_to_epoch(value) or 0.0
