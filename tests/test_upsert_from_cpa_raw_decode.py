"""upsert_from_cpa must tolerate historical double-write JSON (Extra data)."""
from __future__ import annotations

import json
from pathlib import Path

from grokreg.auth_pool import list_entries, upsert_from_cpa


def test_upsert_from_cpa_accepts_extra_data(tmp_path: Path):
    auth = tmp_path / "auth.json"
    cpa = tmp_path / "xai-extra@example.com.json"
    obj = {
        "email": "extra@example.com",
        "access_token": "at-dummy",
        "refresh_token": "rt-extra",
        "type": "xai",
    }
    # two JSON objects concatenated (historical corrupt write)
    blob = json.dumps(obj, ensure_ascii=False, indent=2) + "\n" + json.dumps(
        {"email": "junk"}, ensure_ascii=False
    ) + "\n"
    cpa.write_text(blob, encoding="utf-8")
    key = upsert_from_cpa(auth, cpa)
    assert key
    rows = list_entries(auth, include_disabled=True, include_expired=True)
    emails = {(e.get("email") or "").lower() for _, e in rows}
    assert "extra@example.com" in emails
    entry = next(
        e for _, e in rows if (e.get("email") or "").lower() == "extra@example.com"
    )
    assert entry.get("refresh_token") == "rt-extra"
