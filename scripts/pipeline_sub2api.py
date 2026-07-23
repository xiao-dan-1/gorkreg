#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LEGACY 一条龙入口（非日常主路径）。

日常分步::

  python scripts/prod_cloudmail_batch.py -n N -j 2 --ascii-log
  python main.py --mint all --mint-missing --limit N --no-probe -j 1 --ascii-log
  python main.py --export sub2api
  python main.py --sub2api-upload all

本脚本仅转发到 ``scripts/run.py sub2api``（prod_pipeline SKELETON_ONLY，暂不开发）。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(__file__).resolve().parent / "run.py"


def main(argv: list[str] | None = None) -> int:
    av = list(argv) if argv is not None else sys.argv[1:]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    g = runpy.run_path(str(RUN), run_name="__not_main__")
    return int(g["main"](["sub2api"] + av))


if __name__ == "__main__":
    raise SystemExit(main())
