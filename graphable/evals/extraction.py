"""Extraction quality vs the generator's gold triples (truth/extraction_gold.jsonl): precision / recall per relation.

  python -m graphable.evals.extraction --tenant acme
Compares the graph's extracted edges (those carrying extracted_by provenance) with gold facts that the prose was
rendered from. RESOLVED_BY is loaded from the incident tool (not extracted) and is excluded.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from ..core.db import TenantGraph
from ..settings import ROOT, settings

RELS = {"CAUSED_BY": ("Incident", None), "MITIGATED_WITH": ("Incident", "Runbook"), "DECIDED_BY": ("Decision", "Person"),
        "AFFECTS": ("Decision", "Component"), "SUPERSEDES": ("Decision", "Decision"), "OWNS": ("Team", "Component")}


def extracted(tg: TenantGraph) -> dict[str, set]:
    out = defaultdict(set)
    for rel, (l1, l2) in RELS.items():
        dst = f":{l2}" if l2 else ""
        rows = tg.read(f"""MATCH (a:{l1})-[r:{rel}]->(b{dst}) WHERE r.extracted_by IS NOT NULL OR r.corroborated_by IS NOT NULL
            RETURN a.id AS s, b.id AS d""", timeout=60_000)
        out[rel] = {(r["s"], r["d"]) for r in rows}
    return out


def gold(tenant: str) -> tuple[dict[str, set], set]:
    g, help_mentions = defaultdict(set), set()
    for line in (settings.data_dir / tenant / "truth" / "extraction_gold.jsonl").open():
        x = json.loads(line)
        for s, rel, o in x["triples"]:
            if rel in RELS:
                g[rel].add((s, o))
            elif rel == "ANSWERED_ABOUT":
                help_mentions.add((x["source_id"], o))
    return g, help_mentions


def evaluate(tenant: str = "acme") -> dict:
    tg = TenantGraph(tenant)
    ex = extracted(tg)
    gd, hm = gold(tenant)
    res = {}
    tp_all = fp_all = fn_all = 0
    for rel in RELS:
        e, g = ex.get(rel, set()), gd.get(rel, set())
        tp, fp, fn = len(e & g), len(e - g), len(g - e)
        tp_all, fp_all, fn_all = tp_all + tp, fp_all + fp, fn_all + fn
        res[rel] = {"gold": len(g), "extracted": len(e), "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                    "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    ment = {(r["t"], r["c"]) for r in tg.read("MATCH (t:Thread)-[:MENTIONS]->(c:Component) RETURN t.id AS t, c.id AS c", timeout=60_000)}
    res["help_thread_entity_recall"] = round(len(hm & ment) / len(hm), 3) if hm else None
    res["micro"] = {"precision": round(tp_all / (tp_all + fp_all), 3) if tp_all + fp_all else None,
                    "recall": round(tp_all / (tp_all + fn_all), 3) if tp_all + fn_all else None}
    model = tg.read("MATCH (x:ExtractionRun) RETURN x.model AS m ORDER BY x.at DESC LIMIT 1")
    res["extractor"] = model[0]["m"] if model else None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="acme")
    a = ap.parse_args()
    r = evaluate(a.tenant)
    out = ROOT / "evals_out" / f"extraction_{r['extractor']}.json"
    out.write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
