"""Shared production pipeline skeleton: register → publish → upload → summary.

Two product lines share this module and only differ by ``Target``:

  - Target.CPA     → packs cpa_files  → cpa_upload
  - Target.SUB2API → packs sub2api    → sub2api_upload

Design (v0 skeleton):
  * Static ``# [n/N]`` progress only (no SlotBoard / spinner).
  * Default **verbose=True** (process INFO); ``quiet=True`` suppresses noise.
  * ``dry_run=True`` skips network register/upload; still prints phase shape.
  * Real register batch is **not** fully wired yet — use
    ``register_fn`` hook or later fill ``_phase_register``.
  * Upload reuses existing ``ops.cpa_upload.upload_all`` /
    ``ops.sub2api_upload.upload_from_dir`` when not dry-run.

CLI entrypoints (thin wrappers)::

  python scripts/pipeline_cpa.py -n 5 --dry-run
  python scripts/pipeline_sub2api.py -n 5 --dry-run


Status: skeleton only — 暂不开发 / 不作为日常入口。日常用 prod_cloudmail_batch + main 分步。
"""
from __future__ import annotations

# Marker for architecture / hygiene tests: do not treat as daily entry.
SKELETON_ONLY = True  # 暂不开发 — prefer prod_cloudmail_batch + main 分步

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from grokreg import logutil
from grokreg.config import load_config

log = logging.getLogger("grokreg.ops.pipeline")


class Target(str, Enum):
    """Delivery channel — never mix CPA and Sub2API in one run."""

    CPA = "cpa"
    SUB2API = "sub2api"

    @property
    def packs(self) -> list[str]:
        if self is Target.CPA:
            return ["cpa_files"]
        return ["sub2api"]

    @property
    def label(self) -> str:
        return "cpa-pipeline" if self is Target.CPA else "sub2api-pipeline"


@dataclass
class PipelineConfig:
    """Knobs shared by both product lines."""

    target: Target
    n: int = 1
    jobs: int = 1
    # mail / captcha (for future real register wire-up)
    mail_backend: str = "cloudmail"
    captcha_backend: str = "auto"
    region: str = "US"
    # flags
    dry_run: bool = False
    quiet: bool = False  # default verbose when quiet=False
    ascii_log: bool = False
    skip_register: bool = False  # upload-only from existing packs
    skip_upload: bool = False  # register+publish only
    skip_setup: bool = False
    # upload
    upload_limit: int | None = None
    cpa_missing_only: bool = True  # CPA default: only missing on remote
    sub2api_on_exists: str = "overwrite"
    # optional batch file (Outlook/list) — reserved
    batch_file: str | None = None
    # hooks (tests / gradual fill-in)
    register_fn: Callable[["PipelineConfig", dict[str, Any]], list[dict[str, Any]]] | None = (
        None
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.n = max(1, int(self.n or 1))
        self.jobs = max(1, int(self.jobs or 1))
        if not isinstance(self.target, Target):
            raw = str(self.target).strip().lower()
            if raw.startswith("target."):
                raw = raw.split(".", 1)[1]
            self.target = Target(raw)
        if self.upload_limit is not None:
            self.upload_limit = max(0, int(self.upload_limit))


@dataclass
class PipelineResult:
    target: str
    ok: bool
    phases: dict[str, Any] = field(default_factory=dict)
    register_rows: list[dict[str, Any]] = field(default_factory=list)
    publish: dict[str, Any] = field(default_factory=dict)
    upload: dict[str, Any] = field(default_factory=dict)
    wall_s: float = 0.0
    error: str | None = None

    @property
    def exit_code(self) -> int:
        if not self.ok or self.error:
            return 1
        reg = self.phases.get("register") or {}
        up = self.phases.get("upload") or {}
        if int(reg.get("fail") or 0) > 0:
            return 1
        if int(up.get("failed") or up.get("fail") or 0) > 0:
            return 1
        return 0


# ---------------------------------------------------------------------------
# logging helpers
# ---------------------------------------------------------------------------


def configure_pipeline_logging(*, quiet: bool, ascii_log: bool) -> str:
    """Default verbose; quiet → WARNING for process loggers."""
    verbose = not bool(quiet)
    logutil.configure_logging(verbose, ascii_log=bool(ascii_log))
    rid = logutil.new_run_id()
    if quiet:
        logging.getLogger().setLevel(logging.WARNING)
        for name in ("grokreg", "urllib3", "curl_cffi", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
    return rid


def _print_start(cfg: PipelineConfig) -> None:
    print(
        f"{logutil.icon('start')} {cfg.target.label}  "
        f"run_id={logutil.get_run_id()}  "
        f"n={cfg.n} jobs={cfg.jobs}  "
        f"mail={cfg.mail_backend} captcha={cfg.captcha_backend}  "
        f"dry_run={1 if cfg.dry_run else 0}  "
        f"skip_register={1 if cfg.skip_register else 0}  "
        f"skip_upload={1 if cfg.skip_upload else 0}",
        flush=True,
    )
    logutil.info(
        cfg.target.label,
        phase="start",
        n=cfg.n,
        j=cfg.jobs,
        target=cfg.target.value,
        dry_run=1 if cfg.dry_run else 0,
        icon="start",
    )


def _print_phase(name: str) -> None:
    print(f"# —— {name} ——", flush=True)


def _progress(
    done: int,
    total: int,
    *,
    kind: str,
    email: str = "",
    counters: Mapping[str, Any] | None = None,
    t0: float,
    force: bool = False,
) -> None:
    elapsed = max(0.001, time.time() - t0)
    rate = done / elapsed if done else 0.0
    eta = max(0.0, (total - done) / rate) if rate > 0 else 0.0
    logutil.print_progress(
        done,
        total,
        kind=kind,
        email=email,
        counters=dict(counters or {}),
        rate=rate,
        eta_s=eta,
        elapsed_s=elapsed,
        force=force,
    )


def _print_summary(cfg: PipelineConfig, result: PipelineResult) -> None:
    print()
    print("=" * 56)
    print(
        f"{logutil.icon('done')} {cfg.target.label} done  "
        f"elapsed={result.wall_s:.0f}s  run_id={logutil.get_run_id()}  "
        f"ok={1 if result.ok else 0}",
        flush=True,
    )
    print("=" * 56)
    print(f"  {'bucket':<22} {'n':>6}  note")
    print(f"  {'-' * 22} {'-' * 6}  {'-' * 28}")
    for phase_name, blob in (result.phases or {}).items():
        if not isinstance(blob, dict):
            continue
        for k, v in blob.items():
            if k in {"items", "rows", "detail"}:
                continue
            if isinstance(v, (int, float, str, bool)) or v is None:
                print(f"  {f'{phase_name}.{k}':<22} {str(v)[:12]:>6}")
    if result.error:
        print(f"  {'error':<22} {'':>6}  {result.error[:40]}")
    print("=" * 56)
    logutil.done_footer(
        cfg.target.label,
        target=cfg.target.value,
        wall_s=round(result.wall_s, 1),
        ok=1 if result.ok else 0,
        dry_run=1 if cfg.dry_run else 0,
    )


# ---------------------------------------------------------------------------
# phases (skeleton)
# ---------------------------------------------------------------------------


def phase_setup(cfg: PipelineConfig, app_cfg: dict[str, Any]) -> dict[str, Any]:
    """Proxy / captcha balance hooks — skeleton: log only unless dry_run skips."""
    if cfg.skip_setup:
        print(f"{logutil.icon('skip')} setup  skipped", flush=True)
        return {"skipped": 1}
    if cfg.dry_run:
        print(
            f"{logutil.icon('ok')} setup  target={cfg.target.value}  (dry-run)",
            flush=True,
        )
        return {"ok": 1, "dry_run": 1}

    try:
        from grokreg.ops.env_cmds import run_proxy_preflight

        code = run_proxy_preflight(
            app_cfg,
            (cfg.extra or {}).get("proxy"),
            require_ok=True,
            full_probe=True,
        )
        if code != 0:
            print(
                f"{logutil.icon('fail')} setup  proxy preflight refused exit={code}",
                flush=True,
            )
            return {"ok": 0, "proxy": code, "error": f"proxy_preflight:{code}"}
        print(f"{logutil.icon('ok')} setup  proxy OK", flush=True)
    except Exception as exc:
        print(f"{logutil.icon('warn')} setup  proxy preflight skipped: {exc}", flush=True)
        log.warning("proxy preflight error: %s", exc)

    logutil.info(cfg.target.label, phase="setup", target=cfg.target.value, icon="ok")
    return {"ok": 1}


def _default_dry_register(cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Fake register rows for skeleton / --dry-run shape."""
    rows: list[dict[str, Any]] = []
    for i in range(1, cfg.n + 1):
        email = f"dry{i:03d}@example.invalid"
        # every 2nd fails when n>=2 and extra.fail_every set
        fail_every = int((cfg.extra or {}).get("fail_every") or 0)
        fail = fail_every > 0 and (i % fail_every == 0)
        rows.append(
            {
                "idx": i,
                "email": email,
                "ok": not fail,
                "error": "dry_run:simulated_fail" if fail else None,
                "kind": "register_ok" if not fail else "dry_fail",
            }
        )
        sleep_s = float((cfg.extra or {}).get("dry_sleep") or 0)
        if sleep_s > 0:
            time.sleep(sleep_s)
    return rows



def _kind_from_error(err: str | None) -> str:
    if not err:
        return "register_ok"
    s = str(err).lower()
    if s.startswith("alloc:"):
        return "alloc_fail"
    if "captcha" in s or "turnstile" in s:
        return "captcha"
    if "sso" in s:
        return "sso_failed"
    if "mail" in s or "code" in s or "otp" in s:
        return "mail"
    if "proxy" in s:
        return "proxy"
    return "register_fail"


def _register_cloudmail_one(
    *,
    idx: int,
    n: int,
    app_cfg: dict[str, Any],
    cfg: PipelineConfig,
    proxy_override: str | None,
    use_cache: bool,
    ttl: float,
) -> dict[str, Any]:
    """One CloudMail allocate + register_one + save. No tokens in returned row."""
    from grokreg.backends.mail.cloudmail import allocate_cloudmail_address
    from grokreg.pipeline.register import RegisterOptions, register_one, result_ok

    try:
        email = allocate_cloudmail_address(app_cfg)
    except Exception as exc:
        return {
            "idx": idx,
            "email": "",
            "ok": False,
            "error": f"alloc:{exc}",
            "kind": "alloc_fail",
            "sso": None,
        }

    opts = RegisterOptions(
        mail_backend="cloudmail",
        captcha_backend=str(cfg.captcha_backend or "auto"),
        verbose=not bool(cfg.quiet),
        require_captcha_config=True,
        scrape_cache=use_cache,
        scrape_cache_ttl=ttl,
        create_short_body_retries=3,
        concurrent_jitter=(cfg.jobs > 1),
        index=idx,
    )
    t1 = time.time()
    try:
        result = register_one(app_cfg, email, proxy_override, opts)
    except Exception as exc:
        result = {
            "email": email,
            "error": f"exception:{exc}",
            "sso": None,
            "timings_sec": {},
        }
    wall = time.time() - t1
    ok = bool(result_ok(result))
    try:
        from grokreg.ops.ledger_ops import save_result
        from grokreg.ops.ledger_ops import record_register_success

        save_result(app_cfg, None, result)
        if ok:
            append_sso_roster(result)
    except Exception as exc:
        log.warning("save/cli append failed i=%s: %s", idx, exc)

    err = result.get("error")
    row = {
        "idx": idx,
        "email": (result.get("email") or email or "")[:80],
        "ok": ok,
        "error": err,
        "kind": "register_ok" if ok else _kind_from_error(err),
        "wall_s": round(wall, 3),
        "scrape_cache": result.get("scrape_cache"),
        "has_sso": bool(result.get("sso")),
    }
    # In-process only for publish/mint — never print
    if ok and result.get("sso"):
        row["_sso"] = result.get("sso")
        row["_password"] = result.get("password") or ""
    return row


def _register_cloudmail_batch(
    cfg: PipelineConfig,
    app_cfg: dict[str, Any],
    *,
    t0: float,
) -> list[dict[str, Any]]:
    """Concurrent CloudMail register with static # progress."""
    from grokreg import scrape_cache

    use_cache = not bool((cfg.extra or {}).get("no_scrape_cache"))
    ttl = float((cfg.extra or {}).get("scrape_cache_ttl") or 600.0)
    proxy_override = (cfg.extra or {}).get("proxy")
    do_prewarm = use_cache and not bool((cfg.extra or {}).get("no_scrape_prewarm"))

    if do_prewarm:
        try:
            from grokreg.client import GrokAuthClient
            from grokreg.proxyutil import resolve_proxy

            resolved = resolve_proxy(app_cfg, proxy_override)
            client = GrokAuthClient(app_cfg, session_url=resolved.session_url, debug=False)
            info = client.load_signup_page(force=True, use_cache=True, cache_ttl=ttl)
            action = (info or {}).get("next_action") or getattr(client, "_next_action_id", None) or ""
            tree = getattr(client, "_next_router_state_tree", None) or ""
            sitekey = (info or {}).get("turnstile_sitekey") or client.turnstile_sitekey or ""
            if action and tree:
                scrape_cache.put(
                    next_action=str(action),
                    router_state_tree=str(tree),
                    turnstile_sitekey=str(sitekey),
                    ttl=ttl,
                )
            print(
                f"{logutil.icon('ok')} setup  prewarm  action={str(action)[:16]}…",
                flush=True,
            )
        except Exception as exc:
            print(
                f"{logutil.icon('warn')} setup  prewarm failed (non-fatal): {exc}",
                flush=True,
            )

    rows: list[dict[str, Any]] = []
    ok = fail = done = 0

    def _job(idx: int) -> dict[str, Any]:
        return _register_cloudmail_one(
            idx=idx,
            n=cfg.n,
            app_cfg=app_cfg,
            cfg=cfg,
            proxy_override=proxy_override,
            use_cache=use_cache,
            ttl=ttl,
        )

    if cfg.jobs == 1:
        for i in range(1, cfg.n + 1):
            row = _job(i)
            rows.append(row)
            done += 1
            if row.get("ok"):
                ok += 1
            else:
                fail += 1
            _progress(
                done,
                cfg.n,
                kind=str(row.get("kind") or "register_fail"),
                email=str(row.get("email") or ""),
                counters={"ok": ok, "fail": fail},
                t0=t0,
                force=not bool(row.get("ok")),
            )
    else:
        tmp: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=cfg.jobs) as ex:
            futs = {ex.submit(_job, i): i for i in range(1, cfg.n + 1)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    row = {
                        "idx": i,
                        "email": "",
                        "ok": False,
                        "error": f"worker:{exc}",
                        "kind": "register_fail",
                    }
                tmp[i] = row
                done += 1
                if row.get("ok"):
                    ok += 1
                else:
                    fail += 1
                _progress(
                    done,
                    cfg.n,
                    kind=str(row.get("kind") or "register_fail"),
                    email=str(row.get("email") or ""),
                    counters={"ok": ok, "fail": fail},
                    t0=t0,
                    force=not bool(row.get("ok")),
                )
        rows = [tmp[i] for i in range(1, cfg.n + 1)]
    return rows


def phase_register(
    cfg: PipelineConfig,
    app_cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Register phase — hook or dry skeleton.

    Real implementation later:
      - CloudMail: reuse scripts/prod_cloudmail_batch helpers
      - batch file: reuse pipeline.register / cli batch
    """
    if cfg.skip_register:
        print(f"{logutil.icon('skip')} register  skipped", flush=True)
        return [], {"skipped": 1, "ok": 0, "fail": 0, "total": 0}

    _print_phase("register")
    t0 = time.time()

    if cfg.register_fn is not None:
        rows = list(cfg.register_fn(cfg, app_cfg) or [])
    elif cfg.dry_run or cfg.extra.get("force_skeleton_register"):
        # concurrent dry to exercise progress under -j
        rows = []
        if cfg.jobs == 1:
            rows = _default_dry_register(cfg)
            ok = fail = 0
            for i, row in enumerate(rows, 1):
                if row.get("ok"):
                    ok += 1
                else:
                    fail += 1
                _progress(
                    i,
                    cfg.n,
                    kind=str(row.get("kind") or ("register_ok" if row.get("ok") else "register_fail")),
                    email=str(row.get("email") or ""),
                    counters={"ok": ok, "fail": fail},
                    t0=t0,
                    force=not bool(row.get("ok")),
                )
        else:
            # parallel dry
            def _one(idx: int) -> dict[str, Any]:
                sleep_s = float((cfg.extra or {}).get("dry_sleep") or 0)
                if sleep_s:
                    time.sleep(sleep_s)
                fail_every = int((cfg.extra or {}).get("fail_every") or 0)
                fail = fail_every > 0 and (idx % fail_every == 0)
                email = f"dry{idx:03d}@example.invalid"
                return {
                    "idx": idx,
                    "email": email,
                    "ok": not fail,
                    "error": "dry_run:simulated_fail" if fail else None,
                    "kind": "register_ok" if not fail else "dry_fail",
                }

            ok = fail = done = 0
            tmp: dict[int, dict] = {}
            with ThreadPoolExecutor(max_workers=cfg.jobs) as ex:
                futs = {ex.submit(_one, i): i for i in range(1, cfg.n + 1)}
                for fut in as_completed(futs):
                    i = futs[fut]
                    row = fut.result()
                    tmp[i] = row
                    done += 1
                    if row.get("ok"):
                        ok += 1
                    else:
                        fail += 1
                    _progress(
                        done,
                        cfg.n,
                        kind=str(row.get("kind") or "register_fail"),
                        email=str(row.get("email") or ""),
                        counters={"ok": ok, "fail": fail},
                        t0=t0,
                        force=not bool(row.get("ok")),
                    )
            rows = [tmp[i] for i in range(1, cfg.n + 1)]
    else:
        # Real path: CloudMail by default
        mb = (cfg.mail_backend or "cloudmail").strip().lower()
        if mb in {"cloudmail", "cloud-mail", "cloud_mail", "cm", "auto", ""}:
            try:
                rows = _register_cloudmail_batch(cfg, app_cfg, t0=t0)
            except Exception as exc:
                log.exception("cloudmail register batch failed: %s", exc)
                return [], {
                    "ok": 0,
                    "fail": 0,
                    "total": 0,
                    "error": f"register_batch:{exc}",
                }
        else:
            msg = (
                f"mail_backend={mb!r} real batch not wired yet "
                f"(use cloudmail, dry_run, or register_fn)"
            )
            log.error(msg)
            return [], {"ok": 0, "fail": 0, "total": 0, "error": msg}

    ok_n = sum(1 for r in rows if r.get("ok"))
    fail_n = len(rows) - ok_n
    stats = {
        "total": len(rows),
        "ok": ok_n,
        "fail": fail_n,
        "wall_s": round(time.time() - t0, 3),
        "dry_run": bool(cfg.dry_run),
    }
    logutil.info(
        cfg.target.label,
        phase="register_done",
        ok=ok_n,
        fail=fail_n,
        icon="ok" if fail_n == 0 else "warn",
    )
    return rows, stats


def _sso_map_from_cli() -> dict[str, str]:
    """email(lower) -> sso from sso_roster.txt."""
    p = Path("sso_roster.txt")
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = [x.strip() for x in line.strip().split("----")]
            if len(parts) >= 3 and "@" in parts[0] and parts[-1]:
                out[parts[0].lower()] = parts[-1]
    except OSError:
        pass
    return out


def _mint_proxy(app_cfg: dict[str, Any], cfg: PipelineConfig) -> str:
    from .mint_proxy import resolve_mint_proxy

    # pipeline extra.mint_proxy via fake args; else env/default 10808
    class _A:
        mint_proxy = str((cfg.extra or {}).get("mint_proxy") or "").strip() or None

    return resolve_mint_proxy(_A())


def phase_publish(
    cfg: PipelineConfig,
    app_cfg: dict[str, Any],
    register_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mint SSO → auth.json and write target pack (cpa_files | sub2api)."""
    if cfg.skip_upload and cfg.skip_register and not register_rows:
        # upload-only path may still want publish skip
        pass

    _print_phase("publish")
    packs = list(cfg.target.packs)
    t0 = time.time()

    # Candidates: successful register rows (prefer in-memory sso)
    candidates: list[dict[str, Any]] = [
        r
        for r in (register_rows or [])
        if r.get("ok") and str(r.get("email") or "").strip()
    ]

    # skip_register: mint missing from sso_roster (upload-only prep)
    if cfg.skip_register and not candidates:
        cli_map = _sso_map_from_cli()
        for em, sso in list(cli_map.items())[: max(cfg.n, 1) * 50]:
            candidates.append({"email": em, "ok": True, "_sso": sso, "has_sso": True})
        if cfg.upload_limit:
            candidates = candidates[: int(cfg.upload_limit)]
        elif cfg.n and not register_rows:
            # when only -n without register, take last n from cli
            candidates = candidates[-cfg.n :] if cfg.n < len(candidates) else candidates

    if cfg.dry_run:
        n = len(candidates)
        print(
            f"{logutil.icon('ok')} publish  packs={packs}  emails={n}  (dry-run mint+export)",
            flush=True,
        )
        for i, r in enumerate(candidates, 1):
            _progress(
                i,
                max(1, n),
                kind="publish_ok",
                email=str(r.get("email") or ""),
                counters={"ok": i, "fail": 0},
                t0=t0,
            )
        return {
            "packs": packs,
            "emails": n,
            "ok": n,
            "fail": 0,
            "dry_run": 1,
            "wall_s": round(time.time() - t0, 3),
        }

    if not candidates:
        print(f"{logutil.icon('skip')} publish  no successful accounts", flush=True)
        return {"packs": packs, "emails": 0, "ok": 0, "fail": 0, "wall_s": 0.0}

    from grokreg.oauth import mint

    cli_map = _sso_map_from_cli()
    proxy = _mint_proxy(app_cfg, cfg)
    auth_path = Path(
        (cfg.extra or {}).get("auth_path")
        or (app_cfg.get("auth") or {}).get("path")
        or "auth.json"
    )
    ok = fail = 0
    total = len(candidates)
    errors: list[str] = []

    for i, row in enumerate(candidates, 1):
        email = str(row.get("email") or "").strip()
        sso = row.get("_sso") or cli_map.get(email.lower()) or ""
        if not sso:
            fail += 1
            errors.append(f"{email}:no_sso")
            _progress(
                i,
                total,
                kind="publish_fail",
                email=email,
                counters={"ok": ok, "fail": fail},
                t0=t0,
                force=True,
            )
            continue
        try:
            r = mint(
                email,
                sso,
                proxy=proxy,
                auth_path=auth_path,
                packs=packs,
                probe_mode="none",
            )
            if r.get("ok"):
                ok += 1
                kind = "publish_ok"
            else:
                fail += 1
                kind = "publish_fail"
                errors.append(f"{email}:{r.get('error') or 'mint_fail'}")
        except Exception as exc:
            fail += 1
            kind = "publish_fail"
            errors.append(f"{email}:{exc}")
            log.warning("publish mint failed %s: %s", email, exc)
        _progress(
            i,
            total,
            kind=kind,
            email=email,
            counters={"ok": ok, "fail": fail},
            t0=t0,
            force=kind != "publish_ok",
        )

    print(
        f"{logutil.icon('ok' if fail == 0 else 'warn')} publish  "
        f"packs={packs}  ok={ok} fail={fail}  proxy={proxy}",
        flush=True,
    )
    return {
        "packs": packs,
        "emails": total,
        "ok": ok,
        "fail": fail,
        "wall_s": round(time.time() - t0, 3),
        "errors": errors[:20],
    }



def phase_upload(
    cfg: PipelineConfig,
    app_cfg: dict[str, Any],
    register_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Upload phase — CPA or Sub2API via existing modules."""
    if cfg.skip_upload:
        print(f"{logutil.icon('skip')} upload  skipped", flush=True)
        return {"skipped": 1, "ok": 0, "failed": 0}

    _print_phase("upload")
    t0 = time.time()
    limit = cfg.upload_limit

    if cfg.dry_run:
        # simulate one line per successful register email
        emails = [
            str(r.get("email") or "")
            for r in register_rows
            if r.get("ok") and r.get("email")
        ]
        if limit:
            emails = emails[: int(limit)]
        total = len(emails) or (0 if cfg.skip_register else min(cfg.n, limit or cfg.n))
        if not emails and not cfg.skip_register:
            # still show shape with dry emails from register rows even if all failed
            total = 0
        ok = fail = 0
        for i, email in enumerate(emails, 1):
            ok += 1
            _progress(
                i,
                max(1, total),
                kind="upload_ok",
                email=email,
                counters={"ok": ok, "fail": fail},
                t0=t0,
            )
        print(
            f"{logutil.icon('ok')} upload  dry-run  target={cfg.target.value}  "
            f"would_upload={ok}  limit={limit or '-'}",
            flush=True,
        )
        return {
            "ok": ok,
            "failed": fail,
            "total": total,
            "dry_run": 1,
            "target": cfg.target.value,
            "wall_s": round(time.time() - t0, 3),
        }

    # ---- real upload ----
    if cfg.target is Target.CPA:
        from grokreg.ops.cpa_upload import upload_all

        result = upload_all(
            app_cfg,
            dry_run=False,
            jobs=cfg.jobs,
            missing_only=bool(cfg.cpa_missing_only),
            limit=limit,
            progress=True,
        )
        return {
            "ok": int(result.get("ok") or 0),
            "failed": int(result.get("failed") or 0),
            "total": int(result.get("total") or 0),
            "target": "cpa",
            "missing_only": bool(cfg.cpa_missing_only),
            "wall_s": round(time.time() - t0, 3),
            "raw": {k: v for k, v in result.items() if k != "items"},
        }

    # Sub2API
    from grokreg.ops.sub2api_upload import resolve_sub2api_settings, upload_from_dir

    s2 = resolve_sub2api_settings(app_cfg)
    export_dir = Path(
        (cfg.extra or {}).get("sub2api_export_dir")
        or s2.get("export_dir")
        or (app_cfg.get("sub2api") or {}).get("export_dir")
        or "sub2api_export"
    )
    result = upload_from_dir(
        export_dir,
        base_url=str(s2.get("base_url") or ""),
        admin_email=str(s2.get("admin_email") or s2.get("email") or ""),
        admin_password=str(s2.get("admin_password") or s2.get("password") or ""),
        on_exists=str(cfg.sub2api_on_exists or "overwrite"),
        dry_run=False,
        limit=limit,
    )
    # upload_from_dir shape may vary — normalize
    return {
        "ok": int(result.get("ok") or result.get("created") or 0),
        "failed": int(result.get("failed") or result.get("fail") or 0),
        "total": int(result.get("total") or result.get("accounts") or 0),
        "target": "sub2api",
        "on_exists": cfg.sub2api_on_exists,
        "wall_s": round(time.time() - t0, 3),
        "raw": {k: v for k, v in (result or {}).items() if k not in {"items", "accounts"}},
    }


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


def run_pipeline(cfg: PipelineConfig, app_cfg: dict[str, Any] | None = None) -> PipelineResult:
    """Run setup → register → publish → upload → summary."""
    t_wall = time.time()
    configure_pipeline_logging(quiet=cfg.quiet, ascii_log=cfg.ascii_log)
    if app_cfg is None:
        app_cfg = load_config()

    result = PipelineResult(target=cfg.target.value, ok=True)
    try:
        _print_start(cfg)

        setup_stats = phase_setup(cfg, app_cfg)
        result.phases["setup"] = setup_stats
        if setup_stats.get("error") and not cfg.dry_run:
            result.ok = False
            result.error = str(setup_stats.get("error"))
            result.wall_s = round(time.time() - t_wall, 3)
            _print_summary(cfg, result)
            return result

        rows, reg_stats = phase_register(cfg, app_cfg)
        result.register_rows = rows
        result.phases["register"] = reg_stats
        if reg_stats.get("error") and not cfg.dry_run:
            result.ok = False
            result.error = str(reg_stats.get("error"))

        pub_stats = phase_publish(cfg, app_cfg, rows)
        result.publish = pub_stats
        result.phases["publish"] = pub_stats

        up_stats = phase_upload(cfg, app_cfg, rows)
        result.upload = up_stats
        result.phases["upload"] = up_stats

        if int(reg_stats.get("fail") or 0) > 0:
            result.ok = False
        if int(up_stats.get("failed") or up_stats.get("fail") or 0) > 0:
            result.ok = False

    except Exception as exc:
        log.exception("pipeline failed: %s", exc)
        result.ok = False
        result.error = str(exc)
        result.phases["error"] = {"message": str(exc)}

    result.wall_s = round(time.time() - t_wall, 3)
    _print_summary(cfg, result)
    return result


def parse_common_args(argv: list[str] | None = None) -> tuple[Any, list[str]]:
    """Shared argparse builder pieces for thin scripts. Returns (parser, remaining).

    Scripts should add target-specific flags then call this pattern::

        p = argparse.ArgumentParser()
        add_common_arguments(p)
        args = p.parse_args()
    """
    raise NotImplementedError("use add_common_arguments(parser) instead")


def add_common_arguments(parser: Any) -> None:
    """Attach shared flags to an ArgumentParser."""
    parser.add_argument("-n", type=int, default=1, help="how many accounts to register (default 1)")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="concurrency (default 1)")
    parser.add_argument(
        "--mail-backend",
        default="cloudmail",
        help="mail backend for future real register (default cloudmail)",
    )
    parser.add_argument(
        "--captcha-backend",
        default="auto",
        help="captcha backend (default auto)",
    )
    parser.add_argument("--region", default="US")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no real register/upload — print pipeline shape + # progress",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="quiet process logs (default is verbose / -v style)",
    )
    parser.add_argument("--ascii-log", action="store_true")
    parser.add_argument(
        "--skip-register",
        action="store_true",
        help="skip register; upload from existing packs only",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="register+publish only",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        help="skip setup phase",
    )
    parser.add_argument(
        "--limit-upload",
        type=int,
        default=None,
        metavar="N",
        help="max accounts/files to upload (0/omit = no limit)",
    )
    parser.add_argument(
        "--fail-every",
        type=int,
        default=0,
        help="dry-run only: fail every N-th simulated register",
    )
    parser.add_argument(
        "--dry-sleep",
        type=float,
        default=0.0,
        help="dry-run only: sleep seconds per simulated register",
    )


def config_from_namespace(args: Any, target: Target) -> PipelineConfig:
    """Build PipelineConfig from argparse namespace."""
    return PipelineConfig(
        target=target,
        n=int(getattr(args, "n", 1) or 1),
        jobs=int(getattr(args, "jobs", 1) or 1),
        mail_backend=str(getattr(args, "mail_backend", "cloudmail") or "cloudmail"),
        captcha_backend=str(getattr(args, "captcha_backend", "auto") or "auto"),
        region=str(getattr(args, "region", "US") or "US"),
        dry_run=bool(getattr(args, "dry_run", False)),
        quiet=bool(getattr(args, "quiet", False)),
        ascii_log=bool(getattr(args, "ascii_log", False)),
        skip_register=bool(getattr(args, "skip_register", False)),
        skip_upload=bool(getattr(args, "skip_upload", False)),
        skip_setup=bool(getattr(args, "skip_setup", False)),
        upload_limit=getattr(args, "limit_upload", None),
        cpa_missing_only=bool(getattr(args, "cpa_missing", True)),
        sub2api_on_exists=str(getattr(args, "on_exists", "overwrite") or "overwrite"),
        batch_file=getattr(args, "batch", None),
        extra={
            "fail_every": int(getattr(args, "fail_every", 0) or 0),
            "dry_sleep": float(getattr(args, "dry_sleep", 0) or 0),
        },
    )
