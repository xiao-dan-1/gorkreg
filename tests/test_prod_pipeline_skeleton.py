"""prod_pipeline stays skeleton / non-daily entry."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PP = ROOT / "grokreg" / "ops" / "prod_pipeline.py"
RUN = ROOT / "scripts" / "run.py"


def test_prod_pipeline_marked_skeleton():
    src = PP.read_text(encoding="utf-8")
    assert "暂不开发" in src or "skeleton" in src.lower()
    assert "SKELETON_ONLY" in src


def test_run_py_not_daily_path():
    src = RUN.read_text(encoding="utf-8")
    assert "暂不开发" in src
    assert "prod_cloudmail_batch" in src
