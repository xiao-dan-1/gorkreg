#!/usr/bin/env python3
"""P1 scrape-cache A/B: same-process j1 x N per arm.

B_on  : cache ON  (expect 1 miss + N-1 hits)
A_off : cache OFF (all miss)

Primary metrics: hit_rate, scrape_p50, scrape_saved, raw_ok_rate.
Wall/elapsed: also report trimmed (drop rows with sso_s>30 or elapsed>60).
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from grokreg.config import ensure_dotenv, load_config
from grokreg.mail_cloudmail import allocate_cloudmail_address
from grokreg.pipeline.register import RegisterOptions, register_one, result_ok
from grokreg import scrape_cache

ensure_dotenv()

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "exp_scrape_cache"
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("exp_scrape_cache_p1")


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 3) if xs else None


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 3) if xs else None


def _p90(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(0.9 * (len(s) - 1)))))
    return round(s[i], 3)


def _safe_row(result: dict, *, arm: str, idx: int, wall_s: float) -> dict:
    t = result.get("timings_sec") or {}
    return {
        "arm": arm,
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
        "error": (result.get("error") or None),
        "short_body_hits": result.get("short_body_hits"),
        "create_body_len": result.get("create_body_len"),
        "outlier_sso": bool(
            isinstance(t.get("sso"), (int, float)) and float(t["sso"]) > 30.0
        ),
    }


def run_arm(
    *,
    arm: str,
    n: int,
    use_cache: bool,
    cfg: dict,
    proxy_override: str | None,
    prefix: str,
) -> dict[str, Any]:
    scrape_cache.clear()
    rows: list[dict] = []
    t_wall0 = time.time()
    for i in range(1, n + 1):
        try:
            email = allocate_cloudmail_address(cfg)
        except Exception as exc:
            rows.append(
                {
                    "arm": arm,
                    "idx": i,
                    "email": "",
                    "ok": False,
                    "scrape_cache": None,
                    "scrape_s": None,
                    "turnstile_s": None,
                    "wait_code_s": None,
                    "sso_s": None,
                    "elapsed_s": None,
                    "wall_s": None,
                    "error": f"alloc:{exc}",
                    "short_body_hits": None,
                    "create_body_len": None,
                    "outlier_sso": False,
                }
            )
            log.error("alloc failed arm=%s i=%s: %s", arm, i, exc)
            continue

        opts = RegisterOptions(
            mail_backend="cloudmail",
            captcha_backend="auto",
            verbose=True,
            require_captcha_config=True,
            scrape_cache=use_cache,
            scrape_cache_ttl=600.0,
            create_short_body_retries=3,
        )
        log.info("=== %s arm=%s i=%s/%s email=%s cache=%s ===", prefix, arm, i, n, email, use_cache)
        t0 = time.time()
        try:
            result = register_one(cfg, email, proxy_override, opts)
        except Exception as exc:
            log.error("register exception: %s\n%s", exc, traceback.format_exc())
            result = {
                "email": email,
                "error": f"exception:{exc}",
                "timings_sec": {},
                "scrape_cache": None,
            }
        wall = time.time() - t0
        row = _safe_row(result, arm=arm, idx=i, wall_s=wall)
        rows.append(row)
        log.info(
            "row arm=%s i=%s ok=%s cache=%s scrape=%s turnstile=%s sso=%s elapsed=%s err=%s",
            arm,
            i,
            row["ok"],
            row["scrape_cache"],
            row["scrape_s"],
            row["turnstile_s"],
            row["sso_s"],
            row["elapsed_s"],
            row["error"],
        )
        (OUT / f"{prefix}_{arm}_{i:02d}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    wall_total = time.time() - t_wall0
    scrapes = [float(r["scrape_s"]) for r in rows if isinstance(r.get("scrape_s"), (int, float))]
    hits = [r for r in rows if r.get("scrape_cache") == "hit"]
    misses = [r for r in rows if r.get("scrape_cache") == "miss"]
    hit_scrapes = [float(r["scrape_s"]) for r in hits if isinstance(r.get("scrape_s"), (int, float))]
    miss_scrapes = [float(r["scrape_s"]) for r in misses if isinstance(r.get("scrape_s"), (int, float))]
    oks = sum(1 for r in rows if r.get("ok"))
    clean = [r for r in rows if not r.get("outlier_sso") and isinstance(r.get("elapsed_s"), (int, float))]
    clean_elapsed = [float(r["elapsed_s"]) for r in clean]
    turnstiles = [float(r["turnstile_s"]) for r in rows if isinstance(r.get("turnstile_s"), (int, float))]
    short_n = sum(1 for r in rows if (r.get("short_body_hits") or 0) > 0)
    inv = scrape_cache.stats().get("invalidate", 0)

    return {
        "arm": arm,
        "use_cache": use_cache,
        "n": n,
        "ok": oks,
        "raw_ok_rate": round(100.0 * oks / n, 1) if n else 0.0,
        "hit": len(hits),
        "miss": len(misses),
        "hit_rate": round(100.0 * len(hits) / n, 1) if n else 0.0,
        "scrape_p50": _median(scrapes),
        "scrape_p90": _p90(scrapes),
        "scrape_mean": _mean(scrapes),
        "scrape_hit_p50": _median(hit_scrapes),
        "scrape_miss_p50": _median(miss_scrapes),
        "turnstile_p50": _median(turnstiles),
        "elapsed_p50_all": _median(
            [float(r["elapsed_s"]) for r in rows if isinstance(r.get("elapsed_s"), (int, float))]
        ),
        "elapsed_p50_clean": _median(clean_elapsed),
        "clean_n": len(clean),
        "outlier_sso_n": sum(1 for r in rows if r.get("outlier_sso")),
        "wall_total_sec": round(wall_total, 3),
        "short_accounts": short_n,
        "scrape_cache_stats": scrape_cache.stats(),
        "invalidate_end": inv,
        "rows": rows,
    }


def compare_arms(b: dict, a: dict, *, meta: dict) -> dict:
    b_hit_scrape = b.get("scrape_hit_p50")
    a_scrape = a.get("scrape_p50")
    saved = None
    if isinstance(b_hit_scrape, (int, float)) and isinstance(a_scrape, (int, float)):
        saved = round(float(a_scrape) - float(b_hit_scrape), 3)
    # also B miss (first) vs hit
    b_miss = b.get("scrape_miss_p50")
    b_internal_saved = None
    if isinstance(b_miss, (int, float)) and isinstance(b_hit_scrape, (int, float)):
        b_internal_saved = round(float(b_miss) - float(b_hit_scrape), 3)

    # expected: B hit_rate ~ (n-1)/n if first always miss
    n = int(b.get("n") or 0)
    expected_hit_rate = round(100.0 * max(0, n - 1) / n, 1) if n else 0.0

    h1 = bool(b.get("hit", 0) >= max(1, n - 1) and b.get("hit_rate", 0) >= 80.0)
    h2 = bool(
        isinstance(b_hit_scrape, (int, float))
        and b_hit_scrape <= 0.5
        and isinstance(a_scrape, (int, float))
        and a_scrape >= 3.0
    )
    h3 = False
    if isinstance(b.get("elapsed_p50_clean"), (int, float)) and isinstance(
        a.get("elapsed_p50_clean"), (int, float)
    ):
        # ON clean elapsed should be lower by roughly scrape save (noisy)
        h3 = float(a["elapsed_p50_clean"]) - float(b["elapsed_p50_clean"]) >= 3.0
    h4 = abs(float(b.get("raw_ok_rate") or 0) - float(a.get("raw_ok_rate") or 0)) <= 15.0
    a_all_miss = all(
        (r.get("scrape_cache") == "miss") for r in a.get("rows") or [] if r.get("scrape_cache")
    )
    b_first_miss = False
    brows = b.get("rows") or []
    if brows:
        b1 = next((r for r in brows if r.get("idx") == 1), brows[0])
        b_first_miss = b1.get("scrape_cache") == "miss"

    verdict = "PASS_EFFECTIVE"
    if not h1 or not h2:
        verdict = "FAIL_CACHE"
    elif not h4:
        verdict = "HARMFUL_OK_RATE"
    elif h1 and h2 and h4 and not h3:
        verdict = "PASS_SCRAPE_ONLY"  # scrape effective; wall diluted
    elif h1 and h2 and h3 and h4:
        verdict = "PASS_FULL"

    return {
        "meta": meta,
        "B_on": {k: v for k, v in b.items() if k != "rows"},
        "A_off": {k: v for k, v in a.items() if k != "rows"},
        "rows": (b.get("rows") or []) + (a.get("rows") or []),
        "checks": {
            "B_first_miss": b_first_miss,
            "A_all_miss": a_all_miss,
            "H1_hit_rate_ge_80": h1,
            "H2_hit_scrape_le_0.5": h2,
            "H3_clean_elapsed_saved_ge_3": h3,
            "H4_ok_rate_diff_le_15pp": h4,
            "expected_hit_rate": expected_hit_rate,
            "actual_hit_rate": b.get("hit_rate"),
            "scrape_saved_vs_A_p50": saved,
            "scrape_saved_B_miss_vs_hit": b_internal_saved,
            "verdict": verdict,
        },
    }


def write_reports(compare: dict, *, prefix: str, ts: str) -> tuple[Path, Path, Path]:
    out_json = OUT / f"{prefix}_compare_{ts}.json"
    out_json.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")

    ch = compare["checks"]
    b = compare["B_on"]
    a = compare["A_off"]
    lines = [
        f"# scrape cache {prefix.upper()}  {ts}",
        "",
        f"verdict: **{ch.get('verdict')}**",
        "",
        "## arms",
        f"- B_on: n={b.get('n')} ok={b.get('ok')}/{b.get('n')} ({b.get('raw_ok_rate')}%) "
        f"hit_rate={b.get('hit_rate')}% scrape_p50={b.get('scrape_p50')} "
        f"hit_scrape_p50={b.get('scrape_hit_p50')} miss_scrape_p50={b.get('scrape_miss_p50')} "
        f"elapsed_clean_p50={b.get('elapsed_p50_clean')} wall={b.get('wall_total_sec')}s "
        f"sso_outliers={b.get('outlier_sso_n')}",
        f"- A_off: n={a.get('n')} ok={a.get('ok')}/{a.get('n')} ({a.get('raw_ok_rate')}%) "
        f"hit_rate={a.get('hit_rate')}% scrape_p50={a.get('scrape_p50')} "
        f"elapsed_clean_p50={a.get('elapsed_p50_clean')} wall={a.get('wall_total_sec')}s "
        f"sso_outliers={a.get('outlier_sso_n')}",
        "",
        "## checks",
        f"- B first miss: {ch.get('B_first_miss')}",
        f"- A all miss: {ch.get('A_all_miss')}",
        f"- H1 hit_rate≥80: {ch.get('H1_hit_rate_ge_80')} (got {ch.get('actual_hit_rate')}%, expect~{ch.get('expected_hit_rate')}%)",
        f"- H2 hit scrape≤0.5s: {ch.get('H2_hit_scrape_le_0.5')}",
        f"- H3 clean elapsed save≥3s: {ch.get('H3_clean_elapsed_saved_ge_3')}",
        f"- H4 ok rate Δ≤15pp: {ch.get('H4_ok_rate_diff_le_15pp')}",
        f"- scrape saved (A_p50 − B_hit_p50): {ch.get('scrape_saved_vs_A_p50')}s",
        f"- scrape saved (B miss − hit): {ch.get('scrape_saved_B_miss_vs_hit')}s",
        "",
        "## rows",
        "| arm | # | cache | scrape | turnstile | sso | elapsed | ok | err |",
        "|-----|---|-------|--------|-----------|-----|---------|----|-----|",
    ]
    for r in compare["rows"]:
        lines.append(
            f"| {r['arm']} | {r['idx']} | {r.get('scrape_cache')} | {r.get('scrape_s')} | "
            f"{r.get('turnstile_s')} | {r.get('sso_s')} | {r.get('elapsed_s')} | {r.get('ok')} | "
            f"{(r.get('error') or '-')[:40]} |"
        )
    out_md = OUT / f"{prefix}_compare_{ts}.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return out_json, out_md, OUT / f"{prefix}_final.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10, help="accounts per arm (default 10)")
    ap.add_argument("--prefix", default="p1", help="output prefix")
    args = ap.parse_args()
    n = max(2, int(args.n))
    prefix = (args.prefix or "p1").strip() or "p1"

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg = load_config()
    meta = {
        "exp": f"scrape_cache_{prefix}",
        "ts_utc": ts,
        "n_per_arm": n,
        "design": f"same-process B_on n={n} then A_off n={n}; cloudmail; j1; ttl=600",
    }
    log.info("start %s", meta)

    b = run_arm(
        arm="B_on", n=n, use_cache=True, cfg=cfg, proxy_override=None, prefix=prefix
    )
    a = run_arm(
        arm="A_off", n=n, use_cache=False, cfg=cfg, proxy_override=None, prefix=prefix
    )
    compare = compare_arms(b, a, meta=meta)
    out_json, out_md, out_final = write_reports(compare, prefix=prefix, ts=ts)

    ch = compare["checks"]
    final_lines = [
        f"# scrape cache 实验终稿 ({prefix})  {ts}",
        "",
        f"**verdict: {ch.get('verdict')}**",
        "",
        "## 设计",
        f"- 同进程 B_on×{n}（缓存开）→ A_off×{n}（缓存关）",
        "- CloudMail + 默认代理 + 2Captcha；j=1",
        f"- 脚本：`scripts/exp_scrape_cache_p1.py -n {n}`",
        "",
        "## 主指标",
        f"| 指标 | B_on | A_off |",
        f"|------|------|-------|",
        f"| raw_ok | {b['ok']}/{n} ({b['raw_ok_rate']}%) | {a['ok']}/{n} ({a['raw_ok_rate']}%) |",
        f"| hit_rate | {b['hit_rate']}% | {a['hit_rate']}% |",
        f"| scrape_p50 | {b['scrape_p50']} | {a['scrape_p50']} |",
        f"| scrape hit/miss p50 | {b['scrape_hit_p50']} / {b['scrape_miss_p50']} | — / {a['scrape_p50']} |",
        f"| turnstile_p50 | {b['turnstile_p50']} | {a['turnstile_p50']} |",
        f"| elapsed_p50 (clean) | {b['elapsed_p50_clean']} (n={b['clean_n']}) | {a['elapsed_p50_clean']} (n={a['clean_n']}) |",
        f"| wall_total | {b['wall_total_sec']}s | {a['wall_total_sec']}s |",
        f"| SSO outliers | {b['outlier_sso_n']} | {a['outlier_sso_n']} |",
        "",
        "## 贡献",
        f"- scrape 相对 A：省 **{ch.get('scrape_saved_vs_A_p50')}s/号**（A_p50 − B_hit_p50）",
        f"- scrape 臂内 miss→hit：省 **{ch.get('scrape_saved_B_miss_vs_hit')}s**",
        f"- 总时长：clean elapsed 是否省≥3s → {ch.get('H3_clean_elapsed_saved_ge_3')} "
        f"（turnstile/SSO 噪声会稀释）",
        "",
        "## 假设",
        f"| H | 结果 |",
        f"|---|------|",
        f"| H1 hit≥80% | {ch.get('H1_hit_rate_ge_80')} |",
        f"| H2 hit scrape≤0.5s | {ch.get('H2_hit_scrape_le_0.5')} |",
        f"| H3 clean elapsed | {ch.get('H3_clean_elapsed_saved_ge_3')} |",
        f"| H4 ok 不伤 | {ch.get('H4_ok_rate_diff_le_15pp')} |",
        f"| A 全 miss | {ch.get('A_all_miss')} |",
        "",
        "## 产品建议",
    ]
    v = ch.get("verdict")
    if v in ("PASS_FULL", "PASS_SCRAPE_ONLY", "PASS_EFFECTIVE"):
        final_lines += [
            "- **默认保持 scrape 缓存开启**（仅公开页参数，TTL 600）",
            "- 批量务必 **同进程多号**；每号新开进程则永远 miss",
            "- 评估收益时主看 **scrape_s / hit_rate**，不要被 SSO 偶发超时带偏",
        ]
    elif v == "HARMFUL_OK_RATE":
        final_lines += ["- **默认关闭缓存** 或缩短 TTL，先查 next_action 失败"]
    else:
        final_lines += ["- 缓存未达预期，保持 `--no-scrape-cache` 直至复测"]

    final_lines += [
        "",
        "## 产物",
        f"- `{out_json.relative_to(ROOT)}`",
        f"- `{out_md.relative_to(ROOT)}`",
        f"- 本文件：`output/exp_scrape_cache/{prefix}_final.md`",
        "",
        "## 与 P0 关系",
        "- P0（n=2）已证 miss→hit 与 ~11s scrape 节省",
        f"- 本轮 P1（n={n}）给 hit_rate 与分位数，作默认开缓存依据",
    ]
    out_final.write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    # also write stable name
    (OUT / "FINAL.md").write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    log.info("wrote %s %s %s", out_json, out_md, out_final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
