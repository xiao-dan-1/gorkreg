#!/usr/bin/env python3
"""Backfill output/bench_runs.jsonl from output/prod_cloudmail/batch_*.md headers."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grokreg.bench import record_batch

PROD = _ROOT / "output" / "prod_cloudmail"


def main() -> int:
    if not PROD.is_dir():
        print("no", PROD)
        return 1
    n_ok = 0
    for p in sorted(PROD.glob("batch_*.md")):
        blob = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(
            r"j=(\d+)\s+n=(\d+)\s+ok=(\d+)/(\d+).*?wall=([0-9.]+)s",
            blob,
            re.S,
        )
        if not m:
            continue
        jobs = int(m.group(1))
        total = int(m.group(4))
        ok = int(m.group(3))
        wall = float(m.group(5))
        results = []
        for i in range(ok):
            results.append(
                {
                    "email": f"backfill_ok_{i}@hist.local",
                    "sso": "1",
                    "error": None,
                    "elapsed_sec": wall / max(ok, 1),
                    "timings_sec": {},
                }
            )
        for i in range(total - ok):
            results.append(
                {
                    "email": f"backfill_fail_{i}@hist.local",
                    "sso": "",
                    "error": "unknown",
                    "error_code": "unknown",
                    "elapsed_sec": 0,
                    "timings_sec": {},
                }
            )
        rec = record_batch(
            results=results,
            jobs=jobs,
            mail_backend="cloudmail",
            wall_sec=wall,
            ok=ok,
            fail=total - ok,
            batch_file=str(p),
            note=f"backfill {p.name}",
            extra={"source": "bench_backfill_prod_cloudmail", "hist_file": p.name},
        )
        print(f"ok {p.name}: rate={rec.get('success_rate')} wall={wall}")
        n_ok += 1
    print(f"backfilled {n_ok} runs → output/bench_runs.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
