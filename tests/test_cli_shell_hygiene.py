"""cli.py shell hygiene after ops split."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "grokreg" / "cli.py"


def test_cli_no_long_blank_streak():
    lines = CLI.read_text(encoding="utf-8").splitlines()
    best = 0
    cur = 0
    for ln in lines:
        if not ln.strip():
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    # extraction scar must be cleaned (was ~65 blank lines)
    assert best <= 3, f"longest blank streak in cli.py is {best}"


def test_cli_reexports_ops_cmds():
    src = CLI.read_text(encoding="utf-8")
    assert "from .ops.mint_cmds import" in src
    assert "from .ops.credential_cmds import" in src
    assert "from .ops.register_cmds import" in src
