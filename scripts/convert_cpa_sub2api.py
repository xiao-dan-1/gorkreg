#!/usr/bin/env python3
"""CPA (xai-*.json) ↔ sub2api (grok-*.json / UI envelope) 双向转换.

不 mint / 不 upload / 不改 auth.json。纯文件格式互转。

用法（仓库根）:
  # CPA → sub2api（默认一号一信封，对齐 --export sub2api）
  python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o sub2api_export
  python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export/xai-foo@x.json -o sub2api_export

  # 合并成单个导入包（UI 一次导入多号）
  python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o sub2api_export --merge

  # sub2api → CPA
  python scripts/convert_cpa_sub2api.py sub-to-cpa sub2api_export -o cpa_export
  python scripts/convert_cpa_sub2api.py sub-to-cpa path/to/export.json -o cpa_export

  # 自动识别方向
  python scripts/convert_cpa_sub2api.py auto some_dir -o out_dir

  # 预览
  python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o /tmp/out --dry-run --limit 5
  python scripts/convert_cpa_sub2api.py sub-to-cpa sub2api_export -o /tmp/xai --only foo@bar
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grokreg.backends.export.convert import convert_paths  # noqa: E402
from grokreg.errors import ConfigError  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert between CPA (CLIProxy xai-*.json) and sub2api (UI envelope) packs",
    )
    p.add_argument(
        "direction",
        choices=["cpa-to-sub", "sub-to-cpa", "auto", "to-sub", "to-cpa"],
        help="cpa-to-sub | sub-to-cpa | auto  (to-sub/to-cpa 是别名)",
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="输入文件或目录（目录递归 *.json）",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        required=True,
        help="输出目录",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计/预览路径，不写盘",
    )
    p.add_argument(
        "--only",
        default=None,
        help="只转换邮箱包含该子串的号",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理 N 个输入文件（0=不限）",
    )
    p.add_argument(
        "--merge",
        action="store_true",
        help="cpa-to-sub: 合并为一个 grok-merged-export.json 信封",
    )
    p.add_argument(
        "--no-model-mapping",
        action="store_true",
        help="cpa-to-sub: 不写 credentials.model_mapping",
    )
    p.add_argument(
        "--platform",
        default=None,
        help="只处理该平台族：xai|grok|openai|codex|…（默认全处理；不支持的仍 skip）",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="遇到非 xai/grok（如 openai/codex）计 fail，而不是 skip",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="stdout 输出 stats JSON",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    direction = args.direction
    if direction == "to-sub":
        direction = "cpa-to-sub"
    elif direction == "to-cpa":
        direction = "sub-to-cpa"

    try:
        stats = convert_paths(
            direction,  # type: ignore[arg-type]
            args.paths,
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            only=args.only,
            limit=int(args.limit or 0),
            include_model_mapping=not args.no_model_mapping,
            merge_envelope=bool(args.merge),
            platform=args.platform,
            strict=bool(args.strict),
        )
    except ConfigError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1

    if args.as_json:
        # 不回显 token；written 仅路径
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(
            "convert"
            f" direction={stats['direction']}"
            f" inputs={stats['inputs']}"
            f" ok={stats['ok']}"
            f" skip={stats['skip']}"
            f" skip_unsupported={stats.get('skip_unsupported', 0)}"
            f" fail={stats['fail']}"
            f" dry_run={int(bool(stats['dry_run']))}"
            f" out={stats['out_dir']}"
        )
        if stats.get("providers_seen"):
            print(f"  providers_seen={stats['providers_seen']}")
        if stats.get("merged_accounts"):
            print(f"  merged_accounts={stats['merged_accounts']}")
        if (stats["fail"] or stats.get("skip_unsupported")) and stats.get("errors"):
            print("  notes (first 10):")
            for err in stats["errors"][:10]:
                print(f"    {err.get('path')}: {err.get('error')}")
        if stats.get("written") and (args.dry_run or stats["ok"] <= 20):
            print("  written:")
            for w in stats["written"][:20]:
                print(f"    {w}")
            if len(stats["written"]) > 20:
                print(f"    ... +{len(stats['written']) - 20} more")

    return 0 if stats["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
