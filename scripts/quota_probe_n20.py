"""N=20 alive quota experiment via 10808. No secrets in output.

Run: PYTHONPATH=. python scripts/quota_probe_n20.py
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from grokreg.config import ensure_dotenv
from grokreg.oauth.probe_quota import probe_quota

ROOT = Path(__file__).resolve().parents[1]


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


def redact_billing(b: dict) -> dict:
    if not isinstance(b, dict):
        return {}
    keys = (
        "plan_code",
        "plan_name",
        "monthly_limit",
        "used",
        "remaining",
        "on_demand_cap",
        "on_demand_used",
        "prepaid_balance",
        "credit_usage_percent",
        "is_unified",
        "top_up_method",
        "period_type",
        "period_start",
        "period_end",
        "billing_period_start",
        "billing_period_end",
        "month_period_start",
        "month_period_end",
        "week_period_type",
        "week_period_start",
        "week_period_end",
    )
    return {k: b.get(k) for k in keys}


def load_alive(pool: dict, now: float) -> list[dict]:
    if isinstance(pool, dict) and "accounts" in pool and isinstance(pool["accounts"], dict):
        entries = [v for v in pool["accounts"].values() if isinstance(v, dict)]
    elif isinstance(pool, dict):
        entries = [v for v in pool.values() if isinstance(v, dict)]
    else:
        entries = []
    alive: list[dict] = []
    for e in entries:
        if e.get("disabled"):
            continue
        email = (e.get("email") or "").strip()
        at = (e.get("access_token") or e.get("key") or "").strip()
        eu = exp_unix(e)
        if not email or not at or eu is None or eu <= now + 300:
            continue
        alive.append(
            {
                "email": email,
                "at": at,
                "expires_at": eu,
                "has_rt": bool(e.get("refresh_token")),
            }
        )
    alive.sort(key=lambda r: r["expires_at"])
    return alive


def pick_stratified(alive: list[dict], n: int = 20) -> list[dict]:
    if len(alive) <= n:
        return alive[:]
    t1, t2 = len(alive) // 3, 2 * len(alive) // 3
    buckets = [alive[:t1], alive[t1:t2], alive[t2:]]
    picks: list[dict] = []
    seen: set[str] = set()
    for bucket, k in zip(buckets, (7, 7, 6)):
        cand = [x for x in bucket if x["email"] not in seen]
        random.shuffle(cand)
        for x in cand[:k]:
            seen.add(x["email"])
            picks.append(x)
    for x in alive:
        if len(picks) >= n:
            break
        if x["email"] not in seen:
            picks.append(x)
            seen.add(x["email"])
    return picks[:n]


def aggregate(results: list[dict]) -> dict:
    stats: dict = {
        "n": len(results),
        "models_ok": 0,
        "models_fail": 0,
        "billing_ok": 0,
        "billing_fail": 0,
        "chat_ok": 0,
        "chat_fail": 0,
        "status_models": {},
        "status_billing": {},
        "status_chat": {},
        "m_lim_nonzero": 0,
        "used_nonzero": 0,
        "credit_pct_nonzero": 0,
        "plan_nonempty": 0,
        "is_unified_true": 0,
        "week_weekly": 0,
        "req_limits": {},
        "tok_limits": {},
        "req_remaining_lt_limit": 0,
        "chat_429": 0,
        "used_values": [],
        "credit_values": [],
        "m_lim_values": [],
        "month_period_ends": set(),
        "week_period_ends": set(),
        "anomalies": [],
    }
    for r in results:
        for mode, key_ok, key_st in (
            ("models", "models_ok", "status_models"),
            ("billing", "billing_ok", "status_billing"),
            ("chat", "chat_ok", "status_chat"),
        ):
            m = r["modes"].get(mode) or {}
            st = str(m.get("status"))
            stats[key_st][st] = stats[key_st].get(st, 0) + 1
            if m.get("ok"):
                stats[key_ok] += 1
            else:
                stats[key_ok.replace("_ok", "_fail")] += 1
            if mode == "chat" and m.get("status") == 429:
                stats["chat_429"] += 1

        b = (r["modes"].get("billing") or {}).get("billing") or {}
        if (b.get("monthly_limit") or 0) not in (0, 0.0, None):
            stats["m_lim_nonzero"] += 1
            stats["anomalies"].append(
                {"email": r["email"], "kind": "m_lim_nonzero", "billing": b}
            )
        if (b.get("used") or 0) not in (0, 0.0, None):
            stats["used_nonzero"] += 1
            stats["anomalies"].append(
                {"email": r["email"], "kind": "used_nonzero", "used": b.get("used")}
            )
        if (b.get("credit_usage_percent") or 0) not in (0, 0.0, None):
            stats["credit_pct_nonzero"] += 1
            stats["anomalies"].append(
                {
                    "email": r["email"],
                    "kind": "credit_pct_nonzero",
                    "pct": b.get("credit_usage_percent"),
                }
            )
        if (b.get("plan_code") or b.get("plan_name") or "").strip():
            stats["plan_nonempty"] += 1
            stats["anomalies"].append(
                {
                    "email": r["email"],
                    "kind": "plan_nonempty",
                    "plan": b.get("plan_code") or b.get("plan_name"),
                }
            )
        if b.get("is_unified") is True:
            stats["is_unified_true"] += 1
        if "WEEKLY" in str(b.get("week_period_type") or b.get("period_type") or ""):
            stats["week_weekly"] += 1
        if b.get("monthly_limit") is not None:
            stats["m_lim_values"].append(b.get("monthly_limit"))
        if b.get("used") is not None:
            stats["used_values"].append(b.get("used"))
        if b.get("credit_usage_percent") is not None:
            stats["credit_values"].append(b.get("credit_usage_percent"))
        if b.get("month_period_end"):
            stats["month_period_ends"].add(b.get("month_period_end"))
        if b.get("week_period_end"):
            stats["week_period_ends"].add(b.get("week_period_end"))

        ch = r["modes"].get("chat") or {}
        rq = ch.get("grok_request_quota") or {}
        tq = ch.get("grok_token_quota") or {}
        if isinstance(rq, dict) and rq.get("limit") is not None:
            k = f"{rq.get('limit')}/{rq.get('remaining')}"
            stats["req_limits"][k] = stats["req_limits"].get(k, 0) + 1
            if (
                rq.get("remaining") is not None
                and rq.get("limit") is not None
                and rq["remaining"] < rq["limit"]
            ):
                stats["req_remaining_lt_limit"] += 1
                stats["anomalies"].append(
                    {"email": r["email"], "kind": "req_partial", "req": rq}
                )
        if isinstance(tq, dict) and tq.get("limit") is not None:
            k = f"{tq.get('limit')}/{tq.get('remaining')}"
            stats["tok_limits"][k] = stats["tok_limits"].get(k, 0) + 1
            if (
                tq.get("remaining") is not None
                and tq.get("limit") is not None
                and tq["remaining"] < tq["limit"]
            ):
                stats["anomalies"].append(
                    {"email": r["email"], "kind": "tok_partial", "tok": tq}
                )

        mo = r["modes"].get("models") or {}
        bi = r["modes"].get("billing") or {}
        if mo.get("ok") and not bi.get("ok"):
            stats["anomalies"].append(
                {
                    "email": r["email"],
                    "kind": "models_ok_billing_fail",
                    "billing_status": bi.get("status"),
                }
            )
        if bi.get("ok") and not ch.get("ok"):
            stats["anomalies"].append(
                {
                    "email": r["email"],
                    "kind": "billing_ok_chat_fail",
                    "chat_status": ch.get("status"),
                    "chat_error": ch.get("error"),
                }
            )

    stats["month_period_ends"] = sorted(stats["month_period_ends"])
    stats["week_period_ends"] = sorted(stats["week_period_ends"])
    return stats


def main() -> int:
    ensure_dotenv()
    random.seed(20260718)
    now = time.time()
    pool = json.loads((ROOT / "auth.json").read_text(encoding="utf-8"))
    alive = load_alive(pool, now)
    picks = pick_stratified(alive, 20)

    sample = {
        "picked_at": datetime.now(timezone.utc).isoformat(),
        "alive_pool": len(alive),
        "emails": [p["email"] for p in picks],
        "exp_in_h": [round((p["expires_at"] - now) / 3600, 2) for p in picks],
    }
    (ROOT / "output" / "quota_probe_samples_n20.json").write_text(
        json.dumps(sample, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("pool_alive", len(alive), "picked", len(picks))
    if sample["exp_in_h"]:
        print("exp_in_h range", min(sample["exp_in_h"]), "->", max(sample["exp_in_h"]))

    proxy = "http://127.0.0.1:10808"
    modes = ("models", "billing", "chat")
    results: list[dict] = []

    for i, p in enumerate(picks, 1):
        email = p["email"]
        row = {
            "email": email,
            "exp_in_h": round((p["expires_at"] - now) / 3600, 2),
            "modes": {},
        }
        print(f"\n=== [{i}/{len(picks)}] {email} exp_in_h={row['exp_in_h']} ===", flush=True)
        for mode in modes:
            try:
                r = probe_quota(
                    email=email,
                    access_token=p["at"],
                    proxy=proxy,
                    mode=mode,
                    timeout=45.0,
                    retries=1,
                )
            except Exception as exc:  # noqa: BLE001
                r = {
                    "ok": False,
                    "error": f"exc:{type(exc).__name__}:{exc}",
                    "status": 0,
                }
            b = redact_billing(r.get("billing") or {})
            usage = r.get("usage") if isinstance(r.get("usage"), dict) else {}
            slim = {
                "ok": r.get("ok"),
                "status": r.get("status"),
                "error": (str(r.get("error"))[:120] if r.get("error") else None),
                "elapsed_sec": r.get("elapsed_sec"),
                "model_count": len(r.get("model_ids") or []),
                "has_grok_45": r.get("has_grok_45"),
                "billing": b,
                "grok_quota_snapshot_state": r.get("grok_quota_snapshot_state"),
                "grok_request_quota": r.get("grok_request_quota"),
                "grok_token_quota": r.get("grok_token_quota"),
                "usage_total_tokens": usage.get("total_tokens"),
                "usage_cost_ticks": usage.get("cost_in_usd_ticks"),
            }
            row["modes"][mode] = slim
            print(
                f"  {mode:7} ok={slim['ok']} st={slim['status']} "
                f"m_lim={b.get('monthly_limit')} used={b.get('used')} "
                f"pct={b.get('credit_usage_percent')} "
                f"req={slim.get('grok_request_quota')} tok={slim.get('grok_token_quota')} "
                f"err={slim['error']} t={slim['elapsed_sec']}",
                flush=True,
            )
            time.sleep(0.35 if mode != "chat" else 0.6)
        results.append(row)

    stats = aggregate(results)
    out = {
        "experiment": "quota_probe_n20_alive",
        "proxy": proxy,
        "sample": sample,
        "finished": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "results": results,
    }
    out_path = ROOT / "output" / "quota_probe_experiment_n20.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n===== STATS =====")
    print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
    print("\nWROTE", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
