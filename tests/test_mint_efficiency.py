"""Mint efficiency reads task_runs / last mint task."""
from __future__ import annotations

import json
from pathlib import Path

from client.services import ROOT, _efficiency_stats


def test_efficiency_reads_task_runs_mint(tmp_path, monkeypatch):
    # write into real ROOT/output if possible — use function under test path
    p = ROOT / "output" / "task_runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": "test",
        "kind": "mint",
        "ok": 20,
        "fail": 0,
        "total": 20,
        "wall_sec": 12.9,
        "success_rate": 100.0,
        "thr": 1.55,
        "source": "test",
    }
    # append unique marker
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    eff = _efficiency_stats([])
    mint = eff["mint"]
    assert mint.get("n", 0) >= 20
    assert mint.get("ok_rate") is not None
    assert mint.get("last") is not None
