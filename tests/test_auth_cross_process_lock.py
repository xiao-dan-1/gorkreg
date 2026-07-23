"""Cross-process auth.json lock: concurrent CLI processes must not clobber."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _worker_script(auth: Path, email: str, token: str, sleep_s: float) -> str:
    # Hold lock longer by sleeping inside a custom critical section via upsert path
    return f"""
import time
from pathlib import Path
from grokreg.auth_pool import upsert, load_pool, _AuthFileLock, load_pool as lp

auth = Path(r\"{auth}\")
email = {email!r}
token = {token!r}
# occupy lock briefly so sibling process waits
with _AuthFileLock(auth):
    time.sleep({sleep_s})
    data = lp(auth)
upsert(auth, {{
    "email": email,
    "access_token": token,
    "refresh_token": "rt-" + email,
    "expires_at": time.time() + 3600,
    "type": "xai",
    "auth_kind": "oauth",
}})
print("done", email)
"""


def test_cross_process_upsert_both_survive(tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")

    s1 = _worker_script(auth, "a@example.com", "tok-a", 0.4)
    s2 = _worker_script(auth, "b@example.com", "tok-b", 0.05)

    p1 = subprocess.Popen(
        [sys.executable, "-c", s1],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.1)  # let p1 grab lock first
    p2 = subprocess.Popen(
        [sys.executable, "-c", s2],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out1, err1 = p1.communicate(timeout=30)
    out2, err2 = p2.communicate(timeout=30)
    assert p1.returncode == 0, err1
    assert p2.returncode == 0, err2

    from grokreg.auth_pool import list_entries

    rows = list_entries(auth, include_disabled=True, include_expired=True)
    emails = {(e.get("email") or "").lower() for _, e in rows}
    assert "a@example.com" in emails
    assert "b@example.com" in emails
    # both tokens present
    by_email = {(e.get("email") or "").lower(): e for _, e in rows}
    assert by_email["a@example.com"].get("access_token") == "tok-a"
    assert by_email["b@example.com"].get("access_token") == "tok-b"


def test_auth_file_lock_timeout(tmp_path: Path, monkeypatch):
    """Busy lock file (another holder) times out — not same-thread re-entry."""
    from grokreg import auth_pool as ap

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    # Force every non-nested acquire to fail (simulate other process)
    monkeypatch.setattr(ap, "_try_acquire_file_lock", lambda lock_file: None)
    try:
        with ap._AuthFileLock(auth, timeout=0.2):
            raise AssertionError("should not acquire")
    except ap.AuthPoolError as e:
        assert "timeout" in str(e).lower()
