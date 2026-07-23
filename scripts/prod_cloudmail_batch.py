#!/usr/bin/env python3
"""CloudMail production mini-batch: same-process j x N.

- allocate catch-all → register_one (scrape cache ON by default)
- optional scrape prewarm (GET public page only — does NOT burn accounts)
- ThreadPool when -j > 1 (process-local scrape cache shared)
- save account_*.json via cli._save_result
- append sso_roster.txt on sso success
- write safe summary under output/prod_cloudmail/

仅注册批跑。完整一条龙（注册→mint→上传）请用:
  python scripts/run.py cpa|sub2api -n N -j J

Usage (from project root; no PYTHONPATH needed):
  python scripts/prod_cloudmail_batch.py -n 5
  python scripts/prod_cloudmail_batch.py -n 10 -j 5
  python scripts/prod_cloudmail_batch.py -n 5 --no-scrape-cache
  python scripts/prod_cloudmail_batch.py -n 10 -j 5 --no-scrape-prewarm
  python scripts/prod_cloudmail_batch.py -n 5 -j 2 --ascii-log
  python scripts/prod_cloudmail_batch.py -n 4 -j 2 --ascii-log --dry-run  # 只测日志，不注册
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# scripts/ 下直接 python 时，把仓库根加入 path（否则 No module named grokreg）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grokreg.config import ensure_dotenv, load_config, normalize_captcha_backend
from grokreg.mail_cloudmail import allocate_cloudmail_address
from grokreg.pipeline.register import RegisterOptions, register_one, result_ok
from grokreg.ops.ledger_ops import record_register_success, summary_error_bucket
from grokreg import scrape_cache
from grokreg import logutil

ensure_dotenv()

ROOT = _ROOT
OUT_SUM = ROOT / "output" / "prod_cloudmail"
OUT_SUM.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("prod_cloudmail")

_save_lock = threading.Lock()


def _record_prod_bench(
    *,
    rows: list,
    jobs: int,
    n: int,
    ok_n: int,
    wall_total: float,
    use_cache: bool,
    hits: int,
    misses: int,
    summary: dict | None = None,
    batch_file: str = "",
    dry_run: bool = False,
) -> None:
    """Write efficiency ledger (bench_runs.jsonl). Never raises to caller."""
    try:
        from grokreg.bench import record_batch

        bench_results: list[dict] = []
        for r in rows:
            # Prefer full timings_sec from register_one; fall back to stage columns.
            raw_timings = r.get("timings_sec")
            if isinstance(raw_timings, dict) and raw_timings:
                timings = {k: float(v) for k, v in raw_timings.items() if v is not None}
            else:
                timings = {}
                if r.get("scrape_s") is not None:
                    timings["scrape"] = r.get("scrape_s")
                if r.get("turnstile_s") is not None:
                    timings["turnstile"] = r.get("turnstile_s")
                if r.get("wait_code_s") is not None:
                    timings["wait_code"] = r.get("wait_code_s")
                if r.get("sso_s") is not None:
                    timings["sso"] = r.get("sso_s")
            ok_row = bool(r.get("ok"))
            bench_results.append(
                {
                    "email": r.get("email"),
                    "sso": "1" if ok_row else "",
                    "error": None if ok_row else (r.get("error") or "fail"),
                    "error_code": None if ok_row else (r.get("error") or "fail"),
                    "elapsed_sec": r.get("elapsed_s") or r.get("wall_s") or 0,
                    "timings_sec": timings,
                    "short_body_hits": r.get("short_body_hits") or 0,
                    "create_body_len": r.get("create_body_len"),
                    "create_attempts": r.get("create_attempts"),
                }
            )
        note = f"prod_cloudmail j={jobs} n={n} cache={int(bool(use_cache))}"
        if dry_run:
            note = "dry-run " + note
        rec = record_batch(
            results=bench_results,
            jobs=jobs,
            mail_backend="cloudmail",
            wall_sec=wall_total,
            ok=ok_n,
            fail=n - ok_n,
            batch_file=batch_file or "prod_cloudmail",
            note=note,
            extra={
                "source": "prod_cloudmail_batch",
                "dry_run": dry_run,
                "scrape_hit": hits,
                "scrape_miss": misses,
                "scrape_p50": (summary or {}).get("scrape_p50"),
                "elapsed_p50": (summary or {}).get("elapsed_p50"),
            },
        )
        print(
            f"bench: rate={rec.get('success_rate')}% wall={rec.get('wall_sec')}s "
            f"per_ok={rec.get('wall_per_ok_sec')} primary={rec.get('primary_factor')}"
        )
        print("bench: wrote output/bench_runs.jsonl + output/bench_summary.md")
    except Exception as exc:  # noqa: BLE001
        log.warning("bench record skipped: %s", exc)


def _prewarm_scrape(
    cfg: dict,
    *,
    proxy_override: str | None,
    ttl: float,
) -> dict[str, Any]:
    """One serial public-page scrape into process cache. No email/OTP/create.

    Fills next-action / router-tree / sitekey so concurrent workers can hit
    instead of all-missing on the first wave. Does NOT burn accounts.
    """
    from grokreg.client import GrokAuthClient
    from grokreg.proxyutil import resolve_proxy

    t0 = time.time()
    out: dict[str, Any] = {
        "ok": False,
        "sec": 0.0,
        "next_action": "",
        "sitekey": "",
        "error": None,
        "burns_account": False,
    }
    last_exc: Exception | None = None
    for attempt in range(1, 3):
        try:
            resolved = resolve_proxy(cfg, proxy_override)
            client = GrokAuthClient(cfg, session_url=resolved.session_url, debug=False)
            # force=True: always GET; use_cache=True: put into process cache after scrape
            info = client.load_signup_page(force=True, use_cache=True, cache_ttl=ttl)
            action = (info or {}).get("next_action") or getattr(client, "_next_action_id", None) or ""
            tree = getattr(client, "_next_router_state_tree", None) or ""
            sitekey = (info or {}).get("turnstile_sitekey") or client.turnstile_sitekey or ""
            # defensive put if client path skipped store
            if action and tree:
                scrape_cache.put(
                    next_action=str(action),
                    router_state_tree=str(tree),
                    turnstile_sitekey=str(sitekey),
                    ttl=ttl,
                )
                out["ok"] = True
            out["next_action"] = str(action)[:24]
            out["sitekey"] = str(sitekey)[:24]
            out["scrape_cache"] = (info or {}).get("scrape_cache")
            out["http_status"] = (info or {}).get("http_status")
            out["prewarm_attempts"] = attempt
            log.info(
                "scrape-prewarm ok=%s attempt=%s sec=%.3f action=%s… sitekey=%s burns_account=0",
                out["ok"],
                attempt,
                time.time() - t0,
                out["next_action"],
                out["sitekey"],
            )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "scrape-prewarm attempt=%s/2 failed (non-fatal): %s", attempt, exc
            )
            if attempt < 2:
                time.sleep(0.4 * attempt)
    if last_exc is not None and not out["ok"]:
        out["error"] = str(last_exc)[:200]
    out["sec"] = round(time.time() - t0, 3)
    return out



def _safe_row(
    result: dict, *, idx: int, wall_s: float, saved: str | None, cli_appended: bool
) -> dict:
    t = result.get("timings_sec") or {}
    return {
        "idx": idx,
        "email": (result.get("email") or "")[:80],
        "ok": bool(result_ok(result)),
        "scrape_cache": result.get("scrape_cache"),
        "scrape_s": t.get("scrape"),
        "turnstile_s": t.get("turnstile"),
        "wait_code_s": t.get("wait_code"),
        "sso_s": t.get("sso"),
        "elapsed_s": result.get("elapsed_sec"),
        "wall_s": round(wall_s, 3),
        "error": result.get("error"),
        "short_body_hits": result.get("short_body_hits"),
        "create_body_len": result.get("create_body_len"),
        "create_attempts": result.get("create_attempts"),
        "timings_sec": dict(t) if t else {},
        "captcha_prefetch": result.get("captcha_prefetch"),
        "captcha_prefetch_meta": result.get("captcha_prefetch_meta"),
        "register_attempts": result.get("register_attempts") or 1,
        "proxy_retried": bool(result.get("proxy_retried")),
        "account_json": saved,
        "sso_roster_appended": cli_appended,
    }



def _is_proxy_retryable(row: dict) -> bool:
    """True if fail looks like exit-proxy / network flake (worth 1 free retry)."""
    if row.get("ok"):
        return False
    err = str(row.get("error") or "").lower()
    if not err:
        return False
    # bucket from summary helper
    try:
        bucket = summary_error_bucket(row.get("error") or "")
    except Exception:
        bucket = ""
    if bucket == "proxy":
        return True
    needles = (
        "curl: (28)",
        "connection timed out",
        "timed out",
        "timeout",
        "proxy",
        "tunnel",
        "connect",
        "network",
        "request_error",
        "failed to perform",
        # gRPC-web dirty/truncated body through residential proxy (prod j6 saw 2/24)
        "wire type",
        "parse_error",
        "unsupported wire",
        "grpc_parse",
        "empty_body",
    )
    return any(n in err for n in needles)



# Cap ZERO_BALANCE process fuse: stop allocating new work after N consecutive
_ZERO_BALANCE_LOCK = threading.Lock()
_ZERO_BALANCE_STREAK = 0
_ZERO_BALANCE_FUSE = 1  # first captcha_zero_balance → refuse new accounts
_ZERO_BALANCE_TRIPPED = False


def _note_zero_balance_result(row: dict) -> None:
    global _ZERO_BALANCE_STREAK, _ZERO_BALANCE_TRIPPED
    err = str(row.get("error") or "") + " " + str(row.get("error_code") or "")
    low = err.lower()
    hit = (
        "zero_balance" in low
        or "error_zero_balance" in low
        or "captcha_zero_balance" in low
        or "captcha_balance" in low  # preflight balance=0
        or "balance=0" in low
        or "< min" in low
        or "余额不足" in err
        or "打码余额" in err
    )
    with _ZERO_BALANCE_LOCK:
        if hit:
            _ZERO_BALANCE_STREAK += 1
            if _ZERO_BALANCE_STREAK >= _ZERO_BALANCE_FUSE:
                _ZERO_BALANCE_TRIPPED = True
        else:
            if row.get("ok"):
                _ZERO_BALANCE_STREAK = 0


def _zero_balance_tripped() -> bool:
    with _ZERO_BALANCE_LOCK:
        return _ZERO_BALANCE_TRIPPED

def _one(
    *,
    idx: int,
    n: int,
    cfg: dict,
    proxy_override: str | None,
    use_cache: bool,
    ttl: float,
    jobs: int,
    verbose: bool = False,
    proxy_retries: int = 1,
) -> dict:
    if _zero_balance_tripped():
        return {
            "idx": idx,
            "email": "",
            "ok": False,
            "error": "captcha_zero_balance: batch fuse (Cap empty)",
            "error_code": "captcha_zero_balance",
            "scrape_cache": None,
            "scrape_s": None,
            "turnstile_s": None,
            "wait_code_s": None,
            "sso_s": None,
            "elapsed_s": None,
            "wall_s": None,
            "short_body_hits": None,
            "create_body_len": None,
            "account_json": None,
            "sso_roster_appended": False,
        }
    try:
        email = allocate_cloudmail_address(cfg)
    except Exception as exc:
        log.error("alloc failed i=%s: %s", idx, exc)
        return {
            "idx": idx,
            "email": "",
            "ok": False,
            "error": f"alloc:{exc}",
            "scrape_cache": None,
            "scrape_s": None,
            "turnstile_s": None,
            "wait_code_s": None,
            "sso_s": None,
            "elapsed_s": None,
            "wall_s": None,
            "short_body_hits": None,
            "create_body_len": None,
            "account_json": None,
            "sso_roster_appended": False,
        }

    captcha_backend = normalize_captcha_backend(
        str((cfg.get("captcha") or {}).get("backend") or "auto")
    )
    opts = RegisterOptions(
        mail_backend="cloudmail",
        captcha_backend=captcha_backend,
        verbose=bool(verbose),
        require_captcha_config=True,
        scrape_cache=use_cache,
        scrape_cache_ttl=ttl,
        create_short_body_retries=3,
        concurrent_jitter=(jobs > 1),
        index=idx,
    )
    log.debug(
        "=== prod cloudmail i=%s/%s email=%s cache=%s j=%s ===",
        idx,
        n,
        email,
        use_cache,
        jobs,
    )
    t1 = time.time()
    attempts = 0
    result: dict[str, Any] = {}
    while True:
        attempts += 1
        try:
            result = register_one(cfg, email, proxy_override, opts)
        except Exception as exc:
            err_s = f"exception:{exc}"
            # proxy/TLS timeouts: one-line only (Traceback floods j=12 logs)
            soft = _is_proxy_retryable({"ok": False, "error": str(exc)})
            zb = any(
                m in str(exc).upper()
                for m in ("ZERO_BALANCE", "ERROR_ZERO_BALANCE", "INSUFFICIENT")
            ) or "余额不足" in str(exc)
            if soft or zb:
                log.warning(
                    "register exception i=%s attempt=%s (%s, no traceback): %s",
                    idx,
                    attempts,
                    "zero_balance" if zb else "proxy/net",
                    str(exc)[:200],
                )
            else:
                log.error(
                    "register exception i=%s attempt=%s: %s\n%s",
                    idx,
                    attempts,
                    exc,
                    traceback.format_exc(),
                )
            result = {
                "email": email,
                "error": err_s,
                "timings_sec": {},
                "scrape_cache": None,
            }
        # one free retry on proxy/network flake (new sid via resolve_proxy next call)
        if (
            attempts < (1 + max(0, int(proxy_retries)))
            and not result_ok(result)
            and _is_proxy_retryable(
                {
                    "ok": False,
                    "error": result.get("error"),
                }
            )
        ):
            log.warning(
                "proxy/network fail i=%s email=%s attempt=%s → retry once: %s",
                idx,
                email,
                attempts,
                str(result.get("error") or "")[:160],
            )
            time.sleep(1.0)
            continue
        break
    wall = time.time() - t1
    if attempts > 1:
        result = dict(result)
        result["register_attempts"] = attempts
        result["proxy_retried"] = True

    saved = None
    cli_appended = False
    try:
        with _save_lock:
            rec = record_register_success(
                cfg, result, roster_path=ROOT / "sso_roster.txt"
            )
        path = rec["path"]
        saved = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        cli_appended = bool(rec.get("roster_appended"))
        if rec.get("roster_error"):
            log.warning("sso_roster append failed i=%s: %s", idx, rec["roster_error"])
    except Exception as exc:
        log.warning("record_register_success failed i=%s: %s", idx, exc)

    row = _safe_row(
        result, idx=idx, wall_s=wall, saved=saved, cli_appended=cli_appended
    )
    _note_zero_balance_result(row)
    log.debug(
        "row i=%s ok=%s cache=%s scrape=%s elapsed=%s cli=%s err=%s",
        idx,
        row["ok"],
        row.get("scrape_cache"),
        row.get("scrape_s"),
        row.get("elapsed_s"),
        cli_appended,
        row.get("error"),
    )
    return row



def _cmd_dry_run_log(
    *,
    n: int,
    jobs: int,
    use_cache: bool,
    do_prewarm: bool,
    fail_every: int,
    dry_sleep: float,
    ts: str,
) -> int:
    """Fake batch: same stdout shape as real run, no network / no files burned."""
    print(f"{logutil.icon('ok')} setup  proxy OK  0.0s  (dry-run)", flush=True)
    if do_prewarm:
        print(f"{logutil.icon('ok')} setup  prewarm OK  0.0s  (dry-run)", flush=True)
    else:
        print(f"{logutil.icon('skip')} setup  prewarm skipped  (dry-run)", flush=True)

    print(
        f"{logutil.icon('start')} cloudmail-batch  run_id={logutil.get_run_id()}  "
        f"n={n} jobs={jobs} cache={'on' if use_cache else 'off'} "
        f"prewarm={'on' if do_prewarm else 'off'}  dry_run=1",
        flush=True,
    )

    rows: list[dict] = []
    t0 = time.time()
    ok_live = fail_live = done_live = 0
    prog_lock = threading.Lock()
    fail_buckets: dict[str, int] = {}

    def _fake_one(idx: int) -> dict:
        if dry_sleep:
            time.sleep(dry_sleep)
        # deterministic fake email (not a real domain action)
        email = f"dry{idx:03d}@example.invalid"
        fail = fail_every > 0 and (idx % fail_every == 0)
        if fail:
            return {
                "idx": idx,
                "email": email,
                "ok": False,
                "error": "dry_run:simulated_fail",
                "scrape_cache": "hit",
                "scrape_s": 0.01,
                "elapsed_s": dry_sleep,
                "sso_roster_appended": False,
            }
        return {
            "idx": idx,
            "email": email,
            "ok": True,
            "error": None,
            "scrape_cache": "hit",
            "scrape_s": 0.01,
            "elapsed_s": dry_sleep,
            "sso_roster_appended": True,
        }

    def _on_row(row: dict) -> None:
        nonlocal ok_live, fail_live, done_live
        _note_zero_balance_result(row)
        if _zero_balance_tripped() and not row.get("ok"):
            # one-time banner
            if not getattr(_on_row, "_fuse_logged", False):
                log.error(
                    "Cap ZERO_BALANCE fuse tripped (≥%s hit) — "
                    "remaining workers short-circuit, please recharge CapSolver",
                    _ZERO_BALANCE_FUSE,
                )
                _on_row._fuse_logged = True  # type: ignore[attr-defined]
        with prog_lock:
            done_live += 1
            if row.get("ok"):
                ok_live += 1
            else:
                fail_live += 1
                k = "dry_fail"
                fail_buckets[k] = fail_buckets.get(k, 0) + 1
            elapsed = max(0.001, time.time() - t0)
            rate = done_live / elapsed
            eta = max(0, n - done_live) / rate if rate > 0 else 0.0
            kind = "register_ok" if row.get("ok") else "dry_fail"
            logutil.print_progress(
                done_live,
                n,
                kind=kind,
                email=str(row.get("email") or f"#{row.get('idx')}"),
                counters={"ok": ok_live, "fail": fail_live},
                rate=rate,
                eta_s=eta,
                elapsed_s=elapsed,
                force=not bool(row.get("ok")),
            )

    if jobs == 1:
        for i in range(1, n + 1):
            row = _fake_one(i)
            rows.append(row)
            _on_row(row)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_fake_one, i): i for i in range(1, n + 1)}
            tmp: dict[int, dict] = {}
            for fut in as_completed(futs):
                i = futs[fut]
                tmp[i] = fut.result()
                _on_row(tmp[i])
            rows = [tmp[i] for i in range(1, n + 1)]

    wall_total = time.time() - t0
    ok_n = sum(1 for r in rows if r.get("ok"))
    cli_n = sum(1 for r in rows if r.get("sso_roster_appended"))
    hits = sum(1 for r in rows if r.get("scrape_cache") == "hit")
    misses = sum(1 for r in rows if r.get("scrape_cache") == "miss")

    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} cloudmail-batch done  "
        f"elapsed={wall_total:.0f}s  jobs={jobs}  run_id={logutil.get_run_id()}  dry_run=1"
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}  note")
    print(f"  {'-' * 22} {'-' * 6}  {'-' * 28}")
    print(f"  {'n':<22} {n:>6}  requested")
    print(f"  {'ok':<22} {ok_n:>6}  simulated success")
    print(f"  {'fail':<22} {n - ok_n:>6}  simulated fail")
    rate = round(100.0 * ok_n / n, 1) if n else 0.0
    print(f"  {'ok_rate%':<22} {rate:>6.1f}")
    print(f"  {'cli_appended':<22} {cli_n:>6}  dry (not written)")
    print(f"  {'scrape_hit':<22} {hits:>6}  dry")
    print(f"  {'scrape_miss':<22} {misses:>6}  dry")
    if fail_buckets:
        print(f"  {'-' * 22} {'-' * 6}")
        for b, c in sorted(fail_buckets.items(), key=lambda x: -x[1])[:12]:
            print(f"  {b[:22]:<22} {c:>6}  fail bucket")
    print("=" * 56)
    logutil.done_footer(
        "cloudmail-batch",
        n=n,
        ok=ok_n,
        fail=n - ok_n,
        wall_s=round(wall_total, 1),
        j=jobs,
        dry_run=1,
    )
    _record_prod_bench(
        rows=rows,
        jobs=jobs,
        n=n,
        ok_n=ok_n,
        wall_total=wall_total,
        use_cache=True,
        hits=hits,
        misses=misses,
        dry_run=True,
        batch_file="prod_cloudmail_dry_run",
    )
    print("dry-run: no alloc / register / account_*.json / sso_roster write")
    return 0



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5, help="how many CloudMail accounts (default 5)")
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="concurrent workers (default 1=serial; e.g. 5 for j5xN)",
    )
    ap.add_argument("--no-scrape-cache", action="store_true")
    ap.add_argument("--scrape-cache-ttl", type=float, default=600.0)
    ap.add_argument(
        "--no-scrape-prewarm",
        action="store_true",
        help="skip serial public-page prewarm (default: prewarm when cache ON)",
    )
    ap.add_argument("--skip-proxy-preflight", action="store_true")
    ap.add_argument(
        "--proxy-retries",
        type=int,
        default=1,
        metavar="N",
        help="proxy/网络类失败时额外重试次数（默认 1=最多尝试 2 次；0=不重试）",
    )
    ap.add_argument(
        "--account-timeout",
        type=float,
        default=90.0,
        metavar="SEC",
        help="account soft-timeout seconds (default 90; 0=off). Detach from pool to protect thr.",
    )
    ap.add_argument(
        "--ascii-log",
        action="store_true",
        help="ASCII icons for # progress (OK/FAIL)",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="打印 scrape/captcha/sso 等过程 INFO（默认安静，只保留 # 进度与摘要）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="不真正 alloc/register/写盘，只演练进度与摘要日志（可加 --fail-every N）",
    )
    ap.add_argument(
        "--fail-every",
        type=int,
        default=0,
        metavar="N",
        help="配合 --dry-run：每 N 个模拟失败 1 个（0=全成功；例 3=第3,6,9…失败）",
    )
    ap.add_argument(
        "--dry-sleep",
        type=float,
        default=0.05,
        metavar="SEC",
        help="配合 --dry-run：每号模拟耗时秒（默认 0.05，测并发交错可调大）",
    )
    args = ap.parse_args()
    logutil.configure_logging(bool(args.verbose), ascii_log=bool(args.ascii_log))
    logutil.new_run_id()
    # Quiet by default so # progress is visible; -v restores full process INFO.
    if not args.verbose:
        import logging as _logging

        _logging.getLogger().setLevel(_logging.WARNING)
        for _name in (
            "grokreg",
            "prod_cloudmail",
            "urllib3",
            "curl_cffi",
            "httpx",
            "httpcore",
        ):
            _logging.getLogger(_name).setLevel(_logging.WARNING)
    n = max(1, int(args.n))
    jobs = max(1, int(args.jobs or 1))
    use_cache = not bool(args.no_scrape_cache)
    do_prewarm = use_cache and not bool(args.no_scrape_prewarm)
    ttl = float(args.scrape_cache_ttl or 600.0)
    dry_run = bool(getattr(args, "dry_run", False))
    fail_every = max(0, int(getattr(args, "fail_every", 0) or 0))
    dry_sleep = max(0.0, float(getattr(args, "dry_sleep", 0.05) or 0))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg = load_config()
    proxy_override = None

    if dry_run:
        return _cmd_dry_run_log(
            n=n,
            jobs=jobs,
            use_cache=use_cache,
            do_prewarm=do_prewarm,
            fail_every=fail_every,
            dry_sleep=dry_sleep,
            ts=ts,
        )

    if not args.skip_proxy_preflight:
        from grokreg.ops.env_cmds import run_proxy_preflight

        t_pf = time.time()
        code = run_proxy_preflight(cfg, proxy_override, require_ok=True, full_probe=True)
        pf_s = time.time() - t_pf
        if code != 0:
            print(f"{logutil.icon('fail')} setup  proxy FAIL  {pf_s:.1f}s", flush=True)
            log.error("proxy preflight refused, exit=%s", code)
            return 2
        print(f"{logutil.icon('ok')} setup  proxy OK  {pf_s:.1f}s", flush=True)
    else:
        print(f"{logutil.icon('skip')} setup  proxy skipped", flush=True)

    scrape_cache.clear()
    prewarm_info: dict[str, Any] | None = None
    if do_prewarm:
        prewarm_info = _prewarm_scrape(cfg, proxy_override=proxy_override, ttl=ttl)
        pw_ok = bool((prewarm_info or {}).get("ok"))
        pw_s = (prewarm_info or {}).get("sec")
        print(
            f"{logutil.icon('ok' if pw_ok else 'fail')} setup  prewarm "
            f"{'OK' if pw_ok else 'FAIL'}  {pw_s}s",
            flush=True,
        )
    else:
        log.debug("scrape-prewarm skipped cache=%s flag=%s", use_cache, args.no_scrape_prewarm)

    rows: list[dict] = []
    t0 = time.time()
    ok_live = 0
    fail_live = 0
    done_live = 0
    prog_lock = threading.Lock()
    fail_buckets: dict[str, int] = {}

    print(
        f"{logutil.icon('start')} cloudmail-batch  run_id={logutil.get_run_id()}  "
        f"n={n} jobs={jobs} cache={'on' if use_cache else 'off'} "
        f"prewarm={'on' if do_prewarm else 'off'}",
        flush=True,
    )
    logutil.info(
        "cloudmail-batch",
        phase="start",
        n=n,
        j=jobs,
        cache=1 if use_cache else 0,
        prewarm=1 if do_prewarm else 0,
        icon="start",
    )

    def _kind(row: dict) -> str:
        if row.get("ok"):
            return "register_ok"
        err = row.get("error") or ""
        if str(err).startswith("alloc:"):
            return "alloc_fail"
        return summary_error_bucket(err)

    def _on_row(row: dict) -> None:
        nonlocal ok_live, fail_live, done_live
        with prog_lock:
            done_live += 1
            if row.get("ok"):
                ok_live += 1
            else:
                fail_live += 1
                k = _kind(row)
                fail_buckets[k] = fail_buckets.get(k, 0) + 1
            elapsed = max(0.001, time.time() - t0)
            rate = done_live / elapsed
            eta = max(0, n - done_live) / rate if rate > 0 else 0.0
            kind = _kind(row)
            logutil.print_progress(
                done_live,
                n,
                kind=kind,
                email=str(row.get("email") or f"#{row.get('idx')}"),
                counters={"ok": ok_live, "fail": fail_live},
                rate=rate,
                eta_s=eta,
                elapsed_s=elapsed,
                force=not bool(row.get("ok")),
            )
            logutil.info(
                "cloudmail-batch",
                phase="progress",
                done=done_live,
                total=n,
                kind=kind,
                ok=1 if row.get("ok") else 0,
            )

    def _one_kwargs(i: int) -> dict:
        return dict(
            idx=i,
            n=n,
            cfg=cfg,
            proxy_override=proxy_override,
            use_cache=use_cache,
            ttl=ttl,
            jobs=jobs,
            verbose=bool(args.verbose),
            proxy_retries=int(getattr(args, "proxy_retries", 1) or 0),
        )

    account_soft_s = float(getattr(args, "account_timeout", 90.0) or 0.0)
    if account_soft_s < 0:
        account_soft_s = 0.0

    def _timeout_row(i: int, waited: float) -> dict:
        return {
            "idx": i,
            "email": f"#{i}",
            "ok": False,
            "error": f"account_timeout:{waited:.0f}s",
            "error_code": "account_timeout",
            "scrape_cache": None,
            "scrape_s": None,
            "turnstile_s": None,
            "wait_code_s": None,
            "sso_s": None,
            "elapsed_s": round(waited, 3),
            "wall_s": round(waited, 3),
            "short_body_hits": None,
            "create_body_len": None,
            "account_json": None,
            "sso_roster_appended": False,
            "provisional": True,
            "detached": True,
        }

    def _crash_row(i: int, exc: BaseException) -> dict:
        return {
            "idx": i,
            "email": "",
            "ok": False,
            "error": f"worker:{exc}",
            "scrape_cache": None,
            "sso_roster_appended": False,
        }

    # Sliding window + soft-timeout detach (straggler must not set batch wall).
    rows_by_idx: dict[int, dict] = {}
    next_i = 1
    fused_stop = False
    detached_n = 0
    late_ok_n = 0

    if jobs == 1:
        from concurrent.futures import TimeoutError as FutTimeout

        for i in range(1, n + 1):
            if _zero_balance_tripped():
                fused_stop = True
                log.error(
                    "Cap ZERO_BALANCE fuse — stop batch early after %s/%s",
                    i - 1,
                    n,
                )
                break
            if account_soft_s > 0:
                with ThreadPoolExecutor(max_workers=1) as ex1:
                    fut = ex1.submit(_one, **_one_kwargs(i))
                    try:
                        row = fut.result(timeout=account_soft_s)
                    except FutTimeout:
                        log.warning(
                            "account soft-timeout idx=%s after %.0fs (j=1)",
                            i,
                            account_soft_s,
                        )
                        detached_n += 1
                        row = _timeout_row(i, account_soft_s)
                        try:
                            real = fut.result(timeout=2.0)
                            if real.get("ok"):
                                late_ok_n += 1
                                row = real
                        except Exception:
                            pass
                    except Exception as exc:
                        row = _crash_row(i, exc)
            else:
                row = _one(**_one_kwargs(i))
            rows_by_idx[i] = row
            _on_row(row)
            if _zero_balance_tripped():
                fused_stop = True
                log.error(
                    "Cap ZERO_BALANCE fuse tripped after idx=%s — remaining %s not started",
                    i,
                    n - i,
                )
                break
    else:
        ex = ThreadPoolExecutor(max_workers=jobs)
        try:
            # fut -> (idx, t_start)
            futs: dict = {}
            abandoned: dict = {}

            def _submit_more() -> None:
                nonlocal next_i
                while (
                    next_i <= n
                    and len(futs) < jobs
                    and not _zero_balance_tripped()
                ):
                    fut = ex.submit(_one, **_one_kwargs(next_i))
                    futs[fut] = (next_i, time.monotonic())
                    next_i += 1

            def _reap_abandoned_done() -> None:
                nonlocal late_ok_n
                for fut in list(abandoned.keys()):
                    if not fut.done():
                        continue
                    idx, t0m = abandoned.pop(fut)
                    try:
                        real = fut.result()
                    except Exception as exc:
                        real = _crash_row(idx, exc)
                    prev = rows_by_idx.get(idx) or {}
                    if prev.get("provisional") and real.get("ok"):
                        late_ok_n += 1
                        log.info(
                            "late ok idx=%s recovered after soft-timeout (%.0fs)",
                            idx,
                            time.monotonic() - t0m,
                        )
                    # final truth for summary (may replace provisional)
                    rows_by_idx[idx] = real

            _submit_more()
            while futs or abandoned:
                _reap_abandoned_done()
                if _zero_balance_tripped() and not futs:
                    fused_stop = True
                    break
                if not futs:
                    if not abandoned:
                        break
                    done_set, _nd = wait(
                        list(abandoned.keys()),
                        timeout=2.0,
                        return_when=FIRST_COMPLETED,
                    )
                    if done_set:
                        _reap_abandoned_done()
                        continue
                    log.warning(
                        "abandon %s still-running soft-timeout worker(s) — batch wall free",
                        len(abandoned),
                    )
                    # already counted in detached_n when soft-timeout fired
                    abandoned.clear()
                    break

                now = time.monotonic()
                if account_soft_s > 0:
                    rem_list = [
                        max(0.05, account_soft_s - (now - t0m))
                        for _f, (_i, t0m) in futs.items()
                    ]
                    wait_s = min(rem_list) if rem_list else 0.5
                else:
                    wait_s = None

                done_set, _ = wait(
                    list(futs.keys()),
                    timeout=wait_s,
                    return_when=FIRST_COMPLETED,
                )

                if not done_set and account_soft_s > 0:
                    now = time.monotonic()
                    for fut, (idx, t0m) in list(futs.items()):
                        if (now - t0m) + 0.05 >= account_soft_s:
                            futs.pop(fut)
                            abandoned[fut] = (idx, t0m)
                            waited = now - t0m
                            log.warning(
                                "account soft-timeout idx=%s after %.0fs — "
                                "detach (pool free)",
                                idx,
                                waited,
                            )
                            detached_n += 1
                            row = _timeout_row(idx, waited)
                            rows_by_idx[idx] = row
                            _on_row(row)
                    _submit_more()
                    if _zero_balance_tripped():
                        fused_stop = True
                        for f2 in list(futs.keys()):
                            f2.cancel()
                        break
                    continue

                for fut in done_set:
                    idx, t0m = futs.pop(fut)
                    try:
                        row = fut.result()
                    except Exception as exc:
                        log.error("worker crash i=%s: %s", idx, exc)
                        row = _crash_row(idx, exc)
                    rows_by_idx[idx] = row
                    _on_row(row)

                if _zero_balance_tripped():
                    fused_stop = True
                    for f2 in list(futs.keys()):
                        f2.cancel()
                    for f2, (j2, _) in list(futs.items()):
                        if f2.cancelled():
                            futs.pop(f2, None)
                            continue
                        try:
                            rows_by_idx[j2] = f2.result(timeout=5)
                            _on_row(rows_by_idx[j2])
                        except Exception as exc2:
                            rows_by_idx[j2] = _crash_row(j2, exc2)
                            _on_row(rows_by_idx[j2])
                        futs.pop(f2, None)
                    log.error(
                        "Cap ZERO_BALANCE fuse — stop submitting "
                        "(ran≈%s of n=%s)",
                        len(rows_by_idx),
                        n,
                    )
                    break
                _submit_more()

        finally:
            # Do NOT wait for soft-timeout stragglers (they would re-impose 200s wall)
            try:
                ex.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                ex.shutdown(wait=False)
    # only rows that actually ran (no phantom 90+ fuse fails)
    if rows_by_idx:
        rows = [rows_by_idx[i] for i in sorted(rows_by_idx.keys())]
    else:
        rows = []
    # Wall for thr: prefer clock; but if soft-timeouts, thr uses ran completion
    # (clock already free of 200s+ stragglers once detached).
    if detached_n:
        print(
            f"{logutil.icon('warn')} soft-timeout  detached≈{detached_n}  "
            f"late_ok={late_ok_n}  (limit={account_soft_s:.0f}s)",
            flush=True,
        )
    if fused_stop:
        skipped = max(0, n - len(rows))
        if skipped:
            print(
                f"{logutil.icon('warn')} fuse  early_stop  ran={len(rows)}  "
                f"skipped={skipped}  (Cap empty)",
                flush=True,
            )

    wall_total = time.time() - t0
    ran_n = len(rows)
    ok_n = sum(1 for r in rows if r.get("ok"))
    fail_n = sum(1 for r in rows if not r.get("ok"))
    cli_n = sum(1 for r in rows if r.get("sso_roster_appended"))

    hits = sum(1 for r in rows if r.get("scrape_cache") == "hit")
    misses = sum(1 for r in rows if r.get("scrape_cache") == "miss")
    scrapes = [
        float(r["scrape_s"])
        for r in rows
        if isinstance(r.get("scrape_s"), (int, float))
    ]
    elapsed = [
        float(r["elapsed_s"])
        for r in rows
        if isinstance(r.get("elapsed_s"), (int, float))
    ]

    def _med(xs: list[float]) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        return round(s[len(s) // 2], 3)

    def _batch_debug(rows_in: list[dict]) -> dict:
        """Aggregate debug counters for Cap/proxy forensics (no secrets)."""
        pref_ok = pref_fb = turns = 0
        proxy_retried = 0
        reasons: dict[str, int] = {}
        fail_emails: list[dict] = []
        for r in rows_in:
            if r.get("turnstile_s") is not None or r.get("captcha_prefetch") is not None:
                turns += 1
            meta = r.get("captcha_prefetch_meta") if isinstance(r.get("captcha_prefetch_meta"), dict) else {}
            reason = str(meta.get("reason") or "")
            if r.get("captcha_prefetch") is True:
                pref_ok += 1
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
            elif r.get("captcha_prefetch") is False:
                pref_fb += 1
                key = reason or "sync_or_no_prefetch"
                reasons[key] = reasons.get(key, 0) + 1
            if r.get("proxy_retried") or int(r.get("register_attempts") or 1) > 1:
                proxy_retried += 1
            if not r.get("ok"):
                fail_emails.append(
                    {
                        "email": r.get("email") or "",
                        "bucket": _kind(r),
                        "error": str(r.get("error") or "")[:120],
                    }
                )
        # Cap cost heuristic: 1 solve per row that reached turnstile;
        # +1 when prefetch fell back after starting (join budget / error)
        double_pay = 0
        for r in rows_in:
            meta = r.get("captcha_prefetch_meta") if isinstance(r.get("captcha_prefetch_meta"), dict) else {}
            reason = str(meta.get("reason") or "")
            if reason in ("join_budget", "join_timeout", "prefetch_error", "prefetch_expired"):
                double_pay += 1
            elif r.get("captcha_prefetch") is False and reason:
                double_pay += 1
        captcha_est = turns + double_pay
        # proxy-retry whole account often pays another turnstile
        captcha_est += proxy_retried
        return {
            "turnstile_rows": turns,
            "prefetch_ok": pref_ok,
            "prefetch_fallback": pref_fb,
            "prefetch_reasons": reasons,
            "proxy_retried": proxy_retried,
            "captcha_est_solves": captcha_est,
            "fail_buckets": dict(fail_buckets),
            "fail_emails": fail_emails[:200],
            "fail_emails_n": len(fail_emails),
        }


    debug = _batch_debug(rows)

    summary = {
        "ts_utc": ts,
        "n": n,
        "jobs": jobs,
        "ok": ok_n,
        "raw_ok_rate": round(100.0 * ok_n / ran_n, 1) if ran_n else 0.0,
        "requested_n": n,
        "ran_n": ran_n,
        "fail": fail_n,
        "scrape_cache": use_cache,
        "scrape_prewarm": do_prewarm,
        "prewarm": prewarm_info,
        "hit": hits,
        "miss": misses,
        "hit_rate": round(100.0 * hits / n, 1) if n else 0.0,
        "scrape_p50": _med(scrapes),
        "elapsed_p50": _med(elapsed),
        "sso_roster_appended": cli_n,
        "wall_total_sec": round(wall_total, 3),
        "scrape_cache_stats": scrape_cache.stats(),
        "fail": n - ok_n,
        "fail_buckets": dict(fail_buckets),
        "debug": debug,
        "thr": round(ok_n / wall_total, 3) if wall_total > 0 else 0.0,
        "rows": rows,
    }
    out_json = OUT_SUM / f"batch_{ts}_j{jobs}n{n}.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    pw = prewarm_info or {}
    lines = [
        f"# CloudMail prod batch  {ts}",
        "",
        f"j={jobs} n={n} ok={ok_n}/{n} ({summary['raw_ok_rate']}%)  "
        f"cache={'on' if use_cache else 'off'} prewarm={'on' if do_prewarm else 'off'} "
        f"prewarm_ok={pw.get('ok')} prewarm_s={pw.get('sec')} "
        f"hit={hits} miss={misses} "
        f"scrape_p50={summary['scrape_p50']} elapsed_p50={summary['elapsed_p50']}  "
        f"cli_appended={cli_n} wall={summary['wall_total_sec']}s thr={summary.get('thr')}",
        "",
        f"debug: prefetch_ok={debug.get('prefetch_ok')} prefetch_fallback={debug.get('prefetch_fallback')} "
        f"proxy_retried={debug.get('proxy_retried')} captcha_est={debug.get('captcha_est_solves')} "
        f"fail_buckets={debug.get('fail_buckets')}",
        "",
        "prewarm=GET 注册页公开参数 only，burns_account=0",
        "",
        "| # | email | cache | scrape | elapsed | ok | cli | error |",
        "|---|-------|-------|--------|---------|----|-----|-------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('idx')} | {r.get('email') or '-'} | {r.get('scrape_cache')} | "
            f"{r.get('scrape_s')} | {r.get('elapsed_s')} | {r.get('ok')} | "
            f"{r.get('sso_roster_appended')} | {(r.get('error') or '-')[:48]} |"
        )
    lines += ["", f"JSON: `{out_json}`"]
    out_md = OUT_SUM / f"batch_{ts}_j{jobs}n{n}.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} cloudmail-batch done  "
        f"elapsed={wall_total:.0f}s  jobs={jobs}  run_id={logutil.get_run_id()}"
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}  note")
    print(f"  {'-' * 22} {'-' * 6}  {'-' * 28}")
    print(f"  {'n':<22} {n:>6}  requested")
    if ran_n < n:
        print(f"  {'ran':<22} {ran_n:>6}  actually started")
        print(f"  {'skipped':<22} {n - ran_n:>6}  fuse early-stop")
    print(f"  {'ok':<22} {ok_n:>6}  sso success")
    print(f"  {'fail':<22} {fail_n:>6}")
    print(f"  {'ok_rate%':<22} {summary['raw_ok_rate']:>6.1f}")
    print(f"  {'cli_appended':<22} {cli_n:>6}")
    print(f"  {'scrape_hit':<22} {hits:>6}")
    print(f"  {'scrape_miss':<22} {misses:>6}")
    if fail_buckets:
        print(f"  {'-' * 22} {'-' * 6}")
        for b, c in sorted(fail_buckets.items(), key=lambda x: -x[1])[:12]:
            print(f"  {b[:22]:<22} {c:>6}  fail bucket")
    print(f"  {'-' * 22} {'-' * 6}")
    print(f"  {'thr':<22} {summary.get('thr'):>6}  ok/wall")
    print(f"  {'prefetch_ok':<22} {debug.get('prefetch_ok'):>6}")
    print(f"  {'prefetch_fallback':<22} {debug.get('prefetch_fallback'):>6}  sync/double-pay risk")
    print(f"  {'proxy_retried':<22} {debug.get('proxy_retried'):>6}  whole-account retry")
    print(f"  {'captcha_est':<22} {debug.get('captcha_est_solves'):>6}  ~Turnstile pays")
    print("=" * 56)
    # fail email list for sso_failed recovery
    fail_path = OUT_SUM / f"batch_{ts}_j{jobs}n{n}_fails.json"
    try:
        fail_path.write_text(
            json.dumps(
                {
                    "ts_utc": ts,
                    "n": n,
                    "jobs": jobs,
                    "fail": n - ok_n,
                    "buckets": dict(fail_buckets),
                    "emails": debug.get("fail_emails") or [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"fail_list → {fail_path}")
    except Exception as exc:
        log.warning("write fail_list: %s", exc)
    logutil.done_footer(
        "cloudmail-batch",
        n=n,
        ok=ok_n,
        fail=n - ok_n,
        wall_s=round(wall_total, 1),
        j=jobs,
        hit=hits,
        miss=misses,
        cli=cli_n,
        thr=summary.get("thr"),
        prefetch_fb=debug.get("prefetch_fallback"),
        proxy_retry=debug.get("proxy_retried"),
        captcha_est=debug.get("captcha_est_solves"),
    )
    _record_prod_bench(
        rows=rows,
        jobs=jobs,
        n=n,
        ok_n=ok_n,
        wall_total=wall_total,
        use_cache=use_cache,
        hits=hits,
        misses=misses,
        summary=summary,
        batch_file=str(out_json),
        dry_run=False,
    )
    # keep markdown table dump for file path visibility
    print(f"summary_md → {out_md}")
    print(f"summary_json → {out_json}")
    logutil.info(
        "cloudmail-batch",
        phase="done",
        ok=ok_n,
        n=n,
        j=jobs,
        hit=hits,
        miss=misses,
        cli=cli_n,
        wall_s=round(wall_total, 1),
        icon="done",
    )
    return 0 if ok_n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
