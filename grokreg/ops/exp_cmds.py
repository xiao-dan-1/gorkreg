"""Experiment / bench CLI commands."""
from __future__ import annotations

import argparse
import json as _json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

from .mail_cmds import _parse_mail_sources_arg


def _cmd_bench_backfill() -> int:
    """Backfill bench_runs from existing batch logs under output/."""
    from ..bench import backfill_from_logs, record_path_jsonl, record_path_md

    out = Path("output")
    logs = sorted(out.glob("*batch*.log")) + sorted(out.glob("batch_*.log"))
    # unique preserve order
    seen = set()
    uniq = []
    for p in logs:
        k = str(p.resolve()) if p.exists() else str(p)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    n = backfill_from_logs(uniq, skip_existing=True)
    print(f"bench-backfill: new={n} logs_scanned={len(uniq)}")
    print(f"  jsonl={record_path_jsonl()}")
    print(f"  md={record_path_md()}")
    return 0


def _cmd_bench_show(limit: int = 15) -> int:
    """Print recent bench runs (compact)."""
    from ..bench import record_path_jsonl, record_path_md
    import json as _json

    jpath = record_path_jsonl()
    if not jpath.is_file():
        print(f"no bench yet: {jpath}  (run batch or --bench-backfill)")
        return 0
    rows = []
    for line in jpath.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(_json.loads(line))
        except Exception:
            continue
    print(f"bench runs={len(rows)} path={jpath}")
    print(f"{'ts':28} {'j':>2} {'be':5} {'ok':>3} {'fail':>4} {'wall':>7} {'/ok':>6} primary")
    for r in rows[-limit:]:
        print(
            f"{str(r.get('ts') or '')[:28]:28} {r.get('jobs') or 0:2} {str(r.get('mail_backend') or '?'):5} "
            f"{r.get('ok') or 0:3} {r.get('fail') or 0:4} {float(r.get('wall_sec') or 0):7.1f} "
            f"{str(r.get('wall_per_ok_sec') if r.get('wall_per_ok_sec') is not None else '-'):>6} "
            f"{r.get('primary_factor')}"
        )
    print(f"md={record_path_md()}")
    return 0



def _cmd_exp_status(cfg: dict, args: argparse.Namespace) -> int:
    from ..experiment import free_mail_lines, load_rounds, next_round_number

    exp = getattr(args, "exp_name", None) or "j10x10"
    sources = _parse_mail_sources_arg(getattr(args, "mail_sources", None))
    free = free_mail_lines(sources=sources, exp_name=exp, cfg=cfg)
    rows = load_rounds(exp)
    nxt = next_round_number(exp)
    size = max(1, int(getattr(args, "exp_size", 10) or 10))
    print(f"exp={exp}")
    print(f"rounds_done={len(rows)}  next_round={nxt}")
    print(f"free_mails={len(free)}  round_size={size}  full_rounds_left={len(free)//size}")
    if rows:
        last = rows[-1]
        print(
            f"last: round={last.get('round')} ok={last.get('ok')}/{last.get('total')} "
            f"rate={last.get('success_rate')}% wall={last.get('wall_sec')}s "
            f"short%={last.get('short_body_rate')} primary={last.get('primary_factor')}"
        )
    return 0


def _cmd_exp_summary(args: argparse.Namespace) -> int:
    from ..experiment import summarize_experiment

    exp = getattr(args, "exp_name", None) or "j10x10"
    s = summarize_experiment(exp)
    if not s.get("rounds"):
        print(f"exp={exp} 尚无 rounds 数据")
        return 0
    print(
        f"exp={exp} rounds={s['rounds']} overall={s['accounts_ok']}/"
        f"{s['accounts_ok']+s['accounts_fail']} ({s['success_rate_overall']}%)"
    )
    print(
        f"success_rate mean±stdev={s['success_rate_mean']}±{s['success_rate_stdev']}  "
        f"wall mean/med={s['wall_mean']}/{s['wall_median']}  "
        f"per_ok={s['per_ok_mean']}  short%={s['short_rate_mean']}"
    )
    print(f"fail_buckets={s.get('fail_buckets')}")
    print(f"{'rd':>3} {'ok/fail':>8} {'rate':>6} {'wall':>7} {'/ok':>6} {'short%':>7} primary")
    for d in s.get("rounds_detail") or []:
        print(
            f"{d.get('round'):3} {str(d.get('ok'))+'/'+str(d.get('fail')):>8} "
            f"{d.get('rate'):6} {d.get('wall'):7} {str(d.get('per_ok')):>6} "
            f"{str(d.get('short%')):>7} {d.get('primary')}"
        )
    if s.get("summary_md"):
        print(f"wrote {s['summary_md']}")
    return 0


def _cmd_exp_round(cfg: dict, proxy_override: Optional[str], args: argparse.Namespace) -> int:
    """Take next N free mails and run one concurrent round; append experiment ledger."""
    from ..experiment import (
        append_round_record,
        build_round_record_from_bench,
        free_mail_lines,
        next_round_number,
        print_round_terminal,
        take_round_batch,
    )

    exp = getattr(args, "exp_name", None) or "j10x10"
    size = max(1, int(getattr(args, "exp_size", 10) or 10))
    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    # default j=10 when user didn't override (parser default is 1)
    if int(getattr(args, "jobs", 1) or 1) == 1 and not any(
        a in (sys.argv or []) for a in ("-j", "--jobs")
    ):
        jobs = 10
        args.jobs = 10

    sources = _parse_mail_sources_arg(getattr(args, "mail_sources", None))
    free = free_mail_lines(sources=sources, exp_name=exp, cfg=cfg)
    if len(free) < size:
        logging.error(
            "free mails=%s < exp-size=%s。请追加号池文件（mail.sources / --mail-sources）后再跑。",
            len(free),
            size,
        )
        print(f"exp-status hint: free={len(free)} need={size}")
        return 2

    round_no = next_round_number(exp)
    batch_path = take_round_batch(free, round_no=round_no, n=size, exp_name=exp)
    logging.info(
        "exp-round start exp=%s round=%s n=%s jobs=%s batch=%s free_left_after=%s",
        exp,
        round_no,
        size,
        jobs,
        batch_path,
        len(free) - size,
    )
    print(
        f"EXP ROUND {round_no}  exp={exp}  n={size}  j={jobs}  "
        f"backend={getattr(args, 'mail_backend', None) or 'graph'}  "
        f"retry={getattr(args, 'retry_failed', 0)}  file={batch_path}"
    )

    # force experiment-friendly defaults unless user set them
    if not any(a.startswith("--retry-failed") for a in (sys.argv or [])):
        args.retry_failed = 0
    args.batch = str(batch_path)
    args.skip_existing = True
    args.exp_note = f"exp={exp} round={round_no} j={jobs} n={size}"
    args.exp_round_no = round_no
    args.exp_name = exp

    from .register_cmds import _cmd_batch
    rc = _cmd_batch(cfg, proxy_override, args)
    rec = getattr(args, "_last_bench_rec", None)
    if not isinstance(rec, dict):
        logging.warning("exp-round: no bench record; skip rounds.jsonl")
        return rc

    round_rec = build_round_record_from_bench(
        rec,
        round_no=round_no,
        exp_name=exp,
        jobs=jobs,
        note=args.exp_note,
    )
    path = append_round_record(round_rec, exp_name=exp)
    print_round_terminal(round_rec)
    print(f"exp: wrote {path}")
    free_left = len(free_mail_lines(sources=sources, exp_name=exp, cfg=cfg))
    print(f"exp: free_left={free_left}  next=python main.py --exp-round -j {jobs} --exp-size {size}")
    return rc




