"""Ablation on the held-out test set: which signals carry the root-cause accuracy?

Re-scores the stored per-candidate features (evals_out/features_test.json, written by `evals.rca --set test`) with
groups of signals switched off; no graph queries are re-run. Ranking only: the system must name its top candidate, so
the "not confident" threshold is off here (with it, the shipped system scores the top-1 in rca_test.json).

  python -m graphable.evals.ablation        # writes evals_out/ablation_test.json

Signals: R recency (from the change log alone); Cov coverage and F minimality (which alerting services sit upstream of the
changed component, over DEPENDS_ON paths); K and H change-type risk and history (past CAUSED_BY edges); P hops.
"""
from __future__ import annotations

import json

from ..core import tools as T
from ..settings import ROOT
from .rca import evaluate

GRAPH = {"Cov", "F", "K", "H", "P"}
VARIANTS = {
    "all signals (as shipped)": set(),
    "graph signals only (recency off)": {"R"},
    "without dependency coverage and minimality (Cov, F)": {"Cov", "F"},
    "without past-incident history (K, H)": {"K", "H"},
    "recency only (graph signals off)": GRAPH,
}


def main():
    d = ROOT / "evals_out"
    rows = json.loads((d / "features_test.json").read_text())
    w = json.loads((d / "tuned_params.json").read_text()).get("weights", T.W)
    out = {}
    for name, off in VARIANTS.items():
        r = evaluate(rows, {k: (0.0 if k in off else v) for k, v in w.items()}, 0.0)
        out[name] = {"switched_off": sorted(off), "top1": r["ac1"], "top3": r["ac3"], "mrr": r["mrr"]}
        print(f"{name:55s} top-1 {r['ac1']:.2f}  top-3 {r['ac3']:.2f}")
    r = evaluate(rows, T.W_PRIOR, 0.0)  # the same ranker before tuning (research-prior weights), also forced to pick
    out["untuned weights"] = {"switched_off": [], "top1": r["ac1"], "top3": r["ac3"], "mrr": r["mrr"]}
    out["baselines"] = {"random_recent_change": r["base_random"], "newest_change": r["base_recent"], "hops_then_recency": r["base_toy"]}
    print(f"{'untuned weights':55s} top-1 {r['ac1']:.2f}  top-3 {r['ac3']:.2f}")
    (d / "ablation_test.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
