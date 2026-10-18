"""Verifier (pure code, no LLM; ARCHITECTURE §8.4). A claim survives only if:
 1. every cited evidence id exists AND belongs to this incident,
 2. every edge recorded in that evidence exists in the graph AND was valid at incident time
    (bi-temporal edges closed via valid_to before the incident do not count),
 3. every entity the claim names appears in the cited evidence's node set.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .db import TenantGraph
from .evidence import fetch
from .tools import label_of


def _norm(e: str) -> str:
    """Claims may name incidents by key (INC-0287); evidence stores ids (inc_0287)."""
    return e.lower().replace("-", "_") if re.match(r"^INC-[A-Za-z0-9-]+$", e) else e


def _show(x: str) -> str:
    return x.split("_", 1)[1] if x.startswith(("svc_", "ds_")) else x


def _edges_exist(tg: TenantGraph, keys: set[str], at: int | None = None) -> set[str]:
    groups = defaultdict(list)
    for k in keys:
        s, rel, d = k.split("|")
        try:
            groups[(label_of(s), rel, label_of(d))].append([s, d])
        except ValueError:
            continue
    ok = set()
    for (ls, rel, ld), pairs in groups.items():
        rows = tg.read(f"UNWIND $p AS p MATCH (a:{ls} {{id:p[0]}})-[r:{rel}]->(b:{ld} {{id:p[1]}}) "
                       f"WHERE $at IS NULL OR r.valid_to IS NULL OR r.valid_to > $at RETURN DISTINCT p[0] + '|{rel}|' + p[1] AS k",
                       {"p": pairs, "at": at})
        ok |= {r["k"] for r in rows}
    return ok


def verify(tg: TenantGraph, incident_id: str, claims: list[dict]) -> dict:
    ids = sorted({e for c in claims for e in c.get("evidence_ids", [])})
    ev = fetch(tg, ids) if ids else {}
    all_edges = {k for e in ev.values() for k in (e["e"] or [])}
    t0 = tg.read("MATCH (i:Incident {id:$id}) RETURN coalesce(i.detected_at, i.started_at) AS t", {"id": incident_id})
    at = t0[0]["t"] if t0 else None
    live = _edges_exist(tg, all_edges, at) if all_edges else set()
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
            reasons.append(f"{len(missing)} cited relationship(s) did not exist at incident time")
        nodes = {n for x in cited for n in (x["n"] or [])}
        not_on_path = [e for e in c.get("entity_ids", []) if _norm(e) not in nodes]
        if not_on_path:
            reasons.append("names something its evidence does not contain: " + ", ".join(_show(e) for e in not_on_path))
        out.append({**c, "verified": not reasons, "reasons": reasons})
    return {"claims": out, "n_verified": sum(c["verified"] for c in out), "n_claims": len(out),
            "path_validity": round(n_ok / n_edges, 4) if n_edges else 1.0}
