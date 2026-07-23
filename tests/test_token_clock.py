"""token_clock: single exp semantics for ledger entries."""
from __future__ import annotations

import time

from grokreg.token_clock import apply_token_expiry, parse_entry_exp, seconds_left


def test_parse_entry_exp_prefers_expires_at():
    entry = {
        "expires_at": 2_000_000_000,
        "expired": "2020-01-01T00:00:00Z",
        "access_token": "",
    }
    assert parse_entry_exp(entry) == 2_000_000_000


def test_apply_token_expiry_sets_all_fields():
    now = 1_700_000_000.0
    out = apply_token_expiry({}, exp_unix=now + 3600, expires_in=3600, now=now)
    assert out["expires_at"] == now + 3600
    assert out["expires_in"] == 3600
    assert isinstance(out["expired"], str) and "T" in out["expired"]
    assert seconds_left(out, now=now) == 3600
