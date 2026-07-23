"""Register batch timing recorder — wall/stage costs + top bottleneck factors.

Writes:
  output/bench_runs.jsonl   one JSON object per batch (machine-readable)
  output/bench_summary.md   append-only human table for optimization notes
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Stage order used for share / bottleneck ranking
STAGE_KEYS = (
    "scrape",
    "create_code",
    "wait_code",
    "verify",
    "validate_password",
    "turnstile",
    "create_account",
    "sso",
)


def _repo_output() -> Path:
    return Path("output")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def top_factor(timings: dict[str, Any] | None) -> tuple[str | None, float]:
    """Return (stage_name, seconds) with the largest stage cost."""
    if not timings:
        return None, 0.0
    best_k: str | None = None
    best_v = 0.0
    for k in STAGE_KEYS:
        try:
            v = float(timings.get(k) or 0)
        except (TypeError, ValueError):
            continue
        if v > best_v:
            best_v = v
            best_k = k
    # also consider any other numeric keys
    for k, raw in timings.items():
        if k in STAGE_KEYS:
            continue
        try:
            v = float(raw or 0)
        except (TypeError, ValueError):
            continue
        if v > best_v:
            best_v = v
            best_k = str(k)
    return best_k, round(best_v, 3)


def factor_label(stage: str | None, elapsed: float, error_code: str | None) -> str:
    """Human bottleneck label for one account."""
    if error_code:
        ec = str(error_code).lower()
        if "mail_timeout" in ec or ec == "mail":
            return "mail_timeout(投递/等码)"
        if "mail_auth" in ec:
            return "mail_auth(邮箱凭证)"
        if "sso" in ec:
            return "sso_failed"
        if "create" in ec:
            return f"create({error_code})"
        if "captcha" in ec or "turnstile" in ec:
            return "captcha"
        return f"fail:{error_code}"
    if not stage:
        return "unknown"
    mapping = {
        "turnstile": "turnstile(打码)",
        "create_account": "create_account(含short-body重试)",
        "wait_code": "wait_code(收码)",
        "scrape": "scrape",
        "sso": "sso",
        "create_code": "create_code",
        "verify": "verify",
        "validate_password": "validate_password",
    }
    return mapping.get(stage, stage)


def build_run_record(
    *,
    results: list[dict],
    jobs: int,
    mail_backend: str,
    mail_api_url: str | None = None,
    wall_sec: float,
    ok: int,
    fail: int,
    skipped: int = 0,
    batch_file: str | None = None,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    accounts: list[dict[str, Any]] = []
    factor_counts: dict[str, int] = {}
    stage_sums: dict[str, list[float]] = {k: [] for k in STAGE_KEYS}
    ok_elapsed: list[float] = []
    fail_elapsed: list[float] = []

    for r in results:
        timings = dict(r.get("timings_sec") or {})
        elapsed = float(r.get("elapsed_sec") or 0)
        is_ok = bool(r.get("sso")) and not r.get("error")
        err_code = r.get("error_code") or (None if is_ok else "error")
        stage, stage_sec = top_factor(timings if is_ok or timings else None)
        # for fails with little timing, use error as factor
        label = factor_label(stage if is_ok else None, elapsed, None if is_ok else str(err_code or r.get("error") or "fail"))
        if not is_ok and timings:
            # still report largest measured stage + fail code
            s2, sec2 = top_factor(timings)
            if s2 and sec2 >= 5:
                label = f"{factor_label(None, elapsed, str(err_code))} +max={s2}:{sec2:.1f}s"
            stage, stage_sec = s2, sec2
        factor_counts[label] = factor_counts.get(label, 0) + 1
        for k in STAGE_KEYS:
            if k in timings and timings[k] is not None:
                try:
                    stage_sums[k].append(float(timings[k]))
                except (TypeError, ValueError):
                    pass
        if is_ok:
            ok_elapsed.append(elapsed)
        else:
            fail_elapsed.append(elapsed)
        short_hits = int(r.get("short_body_hits") or 0)
        create_attempts = int(r.get("create_attempts") or 0)
        create_body_len = r.get("create_body_len")
        accounts.append(
            {
                "email": r.get("email"),
                "ok": is_ok,
                "elapsed_sec": round(elapsed, 3),
                "error_code": r.get("error_code"),
                "error": (str(r.get("error") or "")[:160] or None),
                "timings_sec": {k: timings[k] for k in STAGE_KEYS if k in timings},
                "top_stage": stage,
                "top_sec": stage_sec,
                "factor": label,
                "proxy_sid": r.get("proxy_sid") or "",
                "short_body_hits": short_hits,
                "create_attempts": create_attempts,
                "create_body_len": create_body_len,
            }
        )

    stage_mean = {
        k: round(statistics.mean(vs), 3) if vs else None for k, vs in stage_sums.items()
    }
    stage_sum = {k: round(sum(vs), 3) for k, vs in stage_sums.items() if vs}
    total_stage = sum(stage_sum.values()) or 1.0
    stage_share = {k: round(100.0 * v / total_stage, 1) for k, v in stage_sum.items()}

    # batch-level primary factor:
    # - OK runs: largest stage *sum* among successful accounts (stable; not mode(top_stage))
    # - pure fail: fail factor label / buckets
    ok_stage_sum: dict[str, float] = {}
    for a in accounts:
        if not a.get("ok"):
            continue
        for k, raw in (a.get("timings_sec") or {}).items():
            try:
                v = float(raw or 0)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            ok_stage_sum[k] = ok_stage_sum.get(k, 0.0) + v
    if ok_stage_sum:
        primary = max(ok_stage_sum, key=ok_stage_sum.get)
        primary_label = factor_label(primary, 0, None)
    elif fail:
        primary_label = max(factor_counts, key=factor_counts.get) if factor_counts else "fail"
    else:
        primary_label = "n/a"

    short_hits_total = sum(int(a.get("short_body_hits") or 0) for a in accounts)
    short_accounts = sum(1 for a in accounts if int(a.get("short_body_hits") or 0) > 0)
    create_attempts_total = sum(int(a.get("create_attempts") or 0) for a in accounts)
    fail_buckets: dict[str, int] = {}
    for a in accounts:
        if a.get("ok"):
            continue
        code = str(a.get("error_code") or "error")
        fail_buckets[code] = fail_buckets.get(code, 0) + 1

    rec: dict[str, Any] = {
        "ts": _now_iso(),
        "batch_file": batch_file,
        "jobs": int(jobs),
        "mail_backend": (mail_backend or "").strip() or "?",
        "mail_api_url": mail_api_url or "",
        "wall_sec": round(float(wall_sec), 3),
        "ok": int(ok),
        "fail": int(fail),
        "skipped": int(skipped),
        "total": int(ok) + int(fail),
        "success_rate": round(100.0 * ok / max(ok + fail, 1), 1),
        "wall_per_ok_sec": round(float(wall_sec) / ok, 1) if ok else None,
        "ok_elapsed_mean": round(statistics.mean(ok_elapsed), 2) if ok_elapsed else None,
        "ok_elapsed_median": round(statistics.median(ok_elapsed), 2) if ok_elapsed else None,
        "ok_elapsed_p90": (
            round(sorted(ok_elapsed)[max(0, int(round(0.9 * (len(ok_elapsed) - 1))))], 2)
            if ok_elapsed
            else None
        ),
        "fail_elapsed_sum": round(sum(fail_elapsed), 2) if fail_elapsed else 0.0,
        "fail_elapsed_mean": round(statistics.mean(fail_elapsed), 2) if fail_elapsed else None,
        "stage_mean_sec": stage_mean,
        "stage_share_pct": stage_share,
        "primary_factor": primary_label,
        "factor_counts": factor_counts,
        "fail_buckets": fail_buckets,
        "short_body_hits": short_hits_total,
        "short_body_accounts": short_accounts,
        "short_body_rate": round(100.0 * short_accounts / max(len(accounts), 1), 1),
        "create_attempts_total": create_attempts_total,
        "accounts": accounts,
        "note": note or "",
    }
    if extra:
        rec["extra"] = extra
    return rec


def record_path_jsonl() -> Path:
    return _repo_output() / "bench_runs.jsonl"


def record_path_md() -> Path:
    return _repo_output() / "bench_summary.md"


def append_run(rec: dict[str, Any]) -> tuple[Path, Path]:
    """Persist one run to jsonl + markdown summary. Returns paths."""
    out = _repo_output()
    out.mkdir(parents=True, exist_ok=True)
    jpath = record_path_jsonl()
    mpath = record_path_md()

    with jpath.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # markdown row
    if not mpath.exists():
        mpath.write_text(
            "# 注册测试耗时台账\n\n"
            "自动追加。用于对照 jobs / mail_backend / 瓶颈阶段，优化流程。\n\n"
            "| 时间 | jobs | backend | ok/fail | rate% | 墙钟s | 每成功s | OK均s | short% | 主瓶颈 | 备注 |\n"
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|\n",
            encoding="utf-8",
        )
    ts = rec.get("ts") or ""
    row = (
        f"| {ts} | {rec.get('jobs')} | {rec.get('mail_backend')} | "
        f"{rec.get('ok')}/{rec.get('fail')} | {rec.get('success_rate')} | {rec.get('wall_sec')} | "
        f"{rec.get('wall_per_ok_sec') if rec.get('wall_per_ok_sec') is not None else '-'} | "
        f"{rec.get('ok_elapsed_mean') if rec.get('ok_elapsed_mean') is not None else '-'} | "
        f"{rec.get('short_body_rate') if rec.get('short_body_rate') is not None else '-'} | "
        f"{rec.get('primary_factor')} | {(rec.get('note') or rec.get('batch_file') or '')[:40]} |\n"
    )
    # detail block
    detail_lines = [
        f"\n### {ts}  jobs={rec.get('jobs')} backend={rec.get('mail_backend')} "
        f"wall={rec.get('wall_sec')}s  primary={rec.get('primary_factor')}\n",
        f"- batch: `{rec.get('batch_file') or '-'}`\n",
        f"- success_rate={rec.get('success_rate')}%  short_rate={rec.get('short_body_rate')}% "
        f"short_hits={rec.get('short_body_hits')}  factors={rec.get('factor_counts')}\n",
        f"- fail_buckets: {rec.get('fail_buckets')}\n",
        f"- stage_mean: { {k:v for k,v in (rec.get('stage_mean_sec') or {}).items() if v} }\n",
        f"- stage_share%: {rec.get('stage_share_pct')}\n",
        "\n| email | ok | elapsed | top_stage | top_s | short | factor |\n"
        "|---|---|---:|---|---:|---:|---|\n",
    ]
    for a in rec.get("accounts") or []:
        em = (a.get("email") or "?")
        short = em.split("@")[0] if "@" in em else em
        detail_lines.append(
            f"| {short} | {a.get('ok')} | {a.get('elapsed_sec')} | "
            f"{a.get('top_stage') or '-'} | {a.get('top_sec') or 0} | "
            f"{a.get('short_body_hits') or 0} | {a.get('factor')} |\n"
        )

    with mpath.open("a", encoding="utf-8") as f:
        f.write(row)
        f.writelines(detail_lines)

    return jpath, mpath


def record_batch(
    *,
    results: list[dict],
    jobs: int,
    mail_backend: str,
    mail_api_url: str | None = None,
    wall_sec: float,
    ok: int,
    fail: int,
    skipped: int = 0,
    batch_file: str | None = None,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build + append. Returns the record (never raises to caller if write fails)."""
    rec = build_run_record(
        results=results,
        jobs=jobs,
        mail_backend=mail_backend,
        mail_api_url=mail_api_url,
        wall_sec=wall_sec,
        ok=ok,
        fail=fail,
        skipped=skipped,
        batch_file=batch_file,
        note=note,
        extra=extra,
    )
    try:
        append_run(rec)
    except Exception as e:  # noqa: BLE001 — bench must not break batch
        import logging

        logging.warning("bench record failed: %s", e)
    return rec


# ---- log backfill (one-shot / CLI) ----

_ITEM_RE = re.compile(
    r"\[#(\d+)\] (OK|FAIL) (\S+@\S+) ([\d.]+)s"
)
_TIMING_RE = re.compile(
    r"总耗时 ([\d.]+)s \| ([^\n]+)"
)
_BATCH_DONE_RE = re.compile(
    r"批量完成: ok=(\d+) fail=(\d+) skipped=(\d+) total=(\d+) 耗时=([\d.]+)s"
)
_BACKEND_RE = re.compile(r"backend=(imap|graph)")
_JOBS_RE = re.compile(r"批量开始 count=\d+ jobs=(\d+)")


def parse_batch_log(text: str, *, source: str = "") -> dict[str, Any] | None:
    """Best-effort parse of a tee'd batch log into a run record."""
    done = _BATCH_DONE_RE.search(text)
    if not done:
        return None
    ok, fail, skipped, total = map(int, done.group(1, 2, 3, 4))
    wall = float(done.group(5))
    jobs_m = _JOBS_RE.search(text)
    jobs = int(jobs_m.group(1)) if jobs_m else 1
    backends = _BACKEND_RE.findall(text)
    backend = backends[-1] if backends else "?"

    # pair timings with accounts by order of 总耗时 then OK/FAIL lines
    timings_list: list[dict[str, float]] = []
    for m in _TIMING_RE.finditer(text):
        total_s = float(m.group(1))
        parts = {"_total": total_s}
        for kv in m.group(2).split():
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            try:
                parts[k] = float(v.rstrip("s"))
            except ValueError:
                pass
        timings_list.append(parts)

    items = _ITEM_RE.findall(text)
    # summary FAIL/OK lines at end are more complete for error codes
    summary_items: list[tuple[str, str, float, str]] = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(
            r"(OK|FAIL)\s+(\S+@\S+)\s+([\d.]+)s\s+(.*)$",
            line,
        )
        if m:
            summary_items.append(
                (m.group(1), m.group(2), float(m.group(3)), m.group(4).strip())
            )

    results: list[dict] = []
    # prefer summary order; fall back to [#n] lines
    if summary_items:
        for i, (st, em, sec, err) in enumerate(summary_items):
            is_ok = st == "OK"
            timings = timings_list[i] if i < len(timings_list) else {}
            # strip trailing [code]
            code = None
            emsg = err if not is_ok else None
            cm = re.search(r"\[([a-z0-9_]+)\]\s*$", err or "", re.I)
            if cm:
                code = cm.group(1)
            results.append(
                {
                    "email": em,
                    "sso": "x" if is_ok else None,
                    "error": emsg if not is_ok else None,
                    "error_code": code if not is_ok else None,
                    "elapsed_sec": sec,
                    "timings_sec": {k: v for k, v in timings.items() if k != "_total"},
                }
            )
    else:
        for i, (_n, st, em, sec) in enumerate(items):
            is_ok = st == "OK"
            timings = timings_list[i] if i < len(timings_list) else {}
            results.append(
                {
                    "email": em,
                    "sso": "x" if is_ok else None,
                    "error": None if is_ok else "unknown",
                    "elapsed_sec": float(sec),
                    "timings_sec": {k: v for k, v in timings.items() if k != "_total"},
                }
            )

    return build_run_record(
        results=results,
        jobs=jobs,
        mail_backend=backend,
        wall_sec=wall,
        ok=ok,
        fail=fail,
        skipped=skipped,
        batch_file=source,
        note=f"backfill:{Path(source).name}" if source else "backfill",
    )


def backfill_from_logs(log_paths: list[Path], *, skip_existing: bool = True) -> int:
    """Parse logs and append records. Returns number of new records."""
    jpath = record_path_jsonl()
    seen: set[str] = set()
    if skip_existing and jpath.exists():
        for line in jpath.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                o = json.loads(line)
                if o.get("batch_file"):
                    seen.add(str(o["batch_file"]))
                if o.get("note"):
                    seen.add(str(o["note"]))
            except json.JSONDecodeError:
                continue
    n = 0
    for p in log_paths:
        key = str(p).replace("\\", "/")
        note_key = f"backfill:{p.name}"
        if skip_existing and (key in seen or note_key in seen or p.name in seen):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        rec = parse_batch_log(text, source=key)
        if not rec:
            continue
        append_run(rec)
        n += 1
    return n
