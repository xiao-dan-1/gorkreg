"""Register / batch / cloudmail CLI commands.

Also re-exports summary/mail/exp/sso command symbols so ``grokreg.cli`` and
legacy imports keep a stable ``register_cmds`` surface.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import logutil
from ..client import GrokAuthClient
from ..mail import parse_mail_line
from .. import mail_marks
from ..mail_cloudmail import allocate_cloudmail_address
from ..pipeline.register import (
    RegisterOptions,
    annotate_result_error as _annotate_result_error,
    register_one,
    result_ok as _result_ok,
    result_retryable as _result_retryable,
)
from ..proxyutil import resolve_proxy
from ..sso import parse_sso_jwt_payload

log = logging.getLogger(__name__)

from .env_cmds import run_proxy_preflight as _run_proxy_preflight
from .ledger_ops import (
    append_sso_roster,
    record_register_success,
    save_result as _save_result,
    summary_error_bucket as _summary_error_bucket,
)
from .summary_cmds import (
    _load_account_jsons,
    _cmd_summary,
    _cmd_check_sso,
    _cmd_sso_audit,
    _cmd_recover_sso_roster,
    _cmd_migrate_sso_roster,
    _cmd_migrate_account_evidence,
)
from .mail_cmds import (
    _cmd_mail_marks_list,
    _cmd_mail_mark,
    _cmd_mail_unmark,
    _cmd_mail_pool_status,
    _parse_mail_sources_arg,
)
# exp_cmds imported lazily via __getattr__ to avoid cycle with _cmd_exp_round → _cmd_batch
def _cmd_register_cloudmail(cfg: dict, proxy_override: Optional[str], args: argparse.Namespace) -> int:
    """Allocate catch-all CloudMail address then full register."""
    try:
        email = allocate_cloudmail_address(cfg)
    except Exception as exc:
        logging.error("cloudmail alloc failed: %s", exc)
        return 2
    logging.info("cloudmail allocated email=%s", email)
    # force cloudmail backend
    args.mail_backend = "cloudmail"
    args.register = email
    return _cmd_register(cfg, proxy_override, args)


def _resolve_captcha_backend(cfg: dict, args: argparse.Namespace, *, default: str = "auto") -> str:
    """Plugin captcha name: CLI > env CAPTCHA_BACKEND > config captcha.backend > default.

    Names: auto | yescaptcha | capsolver | twocaptcha (aliases yes/yc/cap/cs/2captcha/tc).
    Env is applied in load_config → captcha.backend; CLI still wins when set.
    """
    from ..config import normalize_captcha_backend

    raw = (getattr(args, "captcha_backend", None) or "").strip().lower()
    if not raw:
        cap = cfg.get("captcha") if isinstance(cfg.get("captcha"), dict) else {}
        raw = str((cap or {}).get("backend") or default).strip().lower()
    return normalize_captcha_backend(raw, default=default)


def _cmd_register(cfg: dict, proxy_override: Optional[str], args: argparse.Namespace) -> int:
    """完整注册：scrape → create code → 收码 → verify → turnstile → create → sso。"""
    raw = (args.register or "").strip()
    if "----" not in raw and "@" not in raw:
        logging.error("无效邮箱: %s", raw)
        return 2

    browser_cfg = cfg.get("browser_turnstile") or {}
    captcha_backend = _resolve_captcha_backend(cfg, args, default="auto")
    opts = RegisterOptions(
        password=(args.password or "").strip(),
        given_name=(args.given_name or "Jennifer").strip() or "Jennifer",
        family_name=(args.family_name or "Mitchell").strip() or "Mitchell",
        code=(args.code or "").strip(),
        skip_create=bool(args.skip_create),
        signup_email=(getattr(args, "signup_email", None) or "").strip(),
        captcha_backend=captcha_backend,
        mail_backend=getattr(args, "mail_backend", None) or "graph",
        mail_api_url=getattr(args, "mail_api_url", None) or "https://outlook.xdauv.xyz",
        turnstile_token=(args.turnstile_token or "").strip(),
        castle_token=(args.castle_token or ""),
        yescaptcha_key=args.yescaptcha_key,
        twocaptcha_key=args.twocaptcha_key,
        browser_turnstile=bool(args.browser_turnstile or browser_cfg.get("enabled")),
        browser_channel=(args.browser_channel or browser_cfg.get("channel") or "chrome"),
        browser_headless=bool(
            args.browser_headless
            if args.browser_turnstile
            else browser_cfg.get("headless", False)
        ),
        local_solver_url=(
            (args.local_solver_url or "").strip()
            or str(browser_cfg.get("local_solver_url") or "").strip()
            or None
        ),
        manual_turnstile=bool(args.manual_turnstile),
        verbose=bool(args.verbose),
        require_captcha_config=True,
        captcha_balance_check=not bool(getattr(args, "skip_captcha_balance_check", False)),
        scrape_cache=not bool(getattr(args, "no_scrape_cache", False)),
        scrape_cache_ttl=float(getattr(args, "scrape_cache_ttl", 600.0) or 600.0),
    )
    result = register_one(cfg, raw, proxy_override, opts)
    if result.get("error") == "missing_turnstile_config":
        return 2
    if result.get("error") == "no_code_and_no_mail_oauth":
        # 与 baseline 一致：配置错误直接 exit 2，不落盘
        return 2
    if result.get("error") in (
        "invalid_email",
        "invalid_signup_email",
        "signup_email_needs_mail_line_or_code",
    ):
        logging.error("无效注册参数: %s (%s)", raw, result.get("error"))
        return 2
    if result.get("is_alias"):
        logging.info(
            "outlook-alias signup=%s mail_root=%s",
            result.get("signup_email") or result.get("email"),
            result.get("mail_root") or "-",
        )

    rec = record_register_success(cfg, result, output=getattr(args, "output", None))
    path = rec["path"]
    logging.info("结果写入 %s roster_appended=%s", path, rec.get("roster_appended"))
    if opts.skip_create and not result.get("error"):
        return 0
    return 0 if result.get("sso") and not result.get("error") else 1



def _name_from_email(email: str) -> tuple[str, str]:
    local = (email or "User").split("@", 1)[0]
    m = re.match(r"^([A-Za-z]+)([A-Za-z]+)(\d*)$", local)
    if m and len(m.group(1)) >= 2 and len(m.group(2)) >= 2:
        return m.group(1).title(), m.group(2).title()
    cleaned = re.sub(r"\d+", "", local) or "User"
    if len(cleaned) >= 6:
        mid = len(cleaned) // 2
        return cleaned[:mid].title(), cleaned[mid:].title()
    return cleaned.title() or "User", "Account"




def _read_mail_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if "----" in s:
            lines.append(s)
            continue
        # bare email for cloudmail / pre-supplied code paths
        if "@" in s and " " not in s:
            lines.append(s)
            continue
        logging.warning("跳过非 mail line: %s", s[:40])
    return lines


def _existing_sso_emails(cfg: dict) -> set[str]:
    emails: set[str] = set()
    for r in _load_account_jsons(cfg):
        if r.get("sso") and r.get("email"):
            emails.add(str(r["email"]).lower())
    return emails

def _cmd_batch(cfg: dict, proxy_override: Optional[str], args: argparse.Namespace) -> int:
    """批量注册：串行（默认）或 -j N 并发。"""
    batch_path = Path(args.batch)
    if not batch_path.exists():
        logging.error("批量文件不存在: %s", batch_path)
        return 2
    lines = _read_mail_lines(batch_path)
    if not lines:
        logging.error("批量文件无有效 mail line: %s", batch_path)
        return 2

    skip_existing = bool(getattr(args, "skip_existing", False))
    existing = _existing_sso_emails(cfg) if skip_existing else set()
    if skip_existing:
        logutil.info("batch", phase="skip-existing", sso_n=len(existing))

    use_marks = not bool(getattr(args, "ignore_mail_marks", False))
    marked = mail_marks.skipped_set() if use_marks else set()
    if use_marks and marked:
        logutil.info("batch", phase="mail-marks", skip_n=len(marked))

    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    delay = max(0.0, float(args.batch_delay or 0))
    logutil.info(
        "batch",
        phase="start",
        count=len(lines),
        jobs=jobs,
        delay_s=delay,
        fixed_proxy=1 if args.fixed_proxy else 0,
        skip_existing=1 if skip_existing else 0,
        mail_marks=1 if use_marks else 0,
    )

    if not bool(getattr(args, "skip_proxy_preflight", False)):
        rc_pf = _run_proxy_preflight(cfg, proxy_override, require_ok=True, full_probe=True)
        if rc_pf != 0:
            return rc_pf
    else:
        logutil.warning("proxy-preflight", skipped=1, reason="--skip-proxy-preflight")

    # Build the todo list: [(index, mail_line, given, family)]
    todos: list[tuple[int, str, str, str]] = []
    skipped = 0
    for i, line in enumerate(lines, 1):
        try:
            if "----" in line:
                parts = [x.strip() for x in line.split("----")]
                if len(parts) >= 2 and parts[1].lower() in {
                    "cloudmail", "cm", "catch-all", "catchall"
                }:
                    email = parts[0]
                    if "@" not in email:
                        raise ValueError("invalid cloudmail email")
                else:
                    account = parse_mail_line(line)
                    email = account["email"]
            elif "@" in line:
                email = line.strip()
            else:
                raise ValueError("not a mail line")
        except Exception as exc:
            logging.warning("跳过无效 mail 行 [%s/%s]: %s", i, len(lines), exc)
            continue
        if use_marks and email.lower() in marked:
            m = mail_marks.get_mark(email) or {}
            logging.info(
                "[%s/%s] SKIP %s (mail_mark %s: %s)",
                i, len(lines), email,
                m.get("status") or "dead",
                (m.get("reason") or m.get("code") or "-")[:80],
            )
            skipped += 1
            continue
        if skip_existing and email.lower() in existing:
            logging.info("[%s/%s] SKIP %s (已有 sso)", i, len(lines), email)
            skipped += 1
            continue
        given, family = _name_from_email(email)
        if args.given_name and args.given_name != "Jennifer":
            given = args.given_name
        if args.family_name and args.family_name != "Mitchell":
            family = args.family_name
        todos.append((i, line, given, family))

    if not todos:
        logutil.info("batch", phase="done", ok=0, fail=0, total=0, skipped=skipped, note="empty-todo")
        return 0


    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import Counter

    _result_lock = threading.Lock()
    results: list[dict] = []
    fail = 0
    ok_n = 0
    t_start = time.time()
    total_work = len(todos)
    fail_buckets: Counter[str] = Counter()
    done_n = 0

    print(
        f"{logutil.icon('start')} register  run_id={logutil.get_run_id()}  "
        f"total={total_work} jobs={jobs} skipped={skipped} "
        f"backend={getattr(args, 'mail_backend', None) or 'graph'}",
        flush=True,
    )
    logutil.info(
        "batch",
        phase="start",
        total=total_work,
        j=jobs,
        skipped=skipped,
        icon="start",
    )

    def _reg_kind(r: dict, ok: bool) -> str:
        if ok:
            return "register_ok"
        code = (r.get("error_code") or "").strip()
        if code:
            return code[:40]
        return _summary_error_bucket(r.get("error"))

    def _batch_progress(email: str, kind: str) -> None:
        nonlocal done_n
        done_n += 1
        elapsed = max(0.001, time.time() - t_start)
        rate = done_n / elapsed
        eta = max(0, total_work - done_n) / rate if rate > 0 else 0.0
        force = kind not in {"register_ok", "ok"}
        line = logutil.print_progress(
            done_n,
            total_work,
            kind=kind,
            email=email,
            counters={"ok": ok_n, "fail": fail},
            rate=rate,
            eta_s=eta,
            elapsed_s=elapsed,
            force=force,
        )
        if line:
            logutil.info(
                "register",
                phase="progress",
                done=done_n,
                total=total_work,
                kind=kind,
            )

    def _reg_worker(index: int, mail_line: str, given: str, family: str) -> dict:
        """Per-thread registration via pipeline.register_one."""
        captcha_backend = _resolve_captcha_backend(cfg, args, default="auto")
        opts = RegisterOptions(
            password=(args.password or "").strip(),
            given_name=given,
            family_name=family,
            captcha_backend=captcha_backend,
            mail_backend=getattr(args, "mail_backend", None) or "graph",
            mail_api_url=getattr(args, "mail_api_url", None) or "https://outlook.xdauv.xyz",
            castle_token=(args.castle_token or ""),
            yescaptcha_key=args.yescaptcha_key,
            twocaptcha_key=args.twocaptcha_key,
            browser_turnstile=bool(getattr(args, "browser_turnstile", False)),
            browser_channel=getattr(args, "browser_channel", None) or "chrome",
            browser_headless=bool(getattr(args, "browser_headless", False)),
            local_solver_url=(getattr(args, "local_solver_url", None) or None),
            manual_turnstile=bool(getattr(args, "manual_turnstile", False)),
            turnstile_token=(getattr(args, "turnstile_token", None) or "").strip(),
            verbose=bool(args.verbose),
            proxy_probe_retries=3,
            create_short_body_retries=3,
            concurrent_jitter=(jobs > 1),
            index=index,
            require_captcha_config=False,
            captcha_balance_check=not bool(getattr(args, "skip_captcha_balance_check", False)),
            scrape_cache=not bool(getattr(args, "no_scrape_cache", False)),
            scrape_cache_ttl=float(getattr(args, "scrape_cache_ttl", 600.0) or 600.0),
        )
        result = register_one(cfg, mail_line, proxy_override, opts)
        _annotate_result_error(result)

        # Thread-safe dual-write: output evidence + sso_roster (no password)
        with _result_lock:
            record_register_success(cfg, result, output=getattr(args, "output", None))
        return result

    if jobs == 1:
        # Serial (original behaviour)
        for i, line, given, family in todos:
            r = _reg_worker(i, line, given, family)
            _annotate_result_error(r)
            ok = _result_ok(r)
            results.append(r)
            if ok:
                ok_n += 1
            else:
                fail += 1
                fail_buckets[_reg_kind(r, False)] += 1
            kind = _reg_kind(r, ok)
            logutil.info(
                "register",
                ok=1 if ok else 0,
                email=r.get("email") or "-",
                code=(r.get("error_code") or ("ok" if ok else "fail")),
                ms=int(float(r.get("elapsed_sec") or 0) * 1000),
                i=i,
            )
            _batch_progress(str(r.get("email") or line.split("----")[0]), kind)
            if delay > 0:
                time.sleep(delay)
    else:
        with ThreadPoolExecutor(max_workers=min(jobs, len(todos))) as ex:
            futmap = {
                ex.submit(_reg_worker, i, line, given, family): (i, line, given, family)
                for i, line, given, family in todos
            }
            for f in as_completed(futmap):
                idx, line, given, family = futmap[f]
                r = f.result()
                _annotate_result_error(r)
                ok = _result_ok(r)
                results.append(r)
                if ok:
                    ok_n += 1
                else:
                    fail += 1
                    fail_buckets[_reg_kind(r, False)] += 1
                kind = _reg_kind(r, ok)
                logutil.info(
                    "register",
                    ok=1 if ok else 0,
                    email=r.get("email") or line.split("----")[0],
                    code=(r.get("error_code") or ("ok" if ok else "fail")),
                    ms=int(float(r.get("elapsed_sec") or 0) * 1000),
                    i=idx,
                )
                _batch_progress(str(r.get("email") or line.split("----")[0]), kind)

    # Serial retry pass for retryable failures (proxy/mail/captcha jitter)
    retry_rounds = max(0, int(getattr(args, "retry_failed", 1) or 0))
    todo_by_email = {
        (line.split("----")[0] or "").strip().lower(): (i, line, given, family)
        for i, line, given, family in todos
    }
    for round_i in range(1, retry_rounds + 1):
        retry_items: list[tuple[int, str, str, str, dict]] = []
        for r in results:
            if not _result_retryable(r):
                continue
            em = (r.get("email") or "").strip().lower()
            if em not in todo_by_email:
                continue
            i, line, given, family = todo_by_email[em]
            retry_items.append((i, line, given, family, r))
        if not retry_items:
            break
        logutil.info(
            "batch",
            phase="retry",
            round=round_i,
            count=len(retry_items),
            codes=",".join(sorted({str(x[4].get("error_code") or "?") for x in retry_items})),
        )
        for i, line, given, family, old in retry_items:
            r = _reg_worker(i, line, given, family)
            _annotate_result_error(r)
            # replace old result in-place
            for idx, prev in enumerate(results):
                if (prev.get("email") or "").lower() == (r.get("email") or "").lower():
                    results[idx] = r
                    break
            else:
                results.append(r)
            ok = _result_ok(r)
            logutil.info(
                "register",
                phase="retry",
                round=round_i,
                ok=1 if ok else 0,
                email=r.get("email") or "-",
                was=old.get("error_code") or old.get("error") or "-",
                code=r.get("error_code") or r.get("error") or ("ok" if ok else "fail"),
                ms=int(float(r.get("elapsed_sec") or 0) * 1000),
            )
            if delay > 0:
                time.sleep(delay)

    # recompute tallies after retries
    ok_n = sum(1 for r in results if _result_ok(r))
    fail = sum(1 for r in results if not _result_ok(r))
    fail_buckets = Counter()
    for r in results:
        if not _result_ok(r):
            fail_buckets[_reg_kind(r, False)] += 1

    # Auto-mark permanent mailbox deaths; mail_timeout uses strike counter
    # (1=watch still eligible, 2=hold skip, 3=dead). Never auto-dead on first timeout.
    for r in results:
        em = (r.get("email") or "").strip()
        if not em:
            continue
        try:
            entry = mail_marks.note_batch_result(
                em,
                ok=_result_ok(r),
                error_code=r.get("error_code"),
                error=r.get("error"),
                retryable=r.get("retryable"),
                source="auto_batch",
            )
            if entry and not _result_ok(r):
                logutil.info(
                    "mail",
                    phase="policy",
                    email=em,
                    status=entry.get("status"),
                    strikes=entry.get("timeout_strikes"),
                    code=entry.get("code"),
                )
        except Exception as e:
            logging.warning("mail mark policy failed %s: %s", em, e)

    # Summary: # progress already printed; dashboard + buckets + optional detail
    total = ok_n + fail
    elapsed = time.time() - t_start
    rate = 100.0 * ok_n / max(total, 1)
    logutil.info(
        "batch",
        phase="done",
        ok=ok_n,
        fail=fail,
        total=total,
        rate_pct=round(rate, 1),
        skipped=skipped,
        jobs=jobs,
        wall_s=round(elapsed, 1),
        backend=getattr(args, "mail_backend", None) or "graph",
        icon="done",
    )
    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} register done  "
        f"elapsed={elapsed:.0f}s  jobs={jobs}  run_id={logutil.get_run_id()}"
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}  note")
    print(f"  {'-' * 22} {'-' * 6}  {'-' * 28}")
    print(f"  {'work_total':<22} {total:>6}  attempted this run")
    print(f"  {'skipped':<22} {skipped:>6}  marks / existing sso")
    print(f"  {'ok':<22} {ok_n:>6}  sso + no error")
    print(f"  {'fail':<22} {fail:>6}  registration failed")
    print(f"  {'ok_rate%':<22} {rate:>6.1f}")
    if fail_buckets:
        print(f"  {'-' * 22} {'-' * 6}")
        for b, n in fail_buckets.most_common(12):
            print(f"  {b[:22]:<22} {n:>6}  fail bucket")
    print("=" * 56)
    logutil.done_footer(
        "register",
        total=total,
        ok=ok_n,
        fail=fail,
        skipped=skipped,
        wall_s=round(elapsed, 1),
        j=jobs,
        ok_rate=round(rate, 1),
    )
    # detail rows (keep; useful for small batches)
    for r in results:
        email = r.get("email", "?")
        tag = "OK" if _result_ok(r) else "FAIL"
        err = r.get("error", "-")
        code = r.get("error_code") or ""
        short_n = int(r.get("short_body_hits") or 0)
        short_tag = f" short={short_n}" if short_n else ""
        extra = f" [{code}]" if code and tag == "FAIL" else ""
        ic = logutil.icon("ok" if tag == "OK" else "fail")
        print(
            f"  {ic} {tag:4} {email:<42} {float(r.get('elapsed_sec') or 0):6.1f}s"
            f"{short_tag}  {err if tag=='FAIL' else '-'}{extra}"
        )

    # Always record wall/stage timings + primary bottleneck for optimization
    try:
        from ..bench import record_batch

        mb = getattr(args, "mail_backend", None) or "graph"
        note = str(getattr(args, "exp_note", "") or "")
        rec = record_batch(
            results=results,
            jobs=jobs,
            mail_backend=mb,
            mail_api_url=getattr(args, "mail_api_url", None),
            wall_sec=elapsed,
            ok=ok_n,
            fail=fail,
            skipped=skipped,
            batch_file=str(getattr(args, "batch", "") or ""),
            note=note,
            extra={
                "retry_failed": int(getattr(args, "retry_failed", 0) or 0),
                "exp_name": getattr(args, "exp_name", None),
                "exp_round": getattr(args, "exp_round_no", None),
            },
        )
        print(
            f"bench: rate={rec.get('success_rate')}% wall={rec.get('wall_sec')}s "
            f"per_ok={rec.get('wall_per_ok_sec')} short%={rec.get('short_body_rate')} "
            f"primary={rec.get('primary_factor')}"
        )
        if rec.get("fail_buckets"):
            print(f"bench: fail_buckets={rec.get('fail_buckets')}")
        stage = {k: v for k, v in (rec.get("stage_mean_sec") or {}).items() if v}
        if stage:
            top = sorted(stage.items(), key=lambda kv: kv[1], reverse=True)[:4]
            print("bench: stage_mean", " ".join(f"{k}={v:.1f}s" for k, v in top))
        print("bench: wrote output/bench_runs.jsonl + output/bench_summary.md")
        for a in rec.get("accounts") or []:
            em = (a.get("email") or "?").split("@")[0]
            sh = int(a.get("short_body_hits") or 0)
            print(
                f"  factor {em:28} {'OK' if a.get('ok') else 'FAIL':4} "
                f"{float(a.get('elapsed_sec') or 0):6.1f}s  "
                f"short={sh}  {a.get('factor')}"
            )
        # stash for experiment wrapper
        args._last_bench_rec = rec  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("bench record skipped: %s", e)

    return 0 if fail == 0 else 1




def __getattr__(name: str):
    """Lazy re-export exp cmds (breaks import cycle with exp_round → batch)."""
    if name in {
        "_cmd_bench_backfill",
        "_cmd_bench_show",
        "_cmd_exp_status",
        "_cmd_exp_summary",
        "_cmd_exp_round",
    }:
        from . import exp_cmds

        return getattr(exp_cmds, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "_cmd_register",
    "_cmd_register_cloudmail",
    "_cmd_batch",
    "_cmd_summary",
    "_cmd_check_sso",
    "_cmd_sso_audit",
    "_cmd_recover_sso_roster",
    "_cmd_migrate_sso_roster",
    "_cmd_migrate_account_evidence",
    "_cmd_mail_marks_list",
    "_cmd_mail_mark",
    "_cmd_mail_unmark",
    "_cmd_mail_pool_status",
    "_cmd_bench_backfill",
    "_cmd_bench_show",
    "_cmd_exp_status",
    "_cmd_exp_summary",
    "_cmd_exp_round",
    "_save_result",
    "_summary_error_bucket",
]
