"""N=100 chat probe: free-usage exhaustion rate (proxy 10808). No secrets."""
from __future__ import annotations

import json
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from grokreg.config import ensure_dotenv
from grokreg.oauth.probe_quota import probe_quota

ROOT = Path(__file__).resolve().parents[1]
N = 100
PROXY = "http://127.0.0.1:10808"
SEED = 20260719


def exp_unix(e: dict) -> float | None:
    ea = e.get("expires_at")
    if ea is None:
        return None
    if isinstance(ea, (int, float)):
        return float(ea)
    s = str(ea).strip()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        return datetime.fromisoformat(s2).timestamp()
    except Exception:
        return None


def load_alive(pool: dict, now: float) -> list[dict]:
    if isinstance(pool, dict) and "accounts" in pool and isinstance(pool["accounts"], dict):
        entries = [v for v in pool["accounts"].values() if isinstance(v, dict)]
    elif isinstance(pool, dict):
        entries = [v for v in pool.values() if isinstance(v, dict)]
    else:
        entries = []
    out: list[dict] = []
    for e in entries:
        if e.get("disabled"):
            continue
        em = (e.get("email") or "").strip()
        at = (e.get("access_token") or e.get("key") or "").strip()
        eu = exp_unix(e)
        if not em or not at or eu is None or eu <= now + 180:
            continue
        domain = em.split("@")[-1].lower() if "@" in em else "?"
        out.append(
            {
                "email": em,
                "at": at,
                "exp_in_h": round((eu - now) / 3600, 2),
                "domain": domain,
            }
        )
    return out


def main() -> int:
    ensure_dotenv()
    random.seed(SEED)
    now = time.time()
    pool = json.loads((ROOT / "auth.json").read_text(encoding="utf-8"))
    alive = load_alive(pool, now)
    if len(alive) < N:
        picks = alive[:]
    else:
        picks = random.sample(alive, N)

    sample_path = ROOT / "output" / "quota_exhausted_n100_sample.json"
    sample_path.write_text(
        json.dumps(
            {
                "picked_at": datetime.now(timezone.utc).isoformat(),
                "alive_pool": len(alive),
                "n": len(picks),
                "seed": SEED,
                "emails": [p["email"] for p in picks],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"alive_pool={len(alive)} sample={len(picks)} proxy={PROXY} seed={SEED}",
        flush=True,
    )

    results: list[dict] = []
    class_ctr: Counter[str] = Counter()
    domain_exh: Counter[str] = Counter()
    domain_n: Counter[str] = Counter()

    for i, p in enumerate(picks, 1):
        em = p["email"]
        domain_n[p["domain"]] += 1
        try:
            r = probe_quota(
                email=em,
                access_token=p["at"],
                proxy=PROXY,
                mode="chat",
                timeout=45.0,
                retries=1,
            )
        except Exception as exc:  # noqa: BLE001
            r = {
                "ok": False,
                "email": em,
                "status": 0,
                "classification": "probe_error",
                "error": f"{type(exc).__name__}:{exc}",
            }
        cls = str(r.get("classification") or ("healthy" if r.get("ok") else "probe_error"))
        free = bool(r.get("free_usage_exhausted") or cls == "quota_exhausted")
        if free:
            cls = "quota_exhausted"
            domain_exh[p["domain"]] += 1
        class_ctr[cls] += 1
        row = {
            "email": em,
            "domain": p["domain"],
            "exp_in_h": p["exp_in_h"],
            "ok": r.get("ok"),
            "status": r.get("status"),
            "classification": cls,
            "free_usage_exhausted": free,
            "error_code": r.get("error_code"),
            "reason": r.get("reason"),
            "error": (str(r.get("error") or "")[:200] or None),
            "free_usage_tokens": r.get("free_usage_tokens"),
            "req": r.get("grok_request_quota"),
            "tok": r.get("grok_token_quota"),
            "elapsed_sec": r.get("elapsed_sec"),
        }
        results.append(row)
        print(
            f"[{i}/{len(picks)}] {em[:42]:<42} st={r.get('status')} "
            f"class={cls:<16} free={int(free)}",
            flush=True,
        )
        time.sleep(0.4)

    n = len(results)
    exh = class_ctr.get("quota_exhausted", 0)
    healthy = class_ctr.get("healthy", 0)
    rate_lim = class_ctr.get("rate_limited", 0)
    reauth = class_ctr.get("reauth", 0)
    perm = class_ctr.get("permission_denied", 0)
    other = n - healthy - exh - rate_lim - reauth - perm

    def pct(x: int) -> float:
        return round(100.0 * x / n, 2) if n else 0.0

    domain_stats = []
    for d, total in sorted(domain_n.items(), key=lambda x: -x[1]):
        e = domain_exh.get(d, 0)
        domain_stats.append(
            {
                "domain": d,
                "n": total,
                "exhausted": e,
                "rate_pct": round(100.0 * e / total, 2) if total else 0.0,
            }
        )

    stats = {
        "n": n,
        "alive_pool": len(alive),
        "classification": dict(class_ctr),
        "exhausted_n": exh,
        "exhausted_rate_pct": pct(exh),
        "healthy_n": healthy,
        "healthy_rate_pct": pct(healthy),
        "rate_limited_n": rate_lim,
        "rate_limited_rate_pct": pct(rate_lim),
        "reauth_n": reauth,
        "permission_denied_n": perm,
        "other_n": other,
        "domain": domain_stats,
        "exhausted_emails": [r["email"] for r in results if r["free_usage_exhausted"]],
    }
    out = {
        "experiment": "quota_exhausted_n100",
        "proxy": PROXY,
        "seed": SEED,
        "finished": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "results": results,
    }
    out_path = ROOT / "output" / "quota_exhausted_n100.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n===== N=100 STATS =====", flush=True)
    print(json.dumps(stats, indent=2, ensure_ascii=False), flush=True)
    print("\nWROTE", out_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
