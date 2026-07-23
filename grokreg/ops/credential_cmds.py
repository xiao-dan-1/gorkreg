"""Credential lifecycle CLI commands (mint / refresh / probe / auth-status).

Split from grokreg.cli to keep argparse shell thin.
Ledger-first: auth.json only; cpa_export is export-layer.

Public ledger helpers live in ``grokreg.ops.ledger_ops``
(append_sso_roster, resolve_auth_path, read_sso_roster).
Private ``_append_sso_roster`` / ``_auth_path`` names remain as thin aliases.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from grokreg import logutil

from .ledger_ops import (
    append_sso_roster as _append_sso_roster,
    existing_pool_emails as _existing_pool_emails_impl,
    read_sso_roster as _read_sso_roster_impl,
    resolve_auth_path as _resolve_auth_path_impl,
)

log = logging.getLogger(__name__)


def _auth_path(cfg: dict, args: argparse.Namespace) -> Path:
    return _resolve_auth_path_impl(cfg, args)


def _read_sso_roster() -> list[dict]:
    """Read sso_roster.txt, return [{email, password, sso}]."""
    return _read_sso_roster_impl()


def _existing_cpa_emails(out_dir: Path) -> set[str]:
    """Lowercase emails that already have xai-*.json under out_dir (by filename)."""
    found: set[str] = set()
    if not out_dir.is_dir():
        return found
    for p in out_dir.glob("xai-*.json"):
        stem = p.stem  # xai-email
        email = stem[4:] if stem.lower().startswith("xai-") else stem
        if email:
            found.add(email.lower())
    return found


def _existing_pool_emails(auth_path: Path) -> set[str]:
    """Lowercase emails already present in auth.json ledger."""
    return _existing_pool_emails_impl(auth_path)


def _cpa_path_for_email(out_dir: Path, email: str) -> Path:
    return out_dir / f"xai-{email}.json"




def _tally(ok: int, fail: int, r: dict, email: str) -> tuple[int, int]:
    if r.get("ok"):
        chat_ok = r.get("chat_ok")
        if chat_ok is False:
            logging.info(
                "OK %s chat=False status=%s model=%s err=%s",
                email,
                r.get("chat_status"),
                r.get("chat_model"),
                (r.get("chat_error") or "-")[:200],
            )
        else:
            logging.info(
                "OK %s probe=%s ok=%s model=%s",
                email,
                r.get("probe_mode") or "?",
                chat_ok,
                r.get("chat_model"),
            )
        return ok + 1, fail
    else:
        logging.warning("FAIL: %s chat=%s err=%s", email, r.get("chat_ok"), r.get("error"))
        return ok, fail + 1


def _is_refresh_revoked(err: str | None) -> bool:
    """True when refresh_token is dead server-side (don't retry refresh)."""
    low = (err or "").lower()
    return (
        "invalid_grant" in low
        or "revoked" in low
        or "refresh token has been revoked" in low
        or "token has been revoked" in low
    )


def _sso_by_email() -> dict[str, str]:
    """email(lower) -> sso from sso_roster.txt."""
    out: dict[str, str] = {}
    for a in _read_sso_roster():
        em = (a.get("email") or "").strip().lower()
        sso = (a.get("sso") or "").strip()
        if em and sso:
            out[em] = sso
    return out


def _email_from_cpa_path(path: Path, fallback: str = "") -> str:
    name = path.stem
    if name.lower().startswith("xai-"):
        return name[4:]
    return fallback or name


def _cmd_refresh(cfg: dict, args: argparse.Namespace) -> int:
    """Refresh auth.json ledger via refresh_token; optional remint on revoke.

    Source of truth: auth.json only (ledger-first). Does not require cpa_export.
    Remint writes auth.json; xAI packs via --export cpa_files (export layer).

    Progress: each finish prints ``[done/total]`` + counters.
    Summary: refresh_ok vs remint_ok vs fails, with elapsed.
    """
    from ..auth_pool import list_entries, refresh_entry, status_row
    from ..oauth import mint

    proxy = (args.mint_proxy or "").strip()
    if not proxy:
        from .mint_proxy import resolve_mint_proxy

        proxy = resolve_mint_proxy(args)
    # ledger-only: no cpa_dir/out_dir here (export via --export cpa_files)
    probe_mode = "none" if args.no_probe else (getattr(args, "mint_probe_mode", None) or "models")
    needs_only = bool(getattr(args, "needs_refresh_only", False))
    remint_on_revoke = bool(getattr(args, "remint_on_revoke", False))
    skew_min = float(getattr(args, "skew_min", 5.0) or 5.0)
    skew_sec = max(0.0, skew_min) * 60.0
    jobs = max(1, int(getattr(args, "jobs", 1) or 1))

    target = (args.refresh or "").strip().lower()
    if not target:
        logging.error(
            "用法: --refresh all|email [--needs-refresh-only] [--limit N] [--skew-min N] "
            "[--remint-on-revoke] [--no-probe] [-j N]"
        )
        return 2

    auth_path = _auth_path(cfg, args)
    if not Path(auth_path).is_file():
        logging.error("auth pool 不存在: %s", auth_path)
        return 2

    entries = list_entries(auth_path, include_disabled=False, include_expired=True)
    if target not in {"", "all", "*"}:
        entries = [
            (k, e)
            for k, e in entries
            if target in (e.get("email") or "").lower()
        ]

    if not entries:
        logging.error("auth.json 无匹配账号: target=%s path=%s", target, auth_path)
        return 2

    skipped_fresh = 0
    skipped_no_rt = 0
    work: list[tuple[str, dict]] = []
    for k, e in entries:
        row = status_row(k, e, skew_sec=skew_sec)
        if needs_only:
            if row.get("needs_refresh"):
                work.append((k, e))
            elif row.get("state") == "fresh":
                skipped_fresh += 1
            elif not row.get("has_rt"):
                skipped_no_rt += 1
            else:
                skipped_fresh += 1
        else:
            if not row.get("has_rt") and not row.get("has_at"):
                skipped_no_rt += 1
            else:
                work.append((k, e))

    if needs_only:
        logging.info(
            "refresh filter needs-only source=auth.json candidates=%s keep=%s "
            "skip_freshish=%s skip_no_rt=%s skew_min=%s",
            len(entries),
            len(work),
            skipped_fresh,
            skipped_no_rt,
            skew_min,
        )
        if not work:
            logging.info(
                "refresh: nothing needs refresh (all fresh or no refresh_token); done ok=0 fail=0"
            )
            print(
                f"refresh: nothing needs refresh source=auth.json "
                f"skipped_freshish={skipped_fresh} skipped_no_rt={skipped_no_rt} skew_min={skew_min}"
            )
            return 0

    sso_map: dict[str, str] = {}
    if remint_on_revoke:
        sso_map = _sso_by_email()
        logging.info("remint-on-revoke: sso_index=%s", len(sso_map))

    limit_n = getattr(args, "limit", None)
    if limit_n is not None and int(limit_n) > 0:
        before = len(work)
        work = work[: int(limit_n)]
        logging.info("refresh --limit=%s: truncated %s → %s", limit_n, before, len(work))
        print(f"refresh limit={limit_n} (of {before} candidates)")

    total = len(work)
    logging.info(
        "refresh: %s entries source=auth.json jobs=%s proxy=%s probe_mode=%s "
        "needs_only=%s skew_min=%s remint_on_revoke=%s",
        total,
        jobs,
        proxy,
        probe_mode,
        1 if needs_only else 0,
        skew_min,
        1 if remint_on_revoke else 0,
    )
    print(
        f"{logutil.icon('start')} refresh  run_id={logutil.get_run_id()}  "
        f"total={total} jobs={jobs} needs_only={1 if needs_only else 0} "
        f"remint_on_revoke={1 if remint_on_revoke else 0} "
        f"skew_min={skew_min} source=auth.json",
        flush=True,
    )
    logutil.info(
        "refresh",
        phase="start",
        total=total,
        j=jobs,
        needs_only=int(bool(needs_only)),
        remint_on_revoke=int(bool(remint_on_revoke)),
        skew_min=skew_min,
        icon="start",
    )

    refresh_ok = remint_ok = 0
    remint_fail = remint_skip_no_sso = fail_other = 0
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _tally_lock = threading.Lock()
    # no process-wide lock around mint/refresh HTTP (was serializing -j)
    t0 = _time.time()

    def _refresh_one(item: tuple[str, dict]) -> dict:
        key, entry = item
        email = (entry.get("email") or "").strip()
        out = {
            "kind": "fail_other",
            "email": email,
            "error": "",
        }
        try:
            # Do not hold process lock across HTTP; auth_pool serializes auth.json.
            r = refresh_entry(
                auth_path,
                key,
                entry,
                proxy=proxy,
                probe_mode=probe_mode,
            )
            if r.get("ok"):
                out["kind"] = "refresh_ok"
                logging.info(
                    "OK refresh %s exp_old=%s exp_new=%s",
                    email,
                    r.get("exp_old"),
                    r.get("exp_new"),
                )
                return out

            err = str(r.get("error") or "")
            out["error"] = err[:200]
            if remint_on_revoke and _is_refresh_revoked(err):
                sso = sso_map.get(email.lower())
                if not sso:
                    out["kind"] = "remint_skip_no_sso"
                    logging.warning(
                        "FAIL refresh revoked, no SSO for remint: %s err=%s",
                        email,
                        err[:200],
                    )
                    return out
                logging.info("refresh revoked → remint via SSO: %s", email)
                try:
                    # ledger only — CPA is export-layer (--export cpa_files)
                    mr = mint(
                        email=email,
                        sso=sso,
                        proxy=proxy,
                        auth_path=auth_path,
                        packs=[],
                        out_dir=None,
                        probe_mode=probe_mode,
                    )
                except Exception as mex:
                    out["kind"] = "remint_fail"
                    out["error"] = f"{type(mex).__name__}: {mex}"
                    logging.exception("REMINT EXCEPTION %s: %s", email, mex)
                    return out
                if mr.get("ok"):
                    out["kind"] = "remint_ok"
                    logging.info(
                        "REMINT OK %s auth=%s",
                        email,
                        mr.get("auth_path") or auth_path,
                    )
                else:
                    out["kind"] = "remint_fail"
                    out["error"] = (mr.get("error") or "")[:200]
                    logging.warning("REMINT FAIL %s: %s", email, out["error"])
                return out

            out["kind"] = "fail_other"
            logging.warning("FAIL %s: %s", email, err[:200])
            return out
        except Exception as exc:
            out["kind"] = "fail_other"
            out["error"] = f"{type(exc).__name__}: {exc}"
            logging.exception("EXCEPTION %s: %s", email, exc)
            return out

    def _apply_outcome(o: dict) -> None:
        nonlocal refresh_ok, remint_ok, remint_fail, remint_skip_no_sso, fail_other
        kind = o.get("kind") or "fail_other"
        with _tally_lock:
            if kind == "refresh_ok":
                refresh_ok += 1
            elif kind == "remint_ok":
                remint_ok += 1
            elif kind == "remint_fail":
                remint_fail += 1
            elif kind == "remint_skip_no_sso":
                remint_skip_no_sso += 1
            else:
                fail_other += 1

    def _maybe_print_progress(done_n: int, email: str = "", kind: str = "") -> None:
        elapsed = max(0.001, _time.time() - t0)
        rate = done_n / elapsed
        remain = max(0, total - done_n)
        eta = remain / rate if rate > 0 else 0.0
        recovered = refresh_ok + remint_ok
        failed = remint_fail + remint_skip_no_sso + fail_other
        force = kind in {
            "remint_fail",
            "remint_skip_no_sso",
            "fail_other",
        }
        line = logutil.print_progress(
            done_n,
            total,
            kind=kind or "refresh",
            email=email,
            counters={
                "refresh_ok": refresh_ok,
                "remint_ok": remint_ok,
                "recovered": recovered,
                "fail": failed,
            },
            rate=rate,
            eta_s=eta,
            elapsed_s=elapsed,
            force=force,
        )
        if line:
            logutil.info(
                "refresh",
                phase="progress",
                done=done_n,
                total=total,
                kind=kind or "-",
            )

    if jobs == 1:
        for i, item in enumerate(work, 1):
            em = (item[1].get("email") or item[0])[:40]
            logging.info("=== [%s/%s] %s ===", i, total, em)
            o = _refresh_one(item)
            _apply_outcome(o)
            _maybe_print_progress(i, email=o.get("email") or em, kind=str(o.get("kind") or ""))
            # no artificial 1s serial pace between accounts
    else:
        workers = min(jobs, total)
        logging.info("refresh parallel workers=%s", workers)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futmap = {ex.submit(_refresh_one, item): item for item in work}
            done_n = 0
            for f in as_completed(futmap):
                item = futmap[f]
                done_n += 1
                try:
                    o = f.result()
                except Exception as exc:
                    o = {
                        "kind": "fail_other",
                        "email": (item[1].get("email") or "?"),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    logging.exception("EXCEPTION %s: %s", o["email"], exc)
                _apply_outcome(o)
                _maybe_print_progress(
                    done_n,
                    email=str(o.get("email") or ""),
                    kind=str(o.get("kind") or ""),
                )

    elapsed = _time.time() - t0
    recovered = refresh_ok + remint_ok
    failed = remint_fail + remint_skip_no_sso + fail_other
    lim = getattr(args, "limit", None)
    limit_disp = int(lim) if lim is not None and int(lim) > 0 else 0

    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} refresh done  source=auth.json  "
        f"elapsed={elapsed:.0f}s  jobs={jobs}  run_id={logutil.get_run_id()}"
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}  note")
    print(f"  {'-' * 22} {'-' * 6}  {'-' * 28}")
    print(f"  {'work_total':<22} {total:>6}  candidates this run")
    lim = getattr(args, "limit", None)
    if lim is not None and int(lim) > 0:
        print(f"  {'limit':<22} {int(lim):>6}  debug cap")
    if needs_only:
        print(f"  {'skipped_freshish':<22} {skipped_fresh:>6}  needs-only filter")
        print(f"  {'skipped_no_rt':<22} {skipped_no_rt:>6}  needs-only filter")
    print(f"  {'refresh_ok':<22} {refresh_ok:>6}  RT→AT success")
    print(f"  {'remint_ok':<22} {remint_ok:>6}  revoked→SSO mint")
    print(f"  {'recovered':<22} {recovered:>6}  refresh_ok + remint_ok")
    print(f"  {'remint_fail':<22} {remint_fail:>6}  mint failed")
    print(f"  {'remint_skip_no_sso':<22} {remint_skip_no_sso:>6}  no SSO to remint")
    print(f"  {'fail_other':<22} {fail_other:>6}  non-revoke / other errors")
    print(f"  {'failed':<22} {failed:>6}  remint_fail + skip + other")
    print("=" * 56)
    print(
        f"refresh done source=auth.json total={total} "
        f"refresh_ok={refresh_ok} remint_ok={remint_ok} recovered={recovered} "
        f"remint_fail={remint_fail} remint_skip_no_sso={remint_skip_no_sso} "
        f"fail_other={fail_other} failed={failed} "
        f"j={jobs} needs_only={1 if needs_only else 0} skew_min={skew_min} "
        f"limit={limit_disp} "
        f"elapsed={elapsed:.0f}s"
    )
    logutil.done_footer(
        "refresh",
        total=total,
        refresh_ok=refresh_ok,
        remint_ok=remint_ok,
        recovered=recovered,
        fail=failed,
        wall_s=round(elapsed, 1),
        j=jobs,
        limit=limit_disp,
    )
    logging.info(
        "refresh done source=auth.json total=%s refresh_ok=%s remint_ok=%s recovered=%s "
        "remint_fail=%s remint_skip_no_sso=%s fail_other=%s failed=%s jobs=%s elapsed=%.0fs",
        total,
        refresh_ok,
        remint_ok,
        recovered,
        remint_fail,
        remint_skip_no_sso,
        fail_other,
        failed,
        jobs,
        elapsed,
    )
    return 0 if failed == 0 else 1


def _cmd_probe_quota(cfg: dict, args: argparse.Namespace) -> int:
    """Probe token alive (/models), billing, and/or rate limits from auth.json."""
    # Live line output for Web task stream (avoid waiting until process exit)
    try:
        import sys as _sys
        if hasattr(_sys.stdout, "reconfigure"):
            _sys.stdout.reconfigure(line_buffering=True)
        if hasattr(_sys.stderr, "reconfigure"):
            _sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass
    from ..auth_pool import list_entries
    from ..oauth import probe_quota

    proxy = (args.mint_proxy or "").strip()
    if not proxy:
        # mint/probe need outbound (10808); hop1 7890 is for register mail path
        from .mint_proxy import resolve_mint_proxy

        proxy = resolve_mint_proxy(args)
    mode = (getattr(args, "probe_mode", None) or "chat").strip().lower()
    verbose = bool(getattr(args, "probe_verbose", False))
    retries = int(getattr(args, "probe_retries", 2) or 0)
    timeout = float(getattr(args, "probe_timeout", 45.0) or 45.0)
    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    limit = getattr(args, "limit", None)
    if limit is not None:
        limit = max(0, int(limit))

    target = (args.probe_quota or "").strip().lower()
    if not target:
        logging.error(
            "用法: --probe-quota all|email [-j N] "
            "[--probe-mode models|billing|chat|quota|both] "
            "[--probe-interval SEC] [--probe-retries N] [--probe-timeout SEC] [--limit N]"
        )
        return 2

    auth_path = _auth_path(cfg, args)
    if not Path(auth_path).is_file():
        logging.error("auth pool 不存在: %s （先 mint 入账）", auth_path)
        return 2

    targets: list[tuple[str, str]] = []
    for _k, entry in list_entries(auth_path, include_disabled=False, include_expired=True):
        if not isinstance(entry, dict):
            continue
        em = (entry.get("email") or "").strip()
        tok = (entry.get("access_token") or entry.get("key") or "").strip()
        if not em or not tok:
            continue
        if target not in {"", "all", "*"} and target not in em.lower():
            continue
        targets.append((em, tok))

    if not targets:
        logging.error(
            "auth.json 无匹配账号（需有 access_token）: target=%s path=%s",
            target,
            auth_path,
        )
        return 2

    if limit:
        targets = targets[:limit]

    if args.probe_interval is not None:
        interval = max(0.0, float(args.probe_interval))
    elif jobs > 1:
        interval = 0.0 if mode in ("models", "billing") else 0.3
    else:
        interval = 1.5 if target in {"all", "*"} or len(targets) > 1 else 0.0

    if jobs > 1 and mode in ("chat", "both", "quota"):
        logging.warning(
            "并行 chat/quota 共用出口易触发 soft gate；"
            "建议 models/billing 用 -j 4~8，chat/quota 用 -j 2~3 + 间隔"
        )

    logging.info(
        "probe-quota mode=%s accounts=%s source=auth.json jobs=%s interval=%.1fs "
        "retries=%s timeout=%.0fs proxy=%s",
        mode,
        len(targets),
        jobs,
        interval,
        retries,
        timeout,
        proxy,
    )
    # Always print ASAP so Web is not silent while first chat HTTP waits (timeout up to 45s)
    print(
        f"# probe start mode={mode} n={len(targets)} j={jobs} interval={interval}s "
        f"timeout={timeout}s proxy={proxy} verbose={int(verbose)}",
        flush=True,
    )
    if mode in ("chat", "both", "quota") and jobs > 3:
        print(
            f"# warn: chat/quota j={jobs} 易卡/限流；建议 j=2~3。首条结果可能需数秒~超时。",
            flush=True,
        )

    def _fmt_num(v: Any, dash: str = "-") -> str:
        if v is None or v == "":
            return dash
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return f"{v:.4g}"
        return str(v)

    if verbose:
        if mode == "models":
            print(
                f"{'email':<42} {'status':>7} {'models':>7} {'g45':>5} "
                f"{'sec':>6} {'tries':>5} err",
                flush=True,
            )
            print("-" * 100, flush=True)
        elif mode == "billing":
            print(
                f"{'email':<36} {'st':>4} {'m_lim':>6} {'used':>6} "
                f"{'on_cap':>6} {'on_used':>6} {'month':^23} {'week':^23} {'sec':>5}",
                flush=True,
            )
            print("-" * 128, flush=True)
        elif mode == "quota":
            print(
                f"{'email':<28} {'st':>4} {'class':<16} {'req':^11} {'tok':^13} "
                f"{'month':^10} {'week':^10} {'sec':>5}",
                flush=True,
            )
            print("-" * 112, flush=True)
        else:
            print(
                f"{'email':<32} {'st':>4} {'class':<16} "
                f"{'req_lim':>7} {'req_rem':>7} {'tok_lim':>9} {'tok_rem':>9} "
                f"{'sec':>5}",
                flush=True,
            )
            print("-" * 108, flush=True)
    else:
        print(
            f"# probe compact: progress + fails only (use --probe-verbose for row table) "
            f"mode={mode} n={len(targets)} j={jobs}",
            flush=True,
        )

    def _one(item: tuple[str, str]) -> dict:
        em, tok = item
        try:
            return probe_quota(
                None,
                email=em,
                access_token=tok,
                proxy=proxy,
                mode=mode,
                timeout=timeout,
                retries=retries,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "email": em,
                "status": 0,
                "error": f"EXCEPTION {exc}",
                "limits": {},
                "usage": {},
                "billing": {},
                "elapsed_sec": 0.0,
                "attempts": 0,
            }

    def _persist_probe(r: dict) -> None:
        """Write probe.class into auth.json for chat/quota (and both)."""
        if mode not in ("chat", "both", "quota"):
            return
        em = str(r.get("email") or "").strip()
        cls = str(r.get("classification") or "").strip().lower()
        if not em or not cls:
            return
        if r.get("free_usage_exhausted") and cls != "quota_exhausted":
            cls = "quota_exhausted"
        try:
            from ..auth_pool import set_probe_result

            set_probe_result(
                auth_path,
                em,
                classification=cls,
                mode=mode,
                code=str(r.get("error_code") or "")[:200],
                reason=str(r.get("reason") or r.get("error") or "")[:240],
                free_usage_tokens=r.get("free_usage_tokens"),
            )
        except Exception as exc:  # noqa: BLE001
            logging.debug("probe persist skip %s: %s", em, exc)

    def _print_row(r: dict) -> None:
        import builtins as _bi
        def print(*a, **k):  # noqa: A001 — force flush for live stream
            k.setdefault("flush", True)
            return _bi.print(*a, **k)
        """One table row only — never log mid-row (breaks alignment)."""
        email = str(r.get("email", "?"))[:40]
        if mode == "models":
            print(
                f"{email:<42} {r.get('status'):>7} "
                f"{'ok' if r.get('models_ok') else 'no':>7} "
                f"{'yes' if r.get('has_grok_45') else 'no':>5} "
                f"{r.get('elapsed_sec', 0):>6} {r.get('attempts', 1):>5} "
                f"{r.get('error') or ''}"
            )
            return

        bill = r.get("billing") or {}
        if mode == "billing":
            def _span(start: Any, end: Any) -> str:
                s = str(start or "")[:10]
                e = str(end or "")[:10]
                if s and e:
                    return f"{s}\u2192{e}"
                return s or e or "-"

            month = _span(bill.get("month_period_start"), bill.get("month_period_end"))
            week = _span(bill.get("week_period_start"), bill.get("week_period_end"))
            wtype = str(bill.get("week_period_type") or "")
            if "WEEKLY" not in wtype.upper() and wtype and week == "-":
                week = wtype.replace("USAGE_PERIOD_TYPE_", "")[:12]
            print(
                f"{email[:34]:<36} {r.get('status'):>4} "
                f"{_fmt_num(bill.get('monthly_limit')):>6} "
                f"{_fmt_num(bill.get('used')):>6} "
                f"{_fmt_num(bill.get('on_demand_cap')):>6} "
                f"{_fmt_num(bill.get('on_demand_used')):>6} "
                f"{month:^23} {week:^23} {r.get('elapsed_sec', 0):>5}"
            )
            return

        lim = r.get("limits") or {}

        def _gq_pair(q: Any) -> str:
            if not isinstance(q, dict):
                return "-/-"
            return f"{q.get('remaining', '-')}/{q.get('limit', '-')}"

        if mode == "quota":
            def _d(v: Any) -> str:
                return str(v or "")[:10] or "-"

            month = _d(bill.get("month_period_end") or bill.get("month_period_start"))
            week = _d(bill.get("week_period_end") or bill.get("week_period_start"))
            cls = str(r.get("classification") or "-")[:16]
            print(
                f"{email[:26]:<28} {r.get('status'):>4} {cls:<16} "
                f"{_gq_pair(r.get('grok_request_quota')):^11} "
                f"{_gq_pair(r.get('grok_token_quota')):^13} "
                f"{month:^10} {week:^10} {r.get('elapsed_sec', 0):>5}"
            )
            return

        cls = str(r.get("classification") or "-")[:16]
        gq_req = (
            r.get("grok_request_quota")
            if isinstance(r.get("grok_request_quota"), dict)
            else {}
        )
        gq_tok = (
            r.get("grok_token_quota")
            if isinstance(r.get("grok_token_quota"), dict)
            else {}
        )
        print(
            f"{email[:30]:<32} {r.get('status'):>4} {cls:<16} "
            f"{str(gq_req.get('limit', lim.get('x-ratelimit-limit-requests', '-'))):>7} "
            f"{str(gq_req.get('remaining', lim.get('x-ratelimit-remaining-requests', '-'))):>7} "
            f"{str(gq_tok.get('limit', lim.get('x-ratelimit-limit-tokens', '-'))):>9} "
            f"{str(gq_tok.get('remaining', lim.get('x-ratelimit-remaining-tokens', '-'))):>9} "
            f"{r.get('elapsed_sec', 0):>5}"
        )

    ok_n = fail_n = 0
    exhausted_n = rate_n = reauth_n = 0
    fail_notes: list[str] = []
    import time as _time

    def _tally(r: dict) -> None:
        nonlocal ok_n, fail_n, exhausted_n, rate_n, reauth_n
        if r.get("ok"):
            ok_n += 1
        else:
            fail_n += 1
            detail = str(r.get("error") or r.get("reason") or "").replace("\n", " ")
            if detail:
                fail_notes.append(
                    f"{r.get('email')}: {r.get('classification') or '?'} | {detail[:160]}"
                )
        c = str(r.get("classification") or "")
        if c == "quota_exhausted" or r.get("free_usage_exhausted"):
            exhausted_n += 1
        elif c == "rate_limited":
            rate_n += 1
        elif c == "reauth":
            reauth_n += 1

    results: list[dict] = []
    def _is_fail(r: dict) -> bool:
        if not r.get("ok"):
            return True
        c = str(r.get("classification") or "")
        if c in {"quota_exhausted", "reauth", "permission_denied", "probe_error"}:
            return True
        if r.get("free_usage_exhausted"):
            return True
        return False

    def _emit_result(r: dict, *, done_n: int, total_n: int) -> None:
        # default: progress always; row table only if verbose or noteworthy fail
        if verbose or _is_fail(r):
            _print_row(r)
        print(
            f"# [{done_n}/{total_n}] probe "
            f"{'ok' if r.get('ok') and not _is_fail(r) else 'fail'} "
            f"{r.get('email') or '?'} "
            f"class={r.get('classification') or '-'} "
            f"ok={ok_n} fail={fail_n}",
            flush=True,
        )

    if jobs == 1:
        total_n = len(targets)
        for i, item in enumerate(targets, 1):
            r = _one(item)
            results.append(r)
            _persist_probe(r)
            _tally(r)
            _emit_result(r, done_n=i, total_n=total_n)
            if i < len(targets) and interval > 0:
                _time.sleep(interval)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading as _threading

        # Stream each result as it completes (do not wait for full batch to print).
        total_n = len(targets)
        _row_lock = _threading.Lock()
        with ThreadPoolExecutor(max_workers=min(jobs, len(targets))) as ex:
            futmap = {}
            for i, item in enumerate(targets):
                if interval > 0 and i:
                    _time.sleep(interval)
                futmap[ex.submit(_one, item)] = item[0]
            for f in as_completed(futmap):
                em = futmap[f]
                try:
                    r = f.result()
                except Exception as exc:  # noqa: BLE001
                    r = {
                        "ok": False,
                        "email": em,
                        "status": 0,
                        "error": f"EXCEPTION {exc}",
                        "limits": {},
                        "usage": {},
                        "billing": {},
                        "elapsed_sec": 0.0,
                        "attempts": 0,
                    }
                if not isinstance(r, dict):
                    r = {"ok": False, "email": em, "status": 0, "error": "bad result"}
                if not r.get("email"):
                    r["email"] = em
                with _row_lock:
                    results.append(r)
                    _persist_probe(r)
                    _tally(r)
                    done_n = len(results)
                    _emit_result(r, done_n=done_n, total_n=total_n)

    extra = ""
    if mode in ("chat", "both", "quota"):
        extra = (
            f" free_exhausted={exhausted_n} rate_limited={rate_n} reauth={reauth_n}"
        )
    print(
        f"\nok={ok_n} fail={fail_n} total={ok_n + fail_n} "
        f"mode={mode} jobs={jobs} source=auth.json{extra}",
        flush=True,
    )
    # Footer only — never inject logging into the table body.
    if fail_notes:
        print("--- fail detail ---", flush=True)
        for line in fail_notes:
            print(line, flush=True)
    return 0 if fail_n == 0 else 1
def _cmd_auth_status(cfg: dict, args: argparse.Namespace) -> int:
    """Ledger status from auth.json (source of truth). Not cpa_export."""
    from ..auth_pool import list_entries, status_row, summarize

    auth_path = _auth_path(cfg, args)
    if not Path(auth_path).is_file():
        logging.error("auth pool 不存在: %s", auth_path)
        return 2

    only = (getattr(args, "auth_status", None) or "all").strip().lower()
    skew_min = float(getattr(args, "skew_min", 5.0) or 5.0)
    skew_sec = max(0.0, skew_min * 60.0)
    needs_only = bool(getattr(args, "needs_refresh_only", False))

    rows = [
        status_row(k, e, skew_sec=skew_sec)
        for k, e in list_entries(auth_path, include_disabled=True, include_expired=True)
    ]
    if only not in {"", "all", "*"}:
        rows = [r for r in rows if only in (r.get("email") or "").lower()]

    if needs_only:
        rows = [r for r in rows if r.get("needs_refresh") or r.get("state") == "expired"]

    limit_n = getattr(args, "limit", None)
    if limit_n is not None and int(limit_n) > 0:
        before = len(rows)
        rows = rows[: int(limit_n)]
        logging.info("auth-status --limit=%s: truncated %s → %s", limit_n, before, len(rows))
        print(f"auth-status limit={limit_n} (of {before} entries)")

    if not rows:
        msg = f"auth-status: no entries in {auth_path}"
        if only not in {"all", "*", ""}:
            msg += f" matching {only}"
        print(msg)
        return 2

    show = rows

    print(
        f"{'email':<42} {'state':<14} {'left_h':>8} {'rt':>3} {'needs':>5}  source=auth.json"
    )
    print("-" * 90)
    for r in show:
        email = (r.get("email") or "?")[:40]
        left = r.get("left_h")
        left_s = f"{left:.2f}" if isinstance(left, (int, float)) else "-"
        print(
            f"{email:<42} {str(r.get('state') or '?'):<14} {left_s:>8} "
            f"{'Y' if r.get('has_rt') else 'N':>3} "
            f"{'Y' if r.get('needs_refresh') else '-':>5}"
        )

    # Always summarize the rows we actually listed (respects only + --limit)
    s = {
        "total": len(rows),
        "fresh": sum(1 for r in rows if r.get("state") == "fresh"),
        "needs_refresh": sum(1 for r in rows if r.get("needs_refresh")),
        "expired": sum(1 for r in rows if r.get("state") == "expired"),
        "with_rt": sum(1 for r in rows if r.get("has_rt")),
    }

    print(
        f"\nsummary source={auth_path} total={s['total']} fresh={s['fresh']} "
        f"needs_refresh={s['needs_refresh']} expired={s['expired']} "
        f"with_rt={s.get('with_rt', '-')} skew_min={skew_min}"
    )
    return 1 if s["needs_refresh"] or s["expired"] else 0

