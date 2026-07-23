"""Round-based registration experiments (e.g. j10 x 10 per round).

Design notes (load-test style):
- One independent variable per series (default: jobs=10, n=10/round).
- Fixed controls: mail_backend=graph, receive-code direct, retry_failed=0.
- Each round is a fixed-size sample; successive rounds measure stability drift.
- Metrics: success_rate, wall, per_ok, stage means, short_body_rate, fail_buckets.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_ROUND_SIZE = 10
DEFAULT_JOBS = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _repo_output() -> Path:
    return Path("output")


def experiment_dir(name: str = "j10x10") -> Path:
    d = _repo_output() / "experiments" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_mail_lines(
    mails_path: Path | None = None,
    *,
    sources: list[Path] | list[str] | None = None,
    sso_roster: Path = Path("sso_roster.txt"),
    use_marks: bool = True,
    exp_name: str | None = None,
    cfg: dict | None = None,
) -> list[str]:
    """Return unused mail lines suitable for batch registration.

    Multi-source via mail_pool (cfg mail.sources / GROK_MAIL_SOURCES / mails.txt).
    If exp_name is set, also exclude emails already claimed by this experiment.
    """
    from .mail_pool import free_mail_lines as _pool_free

    return _pool_free(
        sources,
        mails_path=mails_path,
        sso_roster=sso_roster,
        use_marks=use_marks,
        exp_name=exp_name,
        cfg=cfg,
    )


def claimed_emails(exp_name: str) -> set[str]:
    """Emails already assigned to any round batch in this experiment."""
    d = experiment_dir(exp_name)
    out: set[str] = set()
    claim_file = d / "claimed_emails.txt"
    if claim_file.is_file():
        for line in claim_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            em = line.strip().lower()
            if em and "@" in em:
                out.add(em)
    # also scan batch files (source of truth)
    for p in sorted(d.glob("round_*_batch.txt")):
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            em = t.split("----", 1)[0].strip().lower()
            if "@" in em:
                out.add(em)
    return out


def _append_claimed(exp_name: str, emails: list[str]) -> None:
    path = experiment_dir(exp_name) / "claimed_emails.txt"
    existing = claimed_emails(exp_name)
    with path.open("a", encoding="utf-8") as f:
        for em in emails:
            e = em.strip().lower()
            if e and e not in existing:
                f.write(e + "\n")
                existing.add(e)


def take_round_batch(
    free_lines: list[str],
    *,
    round_no: int,
    n: int = DEFAULT_ROUND_SIZE,
    exp_name: str = "j10x10",
) -> Path:
    """Write next N free mails into a round batch file. Mutates free_lines via slice only."""
    if len(free_lines) < n:
        raise ValueError(f"free mails {len(free_lines)} < round size {n}")
    chunk = free_lines[:n]
    out = experiment_dir(exp_name) / f"round_{round_no:02d}_batch.txt"
    out.write_text("\n".join(chunk) + "\n", encoding="utf-8")
    emails = [c.split("----", 1)[0].strip() for c in chunk]
    _append_claimed(exp_name, emails)
    return out


def append_round_record(rec: dict[str, Any], *, exp_name: str = "j10x10") -> Path:
    path = experiment_dir(exp_name) / "rounds.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def load_rounds(exp_name: str = "j10x10") -> list[dict[str, Any]]:
    path = experiment_dir(exp_name) / "rounds.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize_experiment(exp_name: str = "j10x10") -> dict[str, Any]:
    rows = load_rounds(exp_name)
    if not rows:
        return {"exp": exp_name, "rounds": 0}
    rates = [float(r.get("success_rate") or 0) for r in rows]
    walls = [float(r.get("wall_sec") or 0) for r in rows]
    per_oks = [float(r["wall_per_ok_sec"]) for r in rows if r.get("wall_per_ok_sec") is not None]
    shorts = [float(r.get("short_body_rate") or 0) for r in rows]
    ok_sum = sum(int(r.get("ok") or 0) for r in rows)
    fail_sum = sum(int(r.get("fail") or 0) for r in rows)
    buckets: dict[str, int] = {}
    for r in rows:
        for k, v in (r.get("fail_buckets") or {}).items():
            buckets[k] = buckets.get(k, 0) + int(v)
    summary = {
        "exp": exp_name,
        "rounds": len(rows),
        "accounts_ok": ok_sum,
        "accounts_fail": fail_sum,
        "success_rate_overall": round(100.0 * ok_sum / max(ok_sum + fail_sum, 1), 1),
        "success_rate_mean": round(statistics.mean(rates), 1) if rates else None,
        "success_rate_stdev": round(statistics.pstdev(rates), 1) if len(rates) >= 2 else 0.0,
        "wall_mean": round(statistics.mean(walls), 1) if walls else None,
        "wall_median": round(statistics.median(walls), 1) if walls else None,
        "per_ok_mean": round(statistics.mean(per_oks), 1) if per_oks else None,
        "short_rate_mean": round(statistics.mean(shorts), 1) if shorts else None,
        "fail_buckets": buckets,
        "rounds_detail": [
            {
                "round": r.get("round"),
                "ok": r.get("ok"),
                "fail": r.get("fail"),
                "rate": r.get("success_rate"),
                "wall": r.get("wall_sec"),
                "per_ok": r.get("wall_per_ok_sec"),
                "short%": r.get("short_body_rate"),
                "primary": r.get("primary_factor"),
            }
            for r in rows
        ],
    }
    # human markdown
    md = experiment_dir(exp_name) / "summary.md"
    lines = [
        f"# Experiment `{exp_name}`\n\n",
        f"- rounds={summary['rounds']}  overall_ok={ok_sum}/{ok_sum+fail_sum} "
        f"({summary['success_rate_overall']}%)\n",
        f"- success_rate mean±stdev = {summary['success_rate_mean']}±{summary['success_rate_stdev']}\n",
        f"- wall mean/median = {summary['wall_mean']}/{summary['wall_median']}s\n",
        f"- per_ok mean = {summary['per_ok_mean']}s  short_rate mean = {summary['short_rate_mean']}%\n",
        f"- fail_buckets = {buckets}\n\n",
        "| round | ok/fail | rate% | wall | /ok | short% | primary |\n",
        "|---:|---:|---:|---:|---:|---:|---|\n",
    ]
    for d in summary["rounds_detail"]:
        lines.append(
            f"| {d.get('round')} | {d.get('ok')}/{d.get('fail')} | {d.get('rate')} | "
            f"{d.get('wall')} | {d.get('per_ok')} | {d.get('short%')} | {d.get('primary')} |\n"
        )
    md.write_text("".join(lines), encoding="utf-8")
    summary["summary_md"] = str(md)
    return summary


def print_round_terminal(rec: dict[str, Any]) -> None:
    """Compact terminal block after one experiment round."""
    print()
    print("=" * 64)
    print(
        f"ROUND {rec.get('round')}  j={rec.get('jobs')}  n={rec.get('total')}  "
        f"ok={rec.get('ok')}/{rec.get('total')}  rate={rec.get('success_rate')}%  "
        f"wall={rec.get('wall_sec')}s  /ok={rec.get('wall_per_ok_sec')}"
    )
    print(
        f"short_accounts={rec.get('short_body_accounts')} "
        f"short_rate={rec.get('short_body_rate')}%  "
        f"primary={rec.get('primary_factor')}"
    )
    if rec.get("fail_buckets"):
        print(f"fail_buckets={rec.get('fail_buckets')}")
    stage = {k: v for k, v in (rec.get("stage_mean_sec") or {}).items() if v}
    if stage:
        top = sorted(stage.items(), key=lambda kv: kv[1], reverse=True)[:4]
        print("stage_mean top:", " ".join(f"{k}={v:.1f}s" for k, v in top))
    print("=" * 64)


def next_round_number(exp_name: str = "j10x10") -> int:
    rows = load_rounds(exp_name)
    if not rows:
        return 1
    nums = [int(r.get("round") or 0) for r in rows]
    return max(nums) + 1


def build_round_record_from_bench(
    bench_rec: dict[str, Any],
    *,
    round_no: int,
    exp_name: str,
    jobs: int,
    note: str = "",
) -> dict[str, Any]:
    rec = {
        "ts": _now_iso(),
        "exp": exp_name,
        "round": int(round_no),
        "jobs": int(jobs),
        "mail_backend": bench_rec.get("mail_backend"),
        "batch_file": bench_rec.get("batch_file"),
        "ok": bench_rec.get("ok"),
        "fail": bench_rec.get("fail"),
        "total": bench_rec.get("total"),
        "success_rate": bench_rec.get("success_rate"),
        "wall_sec": bench_rec.get("wall_sec"),
        "wall_per_ok_sec": bench_rec.get("wall_per_ok_sec"),
        "ok_elapsed_mean": bench_rec.get("ok_elapsed_mean"),
        "ok_elapsed_median": bench_rec.get("ok_elapsed_median"),
        "ok_elapsed_p90": bench_rec.get("ok_elapsed_p90"),
        "stage_mean_sec": bench_rec.get("stage_mean_sec"),
        "stage_share_pct": bench_rec.get("stage_share_pct"),
        "primary_factor": bench_rec.get("primary_factor"),
        "factor_counts": bench_rec.get("factor_counts"),
        "fail_buckets": bench_rec.get("fail_buckets"),
        "short_body_hits": bench_rec.get("short_body_hits"),
        "short_body_accounts": bench_rec.get("short_body_accounts"),
        "short_body_rate": bench_rec.get("short_body_rate"),
        "note": note or bench_rec.get("note") or "",
    }
    return rec
