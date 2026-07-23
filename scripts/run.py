#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LEGACY one-shot runner (prod_pipeline skeleton). 非日常主路径。

日常请用分步::

  python scripts/prod_cloudmail_batch.py -n N -j J --ascii-log
  python main.py --mint all --mint-missing --limit N --no-probe -j 1
  python main.py --export cpa          # 或 --export sub2api
  python main.py --cpa-upload all --cpa-missing -j 20

Status: 暂不开发（prod_pipeline SKELETON_ONLY）— 调用会明确拒绝执行完整流水线。
本文件仅保留 CLI 壳，避免旧脚本路径 404。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grokreg.ops.prod_pipeline import (  # noqa: E402
    Target,
    add_common_arguments,
    config_from_namespace,
    run_pipeline,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="run.py",
        description=(
            "LEGACY 一条龙（SKELETON_ONLY）。日常请用 prod_cloudmail_batch + main.py "
            "mint/export/upload。"
        ),
    )
    p.add_argument(
        "target",
        choices=["cpa", "sub2api", "sub", "s2a"],
        help="上传目标：cpa=CLIProxy；sub2api=远程 import（骨架不执行）",
    )
    add_common_arguments(p)
    p.add_argument(
        "--cpa-missing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="CPA：只上传远端没有的文件（默认 true）",
    )
    p.add_argument(
        "--on-exists",
        default="overwrite",
        choices=["overwrite", "skip", "update", "update-all", "create"],
        help="sub2api 碰撞策略（默认 overwrite）",
    )
    p.add_argument(
        "--mint-proxy",
        default=None,
        help="mint 代理（默认 MINT_PROXY / HTTPS_PROXY / LOCAL 7890）",
    )
    args = p.parse_args(argv)

    raw = str(args.target).strip().lower()
    if raw in {"sub", "s2a", "sub2api"}:
        target = Target.SUB2API
    else:
        target = Target.CPA

    cfg = config_from_namespace(args, target)
    if getattr(args, "mint_proxy", None):
        cfg.extra["mint_proxy"] = str(args.mint_proxy).strip()

    result = run_pipeline(cfg)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
