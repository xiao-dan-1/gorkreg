"""sso_roster append must be process-thread safe and de-dupe."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from grokreg.ops.ledger_ops import append_sso_roster as _append_sso_roster


def test_append_sso_roster_dedupe_and_lock(tmp_path: Path):
    cli = tmp_path / "sso_roster.txt"
    email = "locktest@example.com"
    result = {"email": email, "password": "pw", "sso": "sso-token-1"}

    def once():
        return _append_sso_roster(result, path=cli)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(once) for _ in range(20)]
        results = [f.result() for f in as_completed(futs)]

    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == 19
    lines = [ln for ln in cli.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].startswith(email + "----")
    assert "sso-token-1" in lines[0]


def test_append_sso_roster_skips_error_or_missing():
    assert _append_sso_roster({"email": "a@b.c", "sso": "x", "error": "fail"}) is False
    assert _append_sso_roster({"email": "", "sso": "x"}) is False
    assert _append_sso_roster({"email": "a@b.c", "sso": ""}) is False
