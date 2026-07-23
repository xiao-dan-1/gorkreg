"""--summary: auth.json is the only inventory source."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_auth_pool(path: Path, n_fresh: int = 2, n_needs: int = 1, n_expired: int = 1) -> None:
    """Minimal auth.json entries with controllable exp."""
    now = time.time()
    data = {}
    i = 0
    for _ in range(n_fresh):
        i += 1
        email = f"fresh{i}@bj01.xdauv.xyz"
        key = f"https://auth.x.ai::fresh-{i}"
        data[key] = {
            "email": email,
            "access_token": f"at-{i}",
            "refresh_token": f"rt-{i}",
            "expires_at": now + 6 * 3600,
            "type": "xai",
            "disabled": False,
        }
    for _ in range(n_needs):
        i += 1
        email = f"needs{i}@outlook.com"
        key = f"https://auth.x.ai::needs-{i}"
        data[key] = {
            "email": email,
            "access_token": f"at-{i}",
            "refresh_token": f"rt-{i}",
            "expires_at": now + 60,  # within default skew
            "type": "xai",
            "disabled": False,
        }
    for _ in range(n_expired):
        i += 1
        email = f"exp{i}@bj01.xdauv.xyz"
        key = f"https://auth.x.ai::exp-{i}"
        data[key] = {
            "email": email,
            "access_token": f"at-{i}",
            "refresh_token": f"rt-{i}",
            "expires_at": now - 3600,
            "type": "xai",
            "disabled": False,
        }
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def auth_tree(tmp_path: Path, monkeypatch):
    root = tmp_path
    auth = root / "auth.json"
    _make_auth_pool(auth, n_fresh=2, n_needs=1, n_expired=1)
    # distractors that must NOT affect --summary
    (root / "sso_roster.txt").write_text(
        "ghost@bj01.xdauv.xyz----pw----sso\n", encoding="utf-8"
    )
    (root / "output" / "accounts").mkdir(parents=True)
    (root / "output" / "accounts" / "account_ghost.json").write_text(
        json.dumps({"email": "ghost2@x.com", "sso": "x", "error": None}),
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    cfg = {"_root": str(root)}
    return root, cfg, auth


def test_summary_only_auth_json(auth_tree, capsys):
    root, cfg, auth = auth_tree
    from grokreg.ops.summary_cmds import _cmd_summary

    rc = _cmd_summary(
        cfg,
        SimpleNamespace(
            summary_domain=None,
            summary_full=False,
            summary_limit=30,
            auth_file=None,
            skew_min=5.0,
        ),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "source=auth.json" in out or "auth.json" in out
    # must NOT claim roster as inventory metrics
    assert "roster_pass" not in out
    assert "ghost@bj01.xdauv.xyz" not in out
    # totals from auth only: 2 fresh + 1 needs + 1 expired = 4
    assert "total" in out.lower()
    assert "| total" in out or "total=4" in out or "       4" in out
    assert (root / "output" / "summary.csv").is_file()
    csv = (root / "output" / "summary.csv").read_text(encoding="utf-8")
    assert "fresh1@bj01.xdauv.xyz" in csv
    assert "ghost@bj01.xdauv.xyz" not in csv


def test_summary_missing_auth_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = {"_root": str(tmp_path)}
    from grokreg.ops.summary_cmds import _cmd_summary

    rc = _cmd_summary(
        cfg,
        SimpleNamespace(
            summary_domain=None,
            summary_full=False,
            summary_limit=30,
            auth_file=None,
            skew_min=5.0,
        ),
    )
    assert rc == 2
    out = capsys.readouterr().out + capsys.readouterr().err
    # error may go to logging; exit code is enough


def test_summary_domain_filter_auth(auth_tree, capsys):
    _, cfg, _ = auth_tree
    from grokreg.ops.summary_cmds import _cmd_summary

    rc = _cmd_summary(
        cfg,
        SimpleNamespace(
            summary_domain="outlook.com",
            summary_full=True,
            summary_limit=30,
            auth_file=None,
            skew_min=5.0,
        ),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "outlook.com" in out
    assert "needs" in out.lower() or "1" in out


def test_summary_error_bucket_still_collapses_wire():
    """Keep bucket hygiene for other tools; summary itself is auth-only."""
    from grokreg.ops.ledger_ops import summary_error_bucket

    assert summary_error_bucket("exception:unsupported wire type 4 at offset 1") == "grpc_parse"
