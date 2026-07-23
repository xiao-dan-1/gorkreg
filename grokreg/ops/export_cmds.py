"""Export / upload / auth-pool CLI commands.

Split from grokreg.cli. Export-layer: cpa_export & sub2api packs.
Upload ops: CPA Management API / sub2api admin import.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from grokreg import logutil
from grokreg.ops.ledger_ops import resolve_auth_path

log = logging.getLogger(__name__)

def _cmd_cpa_list(cfg: dict) -> int:
    """List remote CPA auth-files; always end with provider/status summary."""
    from collections import Counter

    from .cpa_upload import list_remote_auth_files, resolve_cpa_settings

    try:
        base, secret, _auth_dir = resolve_cpa_settings(cfg)
        body = list_remote_auth_files(base, secret)
    except Exception as e:
        logging.error("%s", e)
        return 1
    files = body.get("files") if isinstance(body, dict) else None
    if not isinstance(files, list):
        print(json.dumps(body, ensure_ascii=False)[:800])
        return 0

    print(f"base={base} count={len(files)}")
    for f in files[:50]:
        if not isinstance(f, dict):
            continue
        name = f.get("name") or f.get("id") or "?"
        provider = f.get("provider") or ""
        status = f.get("status") or ""
        print(f"  {name}  {provider}  {status}")
    if len(files) > 50:
        print(f"  ... +{len(files) - 50} more")

    # --- summary (stdout result table) ---
    by_prov: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    # provider -> status -> n
    prov_status: dict[str, Counter[str]] = {}
    xai_ok = xai_err = xai_dis = xai_other = 0

    for f in files:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or f.get("id") or "")
        prov = str(f.get("provider") or f.get("type") or "").strip().lower()
        if not prov:
            # filename heuristic: xai-*.json
            low = name.lower()
            if low.startswith("xai-") and low.endswith(".json"):
                prov = "xai"
            else:
                prov = "?"
        st = str(f.get("status") or "").strip().lower() or "?"
        # disabled flag can disagree with status on some builds
        if f.get("disabled") is True and st not in ("disabled", "disable"):
            st = "disabled"
        by_prov[prov] += 1
        by_status[st] += 1
        prov_status.setdefault(prov, Counter())[st] += 1

        if prov == "xai":
            if st == "active":
                xai_ok += 1
            elif st in ("error", "failed"):
                xai_err += 1
            elif st in ("disabled", "disable"):
                xai_dis += 1
            else:
                xai_other += 1

    xai_n = by_prov.get("xai", 0)
    print(
        f"summary total={len(files)} "
        f"xai={xai_n} active={xai_ok} error={xai_err} disabled={xai_dis}"
        + (f" other={xai_other}" if xai_other else "")
    )
    # other providers one-liners (codex etc.)
    for prov, n in by_prov.most_common():
        if prov == "xai":
            continue
        stc = prov_status.get(prov) or Counter()
        parts = " ".join(f"{s}={c}" for s, c in stc.most_common())
        print(f"  {prov}: {n}" + (f"  ({parts})" if parts else ""))
    # note: server active ≠ JWT/chat live
    print(
        "note: status=active 仅表示池内启用，不是本地 JWT/chat 探活；"
        "真·过期看 --auth-status / --probe-quota"
    )
    return 0


def _cmd_cpa_upload(cfg: dict, args: argparse.Namespace) -> int:
    from .cpa_upload import upload_all
    import time as _time

    only = args.cpa_upload
    dry = bool(getattr(args, "cpa_dry_run", False))
    missing = bool(getattr(args, "cpa_missing", False))
    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    limit_n = getattr(args, "limit", None)
    t0 = _time.time()
    try:
        result = upload_all(
            cfg,
            only=only,
            dry_run=dry,
            jobs=jobs,
            missing_only=missing,
            limit=limit_n,
            progress=True,
        )
    except Exception as e:
        logging.error("%s", e)
        return 1

    total = int(result.get("total") or 0)
    ok_n = int(result.get("ok") or 0)
    fail_n = int(result.get("failed") or 0)
    elapsed = _time.time() - t0

    # If upload_all already printed progress, still show summary banner/footer
    if not result.get("_progress_emitted"):
        print(
            f"{logutil.icon('start')} cpa-upload  run_id={logutil.get_run_id()}  "
            f"total={total} jobs={jobs} dry_run={int(dry)} "
            f"missing_only={int(missing)} base={result.get('base_url') or ''}",
            flush=True,
        )
        items = result.get("items") or []
        for i, it in enumerate(items, 1):
            kind = "upload_ok" if it.get("ok") else "fail"
            name = str(it.get("file") or "")
            logutil.print_progress(
                i,
                total or len(items) or 1,
                kind=kind,
                email=name,
                counters={"ok": ok_n, "fail": fail_n},
                force=kind == "fail",
            )

    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} cpa-upload done  "
        f"elapsed={elapsed:.0f}s  jobs={jobs}  run_id={logutil.get_run_id()}"
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}")
    print(f"  {'-' * 22} {'-' * 6}")
    print(f"  {'total':<22} {total:>6}")
    print(f"  {'ok':<22} {ok_n:>6}")
    print(f"  {'failed':<22} {fail_n:>6}")
    if result.get("limit"):
        print(f"  {'limit':<22} {result.get('limit'):>6}")
    if result.get("skipped_present") is not None:
        print(f"  {'skipped_present':<22} {result.get('skipped_present'):>6}")
    print("=" * 56)
    logutil.info(
        "cpa-upload",
        phase="done",
        base=result.get("base_url"),
        total=total,
        ok=ok_n,
        failed=fail_n,
        jobs=jobs,
        dry_run=1 if dry else 0,
        missing_only=1 if missing else 0,
        icon="done",
    )
    logutil.done_footer(
        "cpa-upload",
        total=total,
        ok=ok_n,
        fail=fail_n,
        wall_s=round(elapsed, 1),
        j=jobs,
        limit=result.get("limit") or 0,
        dry_run=int(dry),
    )
    return 0 if not fail_n else 1


def _cmd_sub2api_upload(cfg: dict, args: argparse.Namespace) -> int:
    """Remote sub2api admin importData (ops; not export Protocol)."""
    from .sub2api_upload import (
    build_envelope_from_auth,
    filter_envelope_by_email,
    merge_packs,
    list_local_packs,
    resolve_sub2api_settings,
    upload_envelope,
    upload_from_dir,
    )

    settings = resolve_sub2api_settings(cfg)
    only = (args.sub2api_upload or "all").strip()
    dry = bool(getattr(args, "sub2api_upload_dry_run", False) or getattr(args, "export_dry_run", False))
    from_auth = bool(getattr(args, "sub2api_from_auth", False))
    on_exists = (getattr(args, "sub2api_on_exists", None) or "create").strip().lower()
    if on_exists in {"update", "upsert"}:
        on_exists = "overwrite"
    skip_bind = not bool(getattr(args, "sub2api_no_skip_default_group", False))
    if "skip_default_group_bind" in settings and not getattr(args, "sub2api_no_skip_default_group", False):
        skip_bind = bool(settings.get("skip_default_group_bind", True))

    base = settings.get("base_url") or ""
    email = settings.get("admin_email") or ""
    password = settings.get("admin_password") or ""
    export_dir = (
        getattr(args, "sub2api_out_dir", None)
        or settings.get("export_dir")
        or "sub2api_export"
    )
    timeout = float(settings.get("timeout") or 30)

    if not dry and (not base or not email or not password):
        logging.error(
            "sub2api-upload 需要 SUB2API_BASE_URL + SUB2API_ADMIN_EMAIL + "
            "SUB2API_ADMIN_PASSWORD（或 config.yaml sub2api.*）"
        )
        return 2

    try:
        if from_auth:
            auth_path = cfg.get("auth_file") or cfg.get("auth_path") or "auth.json"
            no_map = bool(getattr(args, "sub2api_no_model_mapping", False))
            env = build_envelope_from_auth(
                auth_path,
                only=only,
                include_model_mapping=not no_map,
            limit=getattr(args, "limit", None),
            )
            limit_n = getattr(args, "limit", None)
            if limit_n is not None and int(limit_n) > 0:
                accs = list(env.get("accounts") or [])
                before = len(accs)
                env = dict(env)
                env["accounts"] = accs[: int(limit_n)]
                print(f"sub2api-upload limit={limit_n} (of {before} from auth)")
            result = upload_envelope(
                env,
                base_url=base or "https://example.invalid",
                admin_email=email or "dry@local",
                admin_password=password or "dry",
                skip_default_group_bind=skip_bind,
                timeout=timeout,
                dry_run=dry,
                on_exists=on_exists,
            )
            result["source"] = "auth.json"
        else:
            result = upload_from_dir(
                export_dir,
                base_url=base or "https://example.invalid",
                admin_email=email or "dry@local",
                admin_password=password or "dry",
                only=only,
                skip_default_group_bind=skip_bind,
                timeout=timeout,
                dry_run=dry,
                merge=True,
                on_exists=on_exists,
                limit=getattr(args, "limit", None),
            )
            result["source"] = str(export_dir)
    except Exception as e:
        logging.error("sub2api-upload: %s", e)
        return 1

    summary = result.get("summary") or result.get("plan_summary") or ""
    print(
        f"sub2api-upload source={result.get('source')} "
        f"on_exists={result.get('on_exists', on_exists)} "
        f"accounts={result.get('accounts', result.get('files', '-'))} "
        f"ok={1 if result.get('ok') else 0} dry_run={1 if dry else 0} "
        f"base={base or '-'} {summary}"
    )
    if result.get("error"):
        logging.error("%s", result.get("error"))
    if result.get("message"):
        logging.info("%s", result.get("message"))
    return 0 if result.get("ok") else 1


def _cmd_auth_import(cfg: dict, args: argparse.Namespace) -> int:
    from ..auth_pool import import_cpa_dir, summarize
    from ..backends.export import sync_cpa_to_pool

    auth_path = resolve_auth_path(cfg, args)
    src = Path(args.auth_import)
    if not src.exists():
        logging.error("源不存在: %s", src)
        return 2
    if src.is_file():
        try:
            key = sync_cpa_to_pool(src, auth_path=auth_path)
            logging.info("imported 1 file -> %s key=%s", auth_path, key[:48])
        except Exception as e:
            logging.error("import fail: %s", e)
            return 1
    else:
        stats = import_cpa_dir(auth_path, src)
        logging.info("import %s -> %s  ok=%s skip=%s fail=%s",
                     src, auth_path, stats["ok"], stats["skip"], stats["fail"])
        if stats["fail"]:
            return 1
    s = summarize(auth_path)
    print(f"auth pool: total={s['total']} fresh={s['fresh']} needs_refresh={s['needs_refresh']} "
          f"expired={s['expired']} with_rt={s['with_rt']}  file={auth_path}")
    return 0


def _cmd_auth_list(cfg: dict, args: argparse.Namespace) -> int:
    from ..auth_pool import list_entries, status_row, summarize

    auth_path = resolve_auth_path(cfg, args)
    if not auth_path.is_file():
        logging.error("auth pool 不存在: %s  （先: --auth-import cpa_export）", auth_path)
        return 2
    rows = [status_row(k, e) for k, e in list_entries(auth_path, include_disabled=True, include_expired=True)]
    print(f"{'#':>3} {'email':<42} {'exp':>7} {'fresh':>5} {'rt':>3} {'need_ref':>8}")
    print("-" * 75)
    for i, r in enumerate(rows, 1):
        left = r.get("left_h")
        exp_s = f"{left:.1f}h" if left is not None else " -"
        print(f"{i:>3} {(r.get('email') or '?')[:41]:<42} {exp_s:>7} "
              f"{'yes' if r.get('fresh') else ' -':>5} "
              f"{'yes' if r.get('has_rt') else ' -':>3} "
              f"{'yes' if r.get('needs_refresh') else ' -':>8}")
    s = summarize(auth_path)
    print(f"\ntotal={s['total']} fresh={s['fresh']} needs_refresh={s['needs_refresh']} "
          f"expired={s['expired']} with_rt={s['with_rt']}")
    print(f"file={auth_path}")
    return 0


def _cmd_auth_pick(cfg: dict, args: argparse.Namespace) -> int:
    from ..auth_pool import pick, status_row, AuthPoolError

    auth_path = resolve_auth_path(cfg, args)
    if not auth_path.is_file():
        logging.error("auth pool 不存在: %s", auth_path)
        return 2
    target = (args.auth_pick or "auto").strip()
    email = None if target.lower() in {"auto", "any", ""} else target
    try:
        key, entry = pick(auth_path, email=email, prefer_fresh=True)
    except AuthPoolError as e:
        logging.error("%s", e)
        return 1
    r = status_row(key, entry)
    left = r.get("left_h")
    print(f"picked email={r.get('email')} exp={left:.1f}h fresh={r.get('fresh')} "
          f"rt={r.get('has_rt')} sub={r.get('sub')}")
    print(f"key={key}")
    return 0


def _sync_auth_from_cpa_path(cfg: dict, args: argparse.Namespace, cpa_path: str | Path | None) -> None:
    """Best-effort ledger upsert after refresh (mint already publishes)."""
    if not cpa_path:
        return
    p = Path(cpa_path)
    if not p.is_file():
        return
    try:
        from ..backends.export import sync_cpa_to_pool

        auth_path = resolve_auth_path(cfg, args)
        key = sync_cpa_to_pool(p, auth_path=auth_path)
        logging.debug("auth pool upsert %s -> %s", p.name, key[:40])
    except Exception as e:
        logging.warning("auth pool upsert fail: %s", e)


def _cmd_export(cfg: dict, args: argparse.Namespace) -> int:
    """Pool pack export: auth.json → backend files (sub2api, future …). No mint."""
    from ..backends.export import export_auth_pool

    auth_path = resolve_auth_path(cfg, args)
    if not Path(auth_path).is_file():
        logging.error("auth pool 不存在: %s  （先 mint 或 --auth-import cpa_export）", auth_path)
        return 2

    # Resolve backend + only filter (support legacy --sub2api-export)
    backend = (getattr(args, "export", None) or "").strip()
    only = (getattr(args, "export_only", None) or "all").strip()
    if getattr(args, "sub2api_export", None) is not None:
        # alias path
        if not backend or backend.lower() in {"sub2api", "s2a", "sub2"}:
            backend = "sub2api"
        only = (args.sub2api_export or only or "all").strip()
    if not backend:
        backend = "sub2api"

    out_dir = (
        getattr(args, "export_out_dir", None)
        or getattr(args, "sub2api_out_dir", None)
        or (
            ((cfg.get("sub2api") or {}).get("export_dir"))
            if isinstance(cfg.get("sub2api"), dict) and backend.lower() in {"sub2api", "s2a", "sub2"}
            else None
        )
    )
    dry = bool(getattr(args, "export_dry_run", False) or getattr(args, "sub2api_dry_run", False))
    no_map = bool(getattr(args, "sub2api_no_model_mapping", False))

    try:
        stats = export_auth_pool(
            backend,
            auth_path,
            out_dir=out_dir,
            only=only,
            dry_run=dry,
            include_model_mapping=not no_map,
            cfg=cfg,
        )
    except Exception as e:
        logging.error("export failed: %s", e)
        return 2

    print(
        f"export backend={stats.get('backend') or backend} dry_run={1 if dry else 0} "
        f"total={stats['total']} ok={stats['ok']} skip={stats['skip']} fail={stats['fail']} "
        f"out={stats['out_dir']} auth={stats['auth_path']}"
    )
    if stats.get("errors"):
        for err in stats["errors"][:20]:
            print(f"  skip/fail email={err.get('email')} err={err.get('error')}")
        if len(stats["errors"]) > 20:
            print(f"  ... +{len(stats['errors']) - 20} more")
    if dry and stats.get("files"):
        for f in stats["files"][:10]:
            print(f"  would_write {f}")
        if len(stats["files"]) > 10:
            print(f"  ... +{len(stats['files']) - 10} more")
    if stats["fail"]:
        return 1
    if stats["ok"] == 0 and stats["total"] == 0:
        logging.warning("no entries matched (auth empty or filter too tight)")
        return 2
    return 0


def _cmd_sub2api_export(cfg: dict, args: argparse.Namespace) -> int:
    """Backward-compat wrapper → _cmd_export."""
    return _cmd_export(cfg, args)
