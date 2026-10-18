"""Root-cause evaluation on held-out synthetic scenarios with ground truth (ARCHITECTURE §14).

  python -m graphable.evals.rca --set train            # collect features + baselines
  python -m graphable.evals.rca --set test --tuned     # final numbers with weights/theta tuned on TRAIN only

Per scenario: fresh GRAPH.COPY of the golden tenant -> play events -> graph-based intake -> rank.
Baselines: random change in window (expected value), most recent change anywhere, toy (hops -> recency, our
feasibility test), and the weighted parsimonious-cover ranker.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

from ..core import tools as T
from ..core.evidence import Ctx
from ..settings import ROOT, settings
from ..sim.player import drop_tenant, fresh_tenant, intake, play

OUT = ROOT / "evals_out"
MIN = 60_000


def scenarios(set_name: str, tenant: str = "acme") -> list[dict]:
    d = settings.data_dir / tenant / "scenarios" / set_name
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def collect(sc: dict, tenant: str = "acme") -> dict:
    et = f"eval_{sc['id'].lower().replace('-', '')}"
    tg = fresh_tenant(tenant, et)
    try:
        play(tg, sc)
        inc = intake(tg, sc["t0"], f"inc_{et}", f"INC-{sc['id']}")
        if not inc:
            return {"id": sc["id"], "error": "no incident"}
        ctx = Ctx(tg, now=inc["detected_at"] + 5 * MIN, incident_id=inc["incident_id"], register=False)
        rc = T.incident_rank_root_causes(ctx, inc["incident_id"], top_k=60)
        t0 = rc.get("t0", inc["detected_at"])
        lo, hi = t0 - 120 * MIN, t0 + 5 * MIN
        window = tg.read("MATCH (c:Change)-[:CHANGED]->(e:Component) WHERE c.ts >= $lo AND c.ts <= $hi AND c.rollback_of IS NULL "
                         "RETURN c.id AS id, c.ts AS ts, e.id AS e", {"lo": lo, "hi": hi})
        recent = tg.read("MATCH (c:Change) WHERE c.ts <= $t AND c.rollback_of IS NULL RETURN c.id AS id ORDER BY c.ts DESC LIMIT 3", {"t": t0})
        toy = sorted(rc["candidates"], key=lambda c: (c["hops"], -c["change_ts"]))
        return {"id": sc["id"], "type": sc["type"], "distractors": sc.get("distractors"), "truth": sc["truth"],
                "alerting": inc["alerting"], "noise_dropped": inc["noise_dropped"],
                "candidates": [{"change_id": c["change_id"], "entity_id": c["entity_id"], "b": c["breakdown"]} for c in rc["candidates"]],
                "non_change": [{"entity_id": c["entity_id"], "b": c["breakdown"]} for c in rc["non_change_candidates"]],
                "window_changes": [w["id"] for w in window], "recent": [r["id"] for r in recent],
                "toy": [c["change_id"] for c in toy]}
    finally:
        drop_tenant(et)


def rescore(cands: list[dict], w: dict) -> list[dict]:
    return sorted(({**c, "s": sum(w[k] * c["b"].get(k, 0) for k in w)} for c in cands), key=lambda c: -c["s"])


def evaluate(rows: list[dict], w: dict, theta: float) -> dict:
    agg = defaultdict(list)
    by_k = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if "error" in r:
            agg["errors"].append(1)
            continue
        tr = r["truth"]
        ranked = rescore(r["candidates"], w)
        best = ranked[0]["s"] if ranked else 0.0
        abstain = best < theta
        roots = set(tr["root_change_ids"])
        is_change = tr["cause_type"] == "change"
        if is_change:
            ids = [c["change_id"] for c in ranked]
            rank = next((i + 1 for i, x in enumerate(ids) if x in roots), None)
            hit1 = (not abstain) and rank == 1
            agg["ac1"].append(hit1)
            agg["ac3"].append(rank is not None and rank <= 3)
            agg["mrr"].append(1 / rank if rank else 0)
            agg["false_abstain"].append(abstain)
            n_win = len(r["window_changes"]) or 1
            agg["base_random"].append(1 / n_win if roots & set(r["window_changes"]) else 0)
            agg["base_recent"].append(bool(r["recent"]) and r["recent"][0] in roots)
            agg["base_toy"].append(bool(r["toy"]) and r["toy"][0] in roots)
            k = r["distractors"]
            by_k[k]["ours"].append(hit1)
            by_k[k]["recent"].append(bool(r["recent"]) and r["recent"][0] in roots)
            by_k[k]["toy"].append(bool(r["toy"]) and r["toy"][0] in roots)
            by_k[k]["random"].append(1 / n_win if roots & set(r["window_changes"]) else 0)
            agg[f"ac1_{r['type']}"].append(hit1)
        else:
            agg["abstain_correct"].append(abstain)
            nc = sorted(r["non_change"], key=lambda c: -sum(v * c["b"].get(k, 0) for k, v in T.W_NC.items()))
            agg["nc_entity_ac1"].append(bool(nc) and nc[0]["entity_id"] in tr["root_entity_ids"])
            agg["base_recent_wrongly_blames"].append(True)
    mean = lambda xs: round(sum(map(float, xs)) / len(xs), 3) if xs else None
    res = {k: mean(v) for k, v in agg.items()}
    res["n"] = len(rows)
    res["by_distractors"] = {k: {m: mean(v) for m, v in d.items()} for k, d in sorted(by_k.items())}
    return res


def tune(rows: list[dict], seed: int = 7, samples: int = 4000) -> tuple[dict, float, dict]:
    """Random search over the weight simplex + theta grid, on TRAIN rows only. Objective = change AC@1 + abstain accuracy
    (balanced), tie-broken toward the research-prior weights (L1 distance) to limit overfitting."""
    rng = random.Random(seed)
    keys = list(T.W_PRIOR)
    prior = T.W_PRIOR
    best = (-1, None, None)
    thetas = [round(0.30 + 0.025 * i, 3) for i in range(13)]
    for i in range(samples):
        if i == 0:
            w = dict(prior)
        else:
            raw = [rng.random() ** 1.5 for _ in keys]
            s = sum(raw)
            w = {k: round(v / s, 3) for k, v in zip(keys, raw)}
        for th in thetas:
            m = evaluate(rows, w, th)
            obj = 0.5 * (m.get("ac1") or 0) + 0.5 * (m.get("abstain_correct") or 0) - 0.02 * sum(abs(w[k] - prior[k]) for k in keys)
            if obj > best[0]:
                best = (obj, w, th)
    return best[1], best[2], evaluate(rows, best[1], best[2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="test", choices=["train", "test", "learning"])
    ap.add_argument("--tuned", action="store_true", help="use weights/theta tuned on the train set")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t = time.time()
    cache = OUT / f"features_{a.set}.json"
    rows = [collect(sc) for sc in scenarios(a.set)]
    cache.write_text(json.dumps(rows))
    default = evaluate(rows, T.W_PRIOR, 0.45)
    report = {"set": a.set, "n": len(rows), "seconds": round(time.time() - t, 1), "default": {"weights": T.W_PRIOR, "theta": 0.45, **default}}
    if a.set == "train":
        w, th, m = tune(rows)
        report["tuned"] = {"weights": w, "theta": th, **m}
        (OUT / "tuned_params.json").write_text(json.dumps({"weights": w, "theta": th}))
    elif a.tuned and (OUT / "tuned_params.json").exists():
        tp = json.loads((OUT / "tuned_params.json").read_text())
        report["tuned"] = {**tp, **evaluate(rows, tp["weights"], tp["theta"])}
    (OUT / f"rca_{a.set}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
