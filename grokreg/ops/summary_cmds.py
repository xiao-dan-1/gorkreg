"""Summary / check-sso / SSO ledger audit CLI commands."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from .. import logutil
from ..sso import parse_sso_jwt_payload

log = logging.getLogger(__name__)

from .ledger_ops import (
    account_evidence_dir,
    iter_account_evidence_paths,
    resolve_auth_path,
    summary_error_bucket as _summary_error_bucket,
)


def _output_dir(cfg: dict) -> Path:
    return Path(cfg.get("_root") or ".") / "output"


def _project_root(cfg: dict) -> Path:
    return Path(cfg.get("_root") or ".")


def _load_account_jsons(cfg: dict) -> list[dict]:
    rows: list[dict] = []
    for path in iter_account_evidence_paths(cfg=cfg):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        data["_path"] = str(path)
        rows.append(data)
    return rows


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


def _cmd_summary(cfg: dict, args: argparse.Namespace | None = None) -> int:
    """Inventory dashboard from **auth.json only** (credential ledger SoT).

    sso_roster / output/accounts are **not** inventory sources here —
    use --sso-audit / account files for register evidence.
    """
    from ..auth_pool import list_entries, status_row, summarize

    t0 = time.time()
    auth_path = resolve_auth_path(cfg, args)
    if not auth_path.is_file():
        logging.error(
            "auth pool 不存在: %s  （先 mint 或 --auth-import cpa_export）",
            auth_path,
        )
        print(f"summary: auth.json missing → {auth_path}")
        print("hint: python main.py --mint all --mint-missing  或  --auth-import cpa_export")
        return 2

    skew_min = 5.0
    if args is not None:
        skew_min = float(getattr(args, "skew_min", 5.0) or 5.0)
    skew_sec = max(0.0, skew_min * 60.0)

    domain_filter = ""
    if args is not None:
        domain_filter = (getattr(args, "summary_domain", None) or "").strip().lower()

    raw_rows = [
        status_row(k, e, skew_sec=skew_sec)
        for k, e in list_entries(auth_path, include_disabled=True, include_expired=True)
    ]
    # attach type/platform from entry when available
    entries_map = {
        k: e
        for k, e in list_entries(auth_path, include_disabled=True, include_expired=True)
    }
    for r in raw_rows:
        ent = entries_map.get(r.get("key") or "") or {}
        r["type"] = str(ent.get("type") or ent.get("auth_kind") or "")[:20]
        r["platform"] = str(ent.get("platform") or "")[:20]

    def _dom_ok(email: str) -> bool:
        if not domain_filter:
            return True
        em = (email or "").lower()
        return em.endswith("@" + domain_filter) or domain_filter in em

    rows = [r for r in raw_rows if _dom_ok(r.get("email") or "")]

    # unique by email (last wins — list_entries already sorted by exp)
    by_email: dict[str, dict] = {}
    for r in rows:
        em = (r.get("email") or "").strip().lower()
        if not em:
            # keep key-only rows under synthetic id
            em = f"(no-email){(r.get('key') or '')[:24]}"
        by_email[em] = r
    uniq = list(by_email.values())

    total = len(uniq)
    fresh = sum(1 for r in uniq if r.get("state") == "fresh")
    expired = sum(1 for r in uniq if r.get("state") == "expired")
    # needs_refresh flag is true for expired+rt too; "needs" metric = non-expired needs
    needs = sum(
        1
        for r in uniq
        if r.get("needs_refresh") and r.get("state") not in {"expired", "disabled"}
    )
    disabled = sum(1 for r in uniq if r.get("disabled") or r.get("state") == "disabled")
    with_rt = sum(1 for r in uniq if r.get("has_rt"))
    with_at = sum(1 for r in uniq if r.get("has_at"))
    no_at = sum(1 for r in uniq if r.get("state") == "no_at")
    quota_ex = sum(1 for r in uniq if (r.get("probe_class") or "") == "quota_exhausted")

    by_domain: dict[str, dict[str, int]] = {}
    by_state: dict[str, int] = {}
    by_type: dict[str, int] = {}
    left_vals: list[float] = []

    for r in uniq:
        email = (r.get("email") or "").strip().lower()
        dom = email.split("@", 1)[-1] if "@" in email else "(none)"
        d = by_domain.setdefault(dom, {"total": 0, "fresh": 0, "needs": 0, "expired": 0})
        d["total"] += 1
        st = str(r.get("state") or "?")
        by_state[st] = by_state.get(st, 0) + 1
        if st == "fresh":
            d["fresh"] += 1
        if r.get("needs_refresh") and st not in {"expired", "disabled"}:
            d["needs"] += 1
        if st == "expired":
            d["expired"] += 1
        t = str(r.get("type") or "(none)") or "(none)"
        by_type[t] = by_type.get(t, 0) + 1
        lh = r.get("left_h")
        if isinstance(lh, (int, float)):
            left_vals.append(float(lh))

    out_dir = _output_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "email",
                "state",
                "left_h",
                "has_rt",
                "has_at",
                "needs_refresh",
                "disabled",
                "type",
                "probe_class",
                "sub",
                "key",
            ]
        )
        for r in sorted(uniq, key=lambda x: (x.get("email") or "")):
            w.writerow(
                [
                    r.get("email") or "",
                    r.get("state") or "",
                    r.get("left_h") if r.get("left_h") is not None else "",
                    str(bool(r.get("has_rt"))).lower(),
                    str(bool(r.get("has_at"))).lower(),
                    str(bool(r.get("needs_refresh"))).lower(),
                    str(bool(r.get("disabled"))).lower(),
                    r.get("type") or "",
                    r.get("probe_class") or "",
                    r.get("sub") or "",
                    (r.get("key") or "")[:80],
                ]
            )

    wall = time.time() - t0
    p50_left = p95_left = None
    if left_vals:
        lv = sorted(left_vals)
        p50_left = lv[len(lv) // 2]
        p95_left = lv[min(len(lv) - 1, int(len(lv) * 0.95))]

    # --- dashboard ---
    print(f"summary  source=auth.json  file={auth_path}")
    print("+------------------+----------+")
    print(f"| {'metric':<16} | {'value':>8} |")
    print("+------------------+----------+")
    for k, v in (
        ("total", total),
        ("fresh", fresh),
        ("needs_refresh", needs),
        ("expired", expired),
        ("disabled", disabled),
        ("no_at", no_at),
        ("with_rt", with_rt),
        ("with_at", with_at),
        ("quota_ex", quota_ex),
    ):
        print(f"| {k:<16} | {str(v):>8} |")
    if p50_left is not None:
        print(f"| {'left_h_p50':<16} | {p50_left:>7.2f}h |")
        print(f"| {'left_h_p95':<16} | {p95_left:>7.2f}h |")
    print(f"| {'skew_min':<16} | {skew_min:>8} |")
    print(f"| {'wall_s':<16} | {wall:>8.3f} |")
    print("+------------------+----------+")
    print("note: 唯一库存源 = auth.json（凭证账本）。注册证据见 account_*.json；SSO 花名册见 sso_roster + --sso-audit")
    if domain_filter:
        print(f"filter domain~={domain_filter}")

    if by_domain:
        print("\nby domain:")
        print(f"  {'domain':<28} {'total':>6} {'fresh':>6} {'needs':>6} {'exp':>6}")
        print("  " + "-" * 56)
        for dom, d in sorted(by_domain.items(), key=lambda x: -x[1]["total"]):
            print(
                f"  {dom:<28} {d['total']:>6} {d['fresh']:>6} {d['needs']:>6} {d['expired']:>6}"
            )

    if by_state:
        print("\nby state:")
        print(f"  {'state':<16} {'n':>6}")
        print("  " + "-" * 24)
        for st, n in sorted(by_state.items(), key=lambda x: -x[1]):
            print(f"  {st:<16} {n:>6}")

    if by_type and any(t != "(none)" and t for t in by_type):
        print("\nby type:")
        print(f"  {'type':<16} {'n':>6}")
        print("  " + "-" * 24)
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"  {t:<16} {n:>6}")

    full = bool(args and getattr(args, "summary_full", False))
    limit = int(getattr(args, "summary_limit", 30) or 0) if args else 30
    if full:
        print("\nentries:")
        print(f"{'email':<42} {'state':<14} {'left_h':>8} {'rt':>3} {'needs':>5}")
        print("-" * 80)
        show = sorted(uniq, key=lambda x: (x.get("email") or ""))
        if limit > 0:
            shown = show[:limit]
        else:
            shown = show
        for r in shown:
            left = r.get("left_h")
            left_s = f"{left:.2f}" if isinstance(left, (int, float)) else "-"
            print(
                f"{(r.get('email') or '?')[:41]:<42} {str(r.get('state') or '?'):<14} "
                f"{left_s:>8} {'Y' if r.get('has_rt') else 'N':>3} "
                f"{'Y' if r.get('needs_refresh') else '-':>5}"
            )
        if limit > 0 and len(show) > limit:
            print(f"… {len(show) - limit} more (CSV or --summary-limit 0)")

    print(f"\ncsv → {csv_path}")
    print(
        f"summary done source=auth.json total={total} fresh={fresh} "
        f"needs_refresh={needs} expired={expired} with_rt={with_rt} wall_s={wall:.3f}"
    )
    logging.info(
        "summary → %s auth=%s total=%s fresh=%s needs=%s expired=%s wall=%.3fs",
        csv_path,
        auth_path,
        total,
        fresh,
        needs,
        expired,
        wall,
    )
    return 0


def _cmd_check_sso(cfg: dict, target: str) -> int:
    rows = _load_account_jsons(cfg)
    if not rows:
        logging.info("output/accounts/ 下没有 account_*.json（含 legacy output/ 根）")
        return 2

    row: Optional[dict] = None
    path: Optional[Path] = None
    target_lower = target.lower().strip()

    if target_lower.endswith(".json"):
        for p in sorted(Path(target_lower).parent.glob(Path(target_lower).name)):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    row = data
                    path = p
                    break
            except Exception:
                continue
    if not row and "@" in target_lower:
        email = target_lower
        for r in reversed(_load_account_jsons(cfg)):
            if (r.get("email") or "").lower() == email:
                row = r
                path = Path(r.get("_path") or "")
                break
    if not row:
        logging.error("找不到账号: %s", target)
        return 2

    email = row.get("email")
    sso = row.get("sso") or ""
    password = row.get("password") or ""
    print(f"email        = {email}")
    print(f"result_file  = {path}")
    print(f"has_password = {bool(password)} len={len(password)} prefix={(password[:4] if password else '-')}")
    print(f"has_sso      = {bool(sso)} len={len(sso)}")
    print(f"session_id   = {row.get('session_id')}")
    print(f"elapsed_sec  = {row.get('elapsed_sec')}")
    print(f"error        = {row.get('error')}")
    print(f"timings      = {row.get('timings_sec')}")
    if not sso:
        print("SSO 校验: FAIL (无 sso，账号可能未完整创建)")
        print("登录提示: 不要用 Outlook 邮箱密码；无 sso 时 Grok 密码也未必有效")
        return 1

    payload = parse_sso_jwt_payload(sso) or {}
    exp = payload.get("exp")
    now = int(time.time())
    has_session = bool(payload.get("session_id") or payload.get("sid") or row.get("session_id"))
    if isinstance(exp, int):
        exp_ok = exp > now
        left = exp - now
    else:
        exp_ok = True
        left = None
    ok = bool(sso) and has_session and exp_ok
    print(f"jwt_keys     = {sorted(payload.keys())}")
    print(f"jwt_session  = {payload.get('session_id') or payload.get('sid')}")
    print(f"jwt_exp      = {exp} exp_ok={exp_ok} left_sec={left}")
    print("SSO 校验: " + ("PASS" if ok else "FAIL"))
    print("登录提示: 用结果 JSON 的 password 登录 accounts.x.ai / grok.com；Outlook 密码无效")
    return 0 if ok else 1


def _cmd_sso_audit(cfg: dict, args: argparse.Namespace) -> int:
    """Print SSO three-sink audit (cli / output / auth.json)."""
    from .ledger_ops import audit_sso_ledgers, resolve_auth_path as _rap

    root = Path(cfg.get("_root") or ".")
    cli_path = root / "sso_roster.txt"
    out_dir = root / "output"
    auth_path = _rap(cfg, args)
    stats = audit_sso_ledgers(cli_path=cli_path, output_dir=out_dir, auth_path=auth_path)
    c = stats["counts"]
    print(
        f"sso-audit cli={c['cli']} output_sso={c['output_sso']} auth={c['auth']} "
        f"in_all_three={c['in_all_three']}"
    )
    print(
        f"  in_output_not_cli={c['in_output_not_cli']}  → --recover-sso-roster"
    )
    print(
        f"  in_cli_not_auth={c['in_cli_not_auth']}  → --mint all --mint-missing"
    )
    print(
        f"  in_auth_not_cli={c['in_auth_not_cli']}  → remint needs SSO recover first"
    )
    for label, key in (
        ("output_not_cli", "in_output_not_cli"),
        ("cli_not_auth", "in_cli_not_auth"),
        ("auth_not_cli", "in_auth_not_cli"),
    ):
        rows = stats.get(key) or []
        if not rows:
            continue
        show = rows[:10]
        more = len(rows) - len(show)
        tail = f" …+{more}" if more > 0 else ""
        print(f"  sample_{label}: {', '.join(show)}{tail}")
    if c["in_output_not_cli"] or c["in_cli_not_auth"]:
        return 1
    return 0


def _cmd_recover_sso_roster(cfg: dict, args: argparse.Namespace) -> int:
    """Backfill sso_roster from output/account_*.json (SSO evidence recover)."""
    from .ledger_ops import recover_sso_roster_from_output

    root = Path(cfg.get("_root") or ".")
    out_dir = Path(getattr(args, "recover_output_dir", None) or "output")
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    cli_path = root / "sso_roster.txt"
    dry = bool(getattr(args, "recover_dry_run", False) or getattr(args, "dry_run", False))
    stats = recover_sso_roster_from_output(out_dir, cli_path=cli_path, dry_run=dry)
    mode = "dry-run" if dry else "write"
    print(
        f"recover-sso-roster mode={mode} output={out_dir} roster={cli_path} "
        f"scanned={stats.get('scanned', 0)} appended={stats.get('appended', 0)} "
        f"would_append={stats.get('would_append', 0)} "
        f"candidates_unique={stats.get('candidates_unique', 0)} "
        f"skipped_existing={stats.get('skipped_existing', 0)} "
        f"skipped_dup_file={stats.get('skipped_dup_file', 0)} "
        f"skipped_error={stats.get('skipped_error', 0)} "
        f"skipped_no_sso={stats.get('skipped_no_sso', 0)}"
    )
    if not dry and int(stats.get("appended") or 0) > 0:
        print(
            f"hint: roster grew by {stats.get('appended')}; "
            f"mint gaps → python main.py --mint all --mint-missing --no-probe -j 2"
        )
    return 0


def _cmd_migrate_sso_roster(cfg: dict, args: argparse.Namespace) -> int:
    """Rewrite sso_roster.txt to email----password----sso (fill pw from evidence)."""
    from .ledger_ops import migrate_sso_roster_ensure_passwords

    root = Path(cfg.get("_root") or ".")
    roster = root / "sso_roster.txt"
    dry = bool(getattr(args, "recover_dry_run", False) or getattr(args, "dry_run", False))
    stats = migrate_sso_roster_ensure_passwords(roster, dry_run=dry, evidence_root=root)
    mode = "dry-run" if dry else "write"
    print(
        f"migrate-sso-roster mode={mode} path={stats.get('path')} "
        f"lines_in={stats.get('lines_in', 0)} rows_out={stats.get('rows_out', 0)} "
        f"filled_password={stats.get('filled_password', 0)} "
        f"two_part_in={stats.get('two_part_in', 0)} "
        f"still_empty_password={stats.get('still_empty_password', 0)} "
        f"rewritten={stats.get('rewritten')}"
    )
    return 0


def _cmd_migrate_account_evidence(cfg: dict, args: argparse.Namespace) -> int:
    """Move output/account_*.json → output/accounts/."""
    from .ledger_ops import migrate_account_evidence_to_subdir

    root = Path(cfg.get("_root") or ".")
    dry = bool(getattr(args, "recover_dry_run", False) or getattr(args, "dry_run", False))
    stats = migrate_account_evidence_to_subdir(root, dry_run=dry)
    mode = "dry-run" if dry else "write"
    print(
        f"migrate-account-evidence mode={mode} dest={stats.get('dest')} "
        f"scanned={stats.get('scanned', 0)} moved={stats.get('moved', 0)} "
        f"skipped_exists={stats.get('skipped_exists', 0)}"
    )
    return 0
