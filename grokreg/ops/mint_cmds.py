"""Mint CLI command (split from credential_cmds)."""
from __future__ import annotations

import argparse
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from .. import logutil

from .credential_cmds import (
    _auth_path,
    _cpa_path_for_email,
    _existing_cpa_emails,
    _existing_pool_emails,
    _read_sso_roster,
    _tally,
)
from .mint_proxy import resolve_mint_proxy


def _cmd_mint(cfg: dict, args: argparse.Namespace) -> int:
    """Protocol mint: SSO → RT/AT → auth.json (no default cpa_files write)."""
    from ..oauth import mint

    proxy = resolve_mint_proxy(args)
    out_dir = Path(args.mint_out_dir or "cpa_export")
    probe_mode = "none" if args.no_probe else (getattr(args, "mint_probe_mode", None) or "models")
    missing_only = bool(getattr(args, "mint_missing", False))
    # optional: --mint-write-cpa writes cpa pack during mint (off by default)
    write_cpa = bool(getattr(args, "mint_write_cpa", False))
    packs = ["cpa_files"] if write_cpa else []

    target = (args.mint or "").strip().lower()

    from .ledger_ops import select_mint_todos

    accounts = _read_sso_roster()
    if not accounts:
        return 2

    auth_path = _auth_path(cfg, args)
    limit_n = getattr(args, "limit", None)
    if limit_n is not None:
        limit_n = int(limit_n) if int(limit_n) > 0 else None

    todos, sel = select_mint_todos(
        accounts,
        target=target or "all",
        auth_path=auth_path,
        missing_only=missing_only,
        limit=limit_n,
        newest_first=True,
    )
    skipped_existing = int(sel.get("skipped_existing") or 0)

    if target not in {"", "all", "*"} and not todos and skipped_existing == 0:
        if not any(
            target in (a.get("email") or "").lower()
            or (a.get("email") or "").lower() == target
            for a in accounts
        ):
            logging.error("在 sso_roster.txt 中未找到邮箱: %s", target)
            return 2

    if missing_only:
        logging.info(
            "mint-missing: candidates=%s already_in_pool=%s will_mint=%s newest_first=1 auth=%s",
            sel.get("candidates_in"),
            skipped_existing,
            len(todos),
            auth_path,
        )
        if not todos:
            logging.info(
                "mint done ok=0 fail=0 total=0 skipped_existing=%s (nothing missing in auth.json)",
                skipped_existing,
            )
            return 0

    if limit_n:
        logging.info(
            "mint --limit=%s: will_mint=%s (newest missing first) of candidates_in=%s",
            limit_n,
            len(todos),
            sel.get("candidates_in"),
        )
        print(
            f"mint limit={limit_n} will_mint={len(todos)} "
            f"(newest-first; candidates_in={sel.get('candidates_in')})"
        )

    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    total = len(todos)
    logging.info(
        "protocol mint: target=%s count=%s jobs=%s proxy=%s probe_mode=%s missing_only=%s packs=%s",
        target,
        total,
        jobs,
        proxy,
        probe_mode,
        int(missing_only),
        packs or "[]",
    )

    t0 = time.time()
    ok = fail = 0
    ctr_lock = threading.Lock()
    print(
        f"{logutil.icon('start')} mint  run_id={logutil.get_run_id()}  "
        f"total={total} jobs={jobs} missing_only={int(missing_only)} "
        f"proxy={proxy} packs={packs or []} source=auth.json",
        flush=True,
    )
    logutil.info(
        "mint",
        phase="start",
        total=total,
        j=jobs,
        missing_only=int(missing_only),
        proxy=proxy,
        icon="start",
    )

    def _mint_worker(acc: dict) -> dict:
        # SSO → RT/AT → auth.json; packs only if --mint-write-cpa
        return mint(
            email=acc["email"],
            sso=acc["sso"],
            proxy=proxy,
            auth_path=auth_path,
            packs=packs,
            out_dir=out_dir if packs else None,
            probe_mode=probe_mode,
        )

    def _progress(done_n: int, email: str, kind: str, ok_n: int, fail_n: int) -> None:
        elapsed = max(0.001, time.time() - t0)
        rate = done_n / elapsed
        eta = max(0, total - done_n) / rate if rate > 0 else 0.0
        force = kind in {"mint_fail", "fail"}
        line = logutil.print_progress(
            done_n,
            total,
            kind=kind,
            email=email,
            counters={"ok": ok_n, "fail": fail_n},
            rate=rate,
            eta_s=eta,
            elapsed_s=elapsed,
            force=force,
        )
        if line:
            logutil.info(
                "mint",
                phase="progress",
                done=done_n,
                total=total,
                kind=kind,
            )

    if jobs == 1:
        for i, acc in enumerate(todos, 1):
            email = acc["email"]
            logging.info("=== [%s/%s] %s ===", i, total, email)
            try:
                r = _mint_worker(acc)
                ok, fail = _tally(ok, fail, r, email)
                _progress(i, email, "mint_ok" if r.get("ok") else "mint_fail", ok, fail)
            except Exception as exc:
                fail += 1
                logging.exception("EXCEPTION %s: %s", email, exc)
                _progress(i, email, "mint_fail", ok, fail)
    else:
        with ThreadPoolExecutor(max_workers=min(jobs, total or 1)) as ex:
            futmap = {ex.submit(_mint_worker, acc): acc["email"] for acc in todos}
            done_n = 0
            for f in as_completed(futmap):
                email = futmap[f]
                try:
                    r = f.result()
                    with ctr_lock:
                        ok, fail = _tally(ok, fail, r, email)
                        done_n += 1
                        cur_ok, cur_fail, cur_done = ok, fail, done_n
                    _progress(
                        cur_done,
                        email,
                        "mint_ok" if r.get("ok") else "mint_fail",
                        cur_ok,
                        cur_fail,
                    )
                except Exception as exc:
                    with ctr_lock:
                        fail += 1
                        done_n += 1
                        cur_ok, cur_fail, cur_done = ok, fail, done_n
                    logging.exception("EXCEPTION %s: %s", email, exc)
                    _progress(cur_done, email, "mint_fail", cur_ok, cur_fail)

    elapsed = time.time() - t0
    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} mint done  source=auth.json  "
        f"elapsed={elapsed:.0f}s  jobs={jobs}  proxy={proxy}  "
        f"run_id={logutil.get_run_id()}"
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}  note")
    print(f"  {'-' * 22} {'-' * 6}  {'-' * 28}")
    print(f"  {'work_total':<22} {total:>6}  candidates this run")
    if missing_only:
        print(f"  {'skipped_existing':<22} {skipped_existing:>6}  already in auth.json")
    print(f"  {'ok':<22} {ok:>6}  mint success")
    print(f"  {'fail':<22} {fail:>6}  mint failed")
    print("=" * 56)
    logutil.done_footer(
        "mint",
        total=total,
        ok=ok,
        fail=fail,
        skipped_existing=skipped_existing if missing_only else 0,
        wall_s=round(elapsed, 1),
        j=jobs,
        proxy=proxy,
    )
    if missing_only:
        logging.info(
            "mint done ok=%s fail=%s total=%s skipped_existing=%s missing_only=1 proxy=%s",
            ok,
            fail,
            ok + fail,
            skipped_existing,
            proxy,
        )
    else:
        logging.info(
            "mint done ok=%s fail=%s total=%s proxy=%s", ok, fail, ok + fail, proxy
        )
    return 0 if fail == 0 else 1
