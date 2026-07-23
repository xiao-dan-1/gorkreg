#!/usr/bin/env python3
"""P0 scrape-cache A/B: same-process multi-account so cache can hit.

Arms:
  B_on  : scrape_cache=True  (n=2)
  A_off : scrape_cache=False (n=2)  — after clear

Writes output/exp_scrape_cache/p0_*/
"""
from __future__ import annotations

import json
import logging
import statistics
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

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
log = logging.getLogger("exp_scrape_cache_p0")


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
        "elapsed_s": result.get("elapsed_sec"),
        "wall_s": round(wall_s, 3),
        "error": (result.get("error") or None),
        "short_body_hits": result.get("short_body_hits"),
        "create_body_len": result.get("create_body_len"),
    }


def run_arm(
    *,
    arm: str,
    n: int,
    use_cache: bool,
    cfg: dict,
    proxy_override: str | None,
) -> dict:
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
                    "elapsed_s": None,
                    "wall_s": None,
                    "error": f"alloc:{exc}",
                    "short_body_hits": None,
                    "create_body_len": None,
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
        log.info("=== arm=%s i=%s/%s email=%s cache=%s ===", arm, i, n, email, use_cache)
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
            "row arm=%s i=%s ok=%s cache=%s scrape=%s elapsed=%s err=%s",
            arm,
            i,
            row["ok"],
            row["scrape_cache"],
            row["scrape_s"],
            row["elapsed_s"],
            row["error"],
        )
        # persist per-account safe row only (no secrets)
        (OUT / f"p0_{arm}_{i}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    wall_total = time.time() - t_wall0
    scrapes = [r["scrape_s"] for r in rows if isinstance(r.get("scrape_s"), (int, float))]
    hits = sum(1 for r in rows if r.get("scrape_cache") == "hit")
    misses = sum(1 for r in rows if r.get("scrape_cache") == "miss")
    oks = sum(1 for r in rows if r.get("ok"))
    summary = {
        "arm": arm,
        "use_cache": use_cache,
        "n": n,
        "ok": oks,
        "raw_ok_rate": round(100.0 * oks / n, 1) if n else 0.0,
        "hit": hits,
        "miss": misses,
        "hit_rate": round(100.0 * hits / n, 1) if n else 0.0,
        "scrape_p50": round(statistics.median(scrapes), 3) if scrapes else None,
        "scrape_mean": round(statistics.mean(scrapes), 3) if scrapes else None,
        "wall_total_sec": round(wall_total, 3),
        "scrape_cache_stats": scrape_cache.stats(),
        "rows": rows,
    }
    return summary


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg = load_config()
    proxy_override = None  # use config default (7890 chain)

    meta = {
        "exp": "scrape_cache_p0",
        "ts_utc": ts,
        "design": "same-process B_on n=2 then A_off n=2; cloudmail; j1",
        "hypotheses": ["H1 hit on 2nd", "H2 scrape hit << miss", "H4 ok not tanked"],
    }
    log.info("start %s", meta)

    # B first so we observe miss→hit in one process
    b = run_arm(arm="B_on", n=2, use_cache=True, cfg=cfg, proxy_override=proxy_override)
    a = run_arm(arm="A_off", n=2, use_cache=False, cfg=cfg, proxy_override=proxy_override)

    # compare
    b_scrapes = [r["scrape_s"] for r in b["rows"] if isinstance(r.get("scrape_s"), (int, float))]
    a_scrapes = [r["scrape_s"] for r in a["rows"] if isinstance(r.get("scrape_s"), (int, float))]
    b2 = next((r for r in b["rows"] if r.get("idx") == 2), None)
    b1 = next((r for r in b["rows"] if r.get("idx") == 1), None)

    compare = {
        "meta": meta,
        "B_on": {k: v for k, v in b.items() if k != "rows"},
        "A_off": {k: v for k, v in a.items() if k != "rows"},
        "rows": b["rows"] + a["rows"],
        "checks": {
            "B1_miss": (b1 or {}).get("scrape_cache") == "miss",
            "B2_hit": (b2 or {}).get("scrape_cache") == "hit",
            "A_all_miss": all(r.get("scrape_cache") == "miss" for r in a["rows"] if r.get("scrape_cache")),
            "H1_hit_on_second": (b2 or {}).get("scrape_cache") == "hit",
            "H2_scrape_drop": bool(
                b1
                and b2
                and isinstance(b1.get("scrape_s"), (int, float))
                and isinstance(b2.get("scrape_s"), (int, float))
                and b2["scrape_s"] < max(1.0, 0.2 * float(b1["scrape_s"]))
            ),
            "scrape_saved_b1_minus_b2": (
                round(float(b1["scrape_s"]) - float(b2["scrape_s"]), 3)
                if b1 and b2 and isinstance(b1.get("scrape_s"), (int, float)) and isinstance(b2.get("scrape_s"), (int, float))
                else None
            ),
            "A_scrape_p50": round(statistics.median(a_scrapes), 3) if a_scrapes else None,
            "B_scrape_p50": round(statistics.median(b_scrapes), 3) if b_scrapes else None,
        },
    }

    out_json = OUT / f"p0_compare_{ts}.json"
    out_json.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# scrape cache P0  {ts}",
        "",
        "| arm | # | cache | scrape_s | elapsed | ok | error |",
        "|-----|---|-------|----------|---------|----|-------|",
    ]
    for r in compare["rows"]:
        lines.append(
            f"| {r['arm']} | {r['idx']} | {r.get('scrape_cache')} | {r.get('scrape_s')} | "
            f"{r.get('elapsed_s')} | {r.get('ok')} | {r.get('error') or '-'} |"
        )
    lines += [
        "",
        "## checks",
        f"- B1 miss: {compare['checks']['B1_miss']}",
        f"- B2 hit: {compare['checks']['B2_hit']}",
        f"- A all miss: {compare['checks']['A_all_miss']}",
        f"- H2 scrape drop: {compare['checks']['H2_scrape_drop']}",
        f"- scrape saved (B1-B2): {compare['checks']['scrape_saved_b1_minus_b2']}s",
        f"- B wall_total: {b['wall_total_sec']}s  A wall_total: {a['wall_total_sec']}s",
        f"- B raw_ok: {b['ok']}/{b['n']}  A raw_ok: {a['ok']}/{a['n']}",
        "",
        f"JSON: `{out_json}`",
    ]
    out_md = OUT / f"p0_compare_{ts}.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    log.info("wrote %s %s", out_json, out_md)
    # exit 0 if experiment ran; failures of individual regs are in table
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
