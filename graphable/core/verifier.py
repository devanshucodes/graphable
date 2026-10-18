"""Verifier (pure code, no LLM; ARCHITECTURE §8.4). A claim survives only if:
 1. every cited evidence id exists AND belongs to this incident,
 2. every edge recorded in that evidence still exists in the graph,
 3. every entity the claim names appears in the cited evidence's node set.
"""
from __future__ import annotations

from collections import defaultdict

from .db import TenantGraph
from .evidence import fetch
from .tools import label_of


def _edges_exist(tg: TenantGraph, keys: set[str]) -> set[str]:
    groups = defaultdict(list)
    for k in keys:
        s, rel, d = k.split("|")
        try:
            groups[(label_of(s), rel, label_of(d))].append([s, d])
        except ValueError:
            continue
    ok = set()
    for (ls, rel, ld), pairs in groups.items():
        rows = tg.read(f"UNWIND $p AS p MATCH (a:{ls} {{id:p[0]}})-[:{rel}]->(b:{ld} {{id:p[1]}}) RETURN DISTINCT p[0] + '|{rel}|' + p[1] AS k",
                       {"p": pairs})
        ok |= {r["k"] for r in rows}
    return ok


def verify(tg: TenantGraph, incident_id: str, claims: list[dict]) -> dict:
    ids = sorted({e for c in claims for e in c.get("evidence_ids", [])})
    ev = fetch(tg, ids) if ids else {}
    all_edges = {k for e in ev.values() for k in (e["e"] or [])}
    live = _edges_exist(tg, all_edges) if all_edges else set()
    out, n_edges, n_ok = [], 0, 0
    for c in claims:
        reasons = []
        cited = [ev.get(i) for i in c.get("evidence_ids", [])]
        if not c.get("evidence_ids"):
            reasons.append("no evidence cited")
        if any(x is None for x in cited):
            reasons.append("unknown evidence id")
        cited = [x for x in cited if x]
        if any(x["inc"] != incident_id for x in cited):
            reasons.append("evidence from another incident")
        edges = {k for x in cited for k in (x["e"] or [])}
        missing = edges - live
        n_edges += len(edges)
        n_ok += len(edges - missing)
        if missing:
            reasons.append(f"{len(missing)} cited edges no longer exist")
        nodes = {n for x in cited for n in (x["n"] or [])}
        not_on_path = [e for e in c.get("entity_ids", []) if e not in nodes]
        if not_on_path:
            reasons.append(f"entities not in cited evidence: {not_on_path}")
        out.append({**c, "verified": not reasons, "reasons": reasons})
    return {"claims": out, "n_verified": sum(c["verified"] for c in out), "n_claims": len(out),
            "path_validity": round(n_ok / n_edges, 4) if n_edges else 1.0}
