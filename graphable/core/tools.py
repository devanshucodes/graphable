"""Domain tools over the tenant graph (ARCHITECTURE §7). Pure functions of (Ctx, args) that run
templated, bounded, read-only Cypher, compute deterministic scores in Python, and register
evidence. Shared by the MCP server and by the agents.

Direction convention: (A)-[:DEPENDS_ON]->(B) means A calls B; if B fails, A is impacted.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict

from .evidence import Ctx, edge_key, path_edges, register

DAY = 86_400_000
MIN = 60_000
PREFIX_LABEL = {"svc_": "Component", "ds_": "Component", "per_": "Person", "team_": "Team", "inc_": "Incident",
                "chg_": "Change", "alt_": "Alert", "thr_": "Thread", "msg_": "Message", "tkt_": "Ticket", "doc_": "Doc",
                "rb_": "Runbook", "dec_": "Decision", "ev_": "Evidence", "hyp_": "Hypothesis", "act_": "Action",
                "les_": "Lesson", "oc_": "OnCallShift", "ep_": "Endpoint", "ff_": "FeatureFlag"}
REL_ALLOW = {"DEPENDS_ON", "OWNS", "MEMBER_OF", "CHANGED", "AUTHORED", "REVIEWED", "AFFECTED", "CAUSED_BY",
             "RESOLVED_BY", "RESPONDED", "MITIGATED_WITH", "MITIGATES", "WROTE", "ABOUT", "MENTIONS", "DECIDED_BY",
             "DOCUMENTED_IN", "DISCUSSED_IN", "SUPERSEDES", "FIRED_ON", "PART_OF", "HAS_SHIFT", "COVERS", "REPORTS_TO",
             "SIMILAR_TO", "FOLLOW_UP_OF", "POSTMORTEM_OF", "IN", "SENT", "EXPOSES", "GATES", "DERIVED_FROM"}


def VALID(v: str) -> str:
    return f"({v}.valid_from <= $at AND ({v}.valid_to IS NULL OR {v}.valid_to > $at))"


def _hops(h, lo=1, hi=5) -> int:
    h = int(h)
    if not lo <= h <= hi:
        raise ValueError(f"hops must be in [{lo},{hi}]")
    return h


def label_of(node_id: str) -> str:
    for p, l in PREFIX_LABEL.items():
        if node_id.startswith(p):
            return l
    raise ValueError(f"unknown id prefix: {node_id}")


def _ft(q: str) -> str:
    q = re.sub(r"[^\w\s]", " ", q.replace("-", " ")).strip()
    return " | ".join(w for w in q.split() if len(w) > 1) or "x"


def _names(ctx: Ctx, ids: list[str]) -> dict:
    rows = ctx.tg.read("MATCH (c:Component) WHERE c.id IN $ids RETURN c.id AS id, c.name AS name", {"ids": list(ids)})
    return {r["id"]: r["name"] for r in rows}


# ------------------------------------------------------------------ 1. resolve
def graph_resolve(ctx: Ctx, text: str, kinds: list[str] | None = None, k: int = 5) -> dict:
    t = text.strip()
    hits = []
    low = t.lower().lstrip("@")
    for pref, lbl in PREFIX_LABEL.items():
        if low.startswith(pref):
            hits += ctx.tg.read(f"MATCH (n:{lbl} {{id:$id}}) RETURN n.id AS id, '{lbl}' AS kind, coalesce(n.name, n.key, n.title, n.id) AS name, 1.0 AS score", {"id": t})
    for lbl, prop in (("Incident", "key"), ("Runbook", "key"), ("Decision", "key"), ("Ticket", "key")):
        hits += ctx.tg.read(f"MATCH (n:{lbl}) WHERE toUpper(n.{prop}) = toUpper($t) RETURN n.id AS id, '{lbl}' AS kind, n.{prop} AS name, 1.0 AS score", {"t": t})
    norm = re.sub(r"[-_\s]+", " ", low).strip()
    hits += ctx.tg.read("MATCH (c:Component) WHERE c.name = $l OR replace(c.name, '-', ' ') = $n OR "
                        "any(a IN c.aliases WHERE toLower(a) = $l OR replace(toLower(a), '-', ' ') = $n) "
                        "RETURN c.id AS id, 'Component' AS kind, c.name AS name, 0.95 AS score", {"l": low, "n": norm})
    hits += ctx.tg.read("MATCH (p:Person) WHERE p.handle = $l OR toLower(p.name) = $l OR $l IN p.former_handles "
                        "RETURN p.id AS id, 'Person' AS kind, p.name AS name, 0.95 AS score", {"l": low})
    hits += ctx.tg.read("MATCH (tm:Team) WHERE toLower(tm.name) = $l OR tm.key = $l RETURN tm.id AS id, 'Team' AS kind, tm.name AS name, 0.95 AS score", {"l": low})
    if not hits:
        q = _ft(t).replace(" | ", " ")  # AND semantics first
        for lbl in ("Component", "Person"):
            try:
                hits += ctx.tg.read(f"CALL db.idx.fulltext.queryNodes('{lbl}', $q) YIELD node, score "
                                    f"RETURN node.id AS id, '{lbl}' AS kind, node.name AS name, score / 10.0 AS score LIMIT 5", {"q": q})
            except Exception:
                pass
    if not hits:
        q = _ft(t)
        for lbl in ("Component", "Person"):
            try:
                hits += ctx.tg.read(f"CALL db.idx.fulltext.queryNodes('{lbl}', $q) YIELD node, score "
                                    f"RETURN node.id AS id, '{lbl}' AS kind, node.name AS name, score / 10.0 AS score LIMIT 5", {"q": q})
            except Exception:
                pass
    if not hits:  # typo tolerance: RediSearch fuzzy matching (Levenshtein distance 1) on the full-text index
        words = [w for w in _ft(t).split(" | ") if len(w) > 3]
        if words:
            q = " | ".join(f"%{w}%" for w in words)
            for lbl in ("Component", "Person"):
                try:
                    hits += ctx.tg.read(f"CALL db.idx.fulltext.queryNodes('{lbl}', $q) YIELD node, score "
                                        f"RETURN node.id AS id, '{lbl}' AS kind, node.name AS name, score / 20.0 AS score LIMIT 5", {"q": q})
                except Exception:
                    pass
    seen, out = set(), []
    for h in sorted(hits, key=lambda h: -h["score"]):
        if h["id"] not in seen and (not kinds or h["kind"] in kinds):
            seen.add(h["id"])
            out.append(h)
    return {"matches": out[:k]}


# ------------------------------------------------------------------ 2. schema
SCHEMA_TEXT = """Graph schema (direction matters):
(Service|Datastore:Component)-[:DEPENDS_ON {kind, critical, valid_from, valid_to}]->(Component): A calls B; if B fails, A is impacted. Blast radius = walk AGAINST the arrow; root-cause search = walk ALONG it.
(Team)-[:OWNS {valid_from, valid_to, source_id}]->(Component)   exactly one active owner
(Person)-[:MEMBER_OF {valid_from, valid_to}]->(Team); (Person)-[:HAS_SHIFT]->(OnCallShift {start,end,level})-[:COVERS]->(Team)
(Change {kind, ts, summary})-[:CHANGED]->(Component); (Person)-[:AUTHORED|REVIEWED]->(Change)
(Alert)-[:FIRED_ON]->(Component); (Alert)-[:PART_OF]->(Incident)-[:AFFECTED]->(Component)
(Incident)-[:CAUSED_BY {source_id}]->(Change|Component); (Incident)-[:RESOLVED_BY]->(Person); (Incident)-[:MITIGATED_WITH]->(Runbook)
(Person)-[:RESPONDED {role}]->(Incident); (Runbook)-[:MITIGATES]->(Component); (Runbook)-[:HAS_STEP]->(Step)
(Doc {kind: postmortem|design|wiki})-[:ABOUT]->(Component); (Decision)-[:DOCUMENTED_IN]->(Doc); (Decision)-[:DISCUSSED_IN]->(Thread)
(Decision)-[:AFFECTS]->(Component); (Decision)-[:SUPERSEDES]->(Decision); (Message)-[:IN]->(Thread)-[:MENTIONS]->(*)
Agent memory: (Evidence)-[:FOR]->(Incident); (Hypothesis); (Handoff); (Task); (Lesson)-[:ABOUT]->(Component)
Times are epoch milliseconds."""


def graph_schema(ctx: Ctx) -> dict:
    return {"schema": SCHEMA_TEXT}


# ------------------------------------------------------------------ 3. neighbors
def graph_neighbors(ctx: Ctx, node_id: str, rel_types: list[str] | None = None, direction: str = "both", limit: int = 25) -> dict:
    lbl = label_of(node_id)
    limit = max(1, min(int(limit), 50))
    rels = [r for r in (rel_types or []) if r in REL_ALLOW]
    rt = ":" + "|".join(rels) if rels else ""
    deg = ctx.tg.read(f"MATCH (n:{lbl} {{id:$id}})-[r]-() RETURN type(r) AS t, count(r) AS c", {"id": node_id})
    total = sum(d["c"] for d in deg)
    if total > 40 and not rels:
        return {"node_id": node_id, "hub": True, "degree_by_type": {d["t"]: d["c"] for d in deg},
                "hint": "High-degree node: call again with rel_types to expand a specific relationship."}
    pat = {"out": f"(n)-[r{rt}]->(m)", "in": f"(n)<-[r{rt}]-(m)"}.get(direction, f"(n)-[r{rt}]-(m)")
    rows = ctx.tg.read(f"MATCH (n:{lbl} {{id:$id}}) MATCH {pat} RETURN startNode(r).id AS s, type(r) AS t, endNode(r).id AS d, "
                       f"coalesce(m.name, m.key, m.title, m.summary, m.id) AS label, r.valid_to AS valid_to LIMIT {limit}", {"id": node_id})
    ev = register(ctx, "graph_neighbors", {"node_id": node_id, "rel_types": rels}, [node_id] + [r["s"] for r in rows] + [r["d"] for r in rows],
                  [edge_key(r["s"], r["t"], r["d"]) for r in rows], f"{len(rows)} neighbors of {node_id}")
    return {"node_id": node_id, "edges": rows, "evidence_id": ev}


# ------------------------------------------------------------------ 4. blast radius
def incident_blast_radius(ctx: Ctx, entity_id: str, max_hops: int = 3, at: int | None = None) -> dict:
    h = _hops(max_hops, 1, 4)
    at = at or ctx.now
    rows = ctx.tg.read(f"""MATCH p=(up:Component)-[:DEPENDS_ON*1..{h}]->(s:Component {{id:$id}})
        WHERE all(r IN relationships(p) WHERE {VALID('r')})
        WITH up, p ORDER BY length(p) WITH up, collect(p)[0] AS p
        OPTIONAL MATCH (t:Team)-[o:OWNS]->(up) WHERE {VALID('o')}
        RETURN up.id AS id, up.name AS name, up.tier AS tier, coalesce(up.entry_point, false) AS entry_point,
               coalesce(up.customer_facing, false) AS customer_facing, length(p) AS hops, coalesce(t.name, '') AS owner,
               [n IN nodes(p) | n.id] AS path ORDER BY hops, tier""", {"id": entity_id, "at": at})
    edges = [e for r in rows for e in path_edges(r["path"])]
    ev = register(ctx, "incident_blast_radius", {"entity_id": entity_id, "max_hops": h}, [entity_id] + [r["id"] for r in rows], edges,
                  f"{len(rows)} upstream dependents of {entity_id}")
    entry = [r["name"] for r in rows if r["entry_point"]]
    return {"entity_id": entity_id, "impacted": rows, "n_impacted": len(rows), "entry_points_hit": entry,
            "customer_facing_hit": sum(r["customer_facing"] for r in rows), "evidence_id": ev}


# ------------------------------------------------------------------ 5. root-cause ranking
# Research-prior weights (ChangeRCA/TraceRCA/Groot-inspired) and the weights tuned on the TRAIN scenarios only
# (random search, evals/rca.py). Production uses the tuned, frozen set; both are reported on the held-out test set.
W_PRIOR = {"R": 0.30, "Cov": 0.30, "F": 0.15, "K": 0.10, "H": 0.10, "P": 0.05}
W = {"R": 0.046, "Cov": 0.541, "F": 0.145, "K": 0.209, "H": 0.003, "P": 0.055}
W_NC = {"Cov": 0.45, "F": 0.25, "S": 0.15, "H": 0.15}
ABSTAIN_THETA = 0.55


_SIG_CACHE: dict = {}


def _signature(kind: str, summary: str | None) -> str:
    """What changed: config key for config changes, otherwise the change kind (+ 'bump' library for dependency bumps)."""
    s = summary or ""
    if kind == "config":
        m = re.match(r"^[^:]+:\s*(\S+)", s)
        return f"config:{m.group(1)}" if m else "config"
    if kind == "dependency_bump":
        m = re.search(r"bump (\S+)", s)
        return f"dep:{m.group(1)}" if m else "dep"
    return kind


def _signature_prior(ctx: Ctx, before: int) -> dict:
    """Smoothed share of past changes with the same signature that were rolled back or confirmed as a cause."""
    n_confirmed = ctx.tg.read("MATCH (:Incident)-[c:CAUSED_BY]->(:Change) RETURN count(c) AS n")[0]["n"]
    key = (ctx.tg.name, before // (6 * 3_600_000), n_confirmed)  # cache invalidates when new causes are confirmed
    if key in _SIG_CACHE:
        return _SIG_CACHE[key]
    rows = _paged_changes(ctx, before)
    agg, kinds = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for r in rows:
        sg = _signature(r["kind"], r["summary"])
        for d in (agg[sg], kinds[r["kind"]]):
            d[0] += 1
            d[1] += 1 if r["bad"] else 0
    kind_rate = {k: (b + 0.5) / (n + 10) for k, (n, b) in kinds.items()}
    out = {k: (b + 5 * kind_rate.get(k.split(":")[0].replace("dep", "dependency_bump"), 0.05)) / (n + 5) for k, (n, b) in agg.items()}
    if len(_SIG_CACHE) > 64:
        _SIG_CACHE.clear()
    _SIG_CACHE[key] = (out, kind_rate)
    return out, kind_rate


def _paged_changes(ctx: Ctx, before: int) -> list[dict]:
    out, last = [], -1
    while True:  # page by ts (RESULTSET_SIZE cap)
        rows = ctx.tg.read("""MATCH (ch:Change) WHERE ch.ts < $t AND ch.ts > $last AND ch.rollback_of IS NULL
            RETURN ch.ts AS ts, ch.kind AS kind, ch.summary AS summary,
                   (coalesce(ch.rolled_back, false) OR size([(i:Incident)-[:CAUSED_BY]->(ch) | i]) > 0) AS bad
            ORDER BY ch.ts LIMIT 5000""", {"t": before, "last": last}, timeout=20000)
        out += rows
        if len(rows) < 5000:
            return out
        last = rows[-1]["ts"]


def incident_rank_root_causes(ctx: Ctx, incident_id: str, window_min: int = 120, max_hops: int = 5, top_k: int = 5,
                              weights: dict | None = None, theta: float | None = None) -> dict:
    """Parsimonious-cover root-cause ranking.
    For each component c downstream of the alerting set A:
      E    = |A covered by c| / |A|                 (recall: share of symptoms c explains)
      Prec = alerting share of c's 3-hop up-closure  (precision: hubs only win if nearly everything alerts)
      Cov  = sqrt(E * Prec)                          (Ochiai, TraceRCA/MicroRank)
      F    = 1 if no candidate downstream of c explains as much (minimal explanation; Groot/CIRCA)
    For each change x on c in the window: S = .30 R + .30 Cov + .15 F + .10 K + .05 P + .10 H
      R recency (changes after detection heavily discounted), K kind prior (rollbacks / confirmed causes),
      H history (past incidents caused by the same kind of change on c), P = 2/(2+hops) (ChangeRCA depth).
    Abstain (non-change) when the best change scores below theta."""
    w = weights or W
    theta = ABSTAIN_THETA if theta is None else theta
    H = _hops(max_hops, 1, 5)
    inc = ctx.tg.read("MATCH (i:Incident {id:$id}) RETURN i.detected_at AS t0, i.started_at AS ts", {"id": incident_id})
    if not inc:
        raise ValueError("unknown incident")
    t0 = inc[0]["t0"] or inc[0]["ts"]
    A = [r["e"] for r in ctx.tg.read("MATCH (a:Alert)-[:PART_OF]->(:Incident {id:$id}) MATCH (a)-[:FIRED_ON]->(e:Component) "
                                     "RETURN DISTINCT e.id AS e", {"id": incident_id})]
    if not A:
        return {"incident_id": incident_id, "candidates": [], "abstain": True, "reason": "no alerts linked"}
    alert_names = sorted({r["n"] for r in ctx.tg.read("MATCH (a:Alert)-[:PART_OF]->(:Incident {id:$id}) RETURN DISTINCT a.name AS n",
                                                      {"id": incident_id})})
    p = {"A": A, "at": t0}
    cand = ctx.tg.read(f"""MATCH p=(a:Component)-[:DEPENDS_ON*0..{H}]->(c:Component)
        WHERE a.id IN $A AND all(r IN relationships(p) WHERE {VALID('r')})
        WITH c, a, p ORDER BY length(p)
        WITH c, collect(DISTINCT a.id) AS srcs, collect(p)[0] AS sp
        RETURN c.id AS c, c.name AS name, c.kind AS kind, length(sp) AS h, srcs, [n IN nodes(sp) | n.id] AS path""", p)
    C = [r["c"] for r in cand]
    p["C"] = C
    up = {r["c"]: r for r in ctx.tg.read(f"""MATCH p=(u:Component)-[:DEPENDS_ON*1..3]->(c:Component)
        WHERE c.id IN $C AND all(r IN relationships(p) WHERE {VALID('r')})
        WITH c, collect(DISTINCT u.id) AS ups
        RETURN c.id AS c, size(ups) AS n_up, size([x IN ups WHERE x IN $A]) AS n_up_alerting""", p)}
    below = ctx.tg.read(f"""MATCH p=(c:Component)-[:DEPENDS_ON*1..{H}]->(d:Component)
        WHERE c.id IN $C AND d.id IN $C AND all(r IN relationships(p) WHERE {VALID('r')}) RETURN DISTINCT c.id AS c, d.id AS d""", p)
    lo, hi = t0 - window_min * MIN, t0 + 5 * MIN
    changes = ctx.tg.read("""MATCH (ch:Change)-[:CHANGED]->(c:Component) WHERE c.id IN $C AND ch.ts >= $lo AND ch.ts <= $hi
        AND ch.rollback_of IS NULL
        OPTIONAL MATCH (a:Person)-[:AUTHORED]->(ch)
        RETURN ch.id AS id, ch.kind AS kind, ch.ts AS ts, ch.summary AS summary, coalesce(ch.canary, false) AS canary,
               c.id AS c, coalesce(a.id, '') AS author""", {"C": C, "lo": lo, "hi": hi})
    hist = ctx.tg.read("""MATCH (ch:Change)-[:CHANGED]->(c:Component) WHERE c.id IN $C AND ch.ts < $lo AND ch.rollback_of IS NULL
        RETURN c.id AS c, ch.kind AS kind, count(ch) AS n,
               sum(CASE WHEN coalesce(ch.rolled_back, false) OR size([(i:Incident)-[:CAUSED_BY]->(ch) | i]) > 0 THEN 1 ELSE 0 END) AS bad""",
                       {"C": C, "lo": lo})
    sig_prior, kind_rate = _signature_prior(ctx, lo)
    # hierarchical (empirical-Bayes) smoothing toward the base rate of that change kind: unseen => base rate, not 0.5
    prior = {(r["c"], r["kind"]): (r["bad"] + 5 * kind_rate.get(r["kind"], 0.05)) / (r["n"] + 5) for r in hist}
    past = ctx.tg.read("""MATCH (i:Incident)-[:CAUSED_BY]->(ch:Change)-[:CHANGED]->(c:Component)
        WHERE c.id IN $C AND i.started_at < $lo RETURN c.id AS c, ch.kind AS kind, count(i) AS n""", {"C": C, "lo": lo})
    past_n = {(r["c"], r["kind"]): r["n"] for r in past}
    past_nc = {r["c"]: r["n"] for r in ctx.tg.read("""MATCH (i:Incident)-[:CAUSED_BY]->(c:Component) WHERE c.id IN $C AND i.started_at < $lo
        RETURN c.id AS c, count(i) AS n""", {"C": C, "lo": lo})}
    nA, Aset = len(A), set(A)
    feat = {}
    for r in cand:
        c = r["c"]
        E = len(r["srcs"]) / nA
        u = up.get(c, {"n_up": 0, "n_up_alerting": 0})
        prec = (u["n_up_alerting"] + (1 if c in Aset else 0)) / (u["n_up"] + 1)
        feat[c] = {"E": round(E, 3), "Prec": round(prec, 3), "Cov": round(math.sqrt(E * prec), 3), "h": r["h"], "path": r["path"],
                   "name": r["name"], "kind": r["kind"]}
    downs = defaultdict(set)
    for b in below:
        downs[b["c"]].add(b["d"])
    for c, f in feat.items():
        f["F"] = 0.0 if any(feat[d]["Cov"] >= f["Cov"] - 0.05 for d in downs.get(c, ()) if d in feat) else 1.0
    out = []
    for ch in changes:
        f = feat[ch["c"]]
        dt = t0 - ch["ts"]
        # causes precede symptoms: changes landing after detection (allowed only as clock skew) are heavily discounted
        R = math.exp(-dt / (30 * MIN)) if dt >= 0 else (0.3 * math.exp(dt / (2 * MIN)) if ch["ts"] <= hi else 0.0)
        # K blends where (component x kind track record) with what (change-signature track record, e.g. config key)
        base = kind_rate.get(ch["kind"], 0.05)
        K = min(1.0, 0.5 * prior.get((ch["c"], ch["kind"]), base) / 0.25 + 0.5 * sig_prior.get(_signature(ch["kind"], ch["summary"]), base) / 0.25)
        Hh = min(1.0, past_n.get((ch["c"], ch["kind"]), 0) / 2)
        b = {"R": round(R, 3), "Cov": f["Cov"], "F": f["F"], "K": round(K, 3), "H": round(Hh, 3), "P": round(2 / (2 + f["h"]), 3)}
        S = sum(w[k] * b[k] for k in w)
        out.append({"type": "change", "change_id": ch["id"], "change_kind": ch["kind"], "change_summary": ch["summary"],
                    "change_ts": ch["ts"], "minutes_before_detection": round(dt / MIN, 1), "author_id": ch["author"],
                    "entity_id": ch["c"], "entity_name": f["name"], "hops": f["h"], "score": round(S, 4),
                    "breakdown": b | {"E": f["E"], "Prec": f["Prec"]}, "path": f["path"]})
    out.sort(key=lambda x: -x["score"])
    nc = []
    engines = {r["id"]: r["e"] for r in ctx.tg.read("MATCH (c:Component) WHERE c.id IN $ids RETURN c.id AS id, c.engine AS e", {"ids": list(feat)})}
    for c, f in feat.items():
        Hn = min(1.0, past_nc.get(c, 0) / 2)
        b = {"Cov": f["Cov"], "F": f["F"], "S": 1.0 if c in Aset else 0.0, "H": Hn}
        S = sum(W_NC[k] * b[k] for k in W_NC)
        nc.append({"type": "non_change", "entity_id": c, "entity_name": f["name"], "engine": engines.get(c), "hops": f["h"], "score": round(S, 4),
                   "breakdown": b | {"E": f["E"], "Prec": f["Prec"]}, "path": f["path"]})
    nc.sort(key=lambda x: -x["score"])
    abstain = (not out) or out[0]["score"] < theta
    for x in out[:top_k] + nc[:3]:
        nodes = x["path"] + ([x["change_id"], x["author_id"]] if x["type"] == "change" else [])
        edges = path_edges(x["path"]) + ([edge_key(x["change_id"], "CHANGED", x["entity_id"])] if x["type"] == "change" else [])
        x["evidence_id"] = register(ctx, "incident_rank_root_causes", {"incident_id": incident_id, "candidate": x.get("change_id", x["entity_id"])},
                                    [n for n in nodes if n], edges,
                                    f"{x['type']} candidate {x.get('change_id', '')} on {x['entity_name']} score {x['score']}")
    return {"incident_id": incident_id, "t0": t0, "alerting": A, "alert_names": alert_names, "n_candidate_components": len(C),
            "n_changes_in_window": len(changes), "candidates": out[:top_k], "non_change_candidates": nc[:3], "abstain": abstain,
            "theta": theta, "weights": w}


# ------------------------------------------------------------------ 6. explain connection
def graph_explain_connection(ctx: Ctx, from_id: str, to_id: str, max_len: int = 4, k: int = 3,
                             rel_types: list[str] | None = None) -> dict:
    L = _hops(max_len, 1, 5)
    types = sorted(r for r in (rel_types or REL_ALLOW) if r in REL_ALLOW) or sorted(REL_ALLOW)
    rels = "|".join(types)

    def run(pattern: str):
        return ctx.tg.read(f"""MATCH (a:{label_of(from_id)} {{id:$a}}), (b:{label_of(to_id)} {{id:$b}}) WITH a, b
            MATCH p=allShortestPaths({pattern})
            RETURN [n IN nodes(p) | n.id] AS ids, [n IN nodes(p) | coalesce(n.name, n.key, n.title, n.summary, n.id)] AS labels,
                   [r IN relationships(p) | [startNode(r).id, type(r), endNode(r).id]] AS rels LIMIT {max(1, min(int(k), 5))}""",
                           {"a": from_id, "b": to_id})

    if types == ["DEPENDS_ON"]:  # dependency chains are directional: a calls ... calls b (or the reverse)
        rows = run(f"(a)-[:DEPENDS_ON*..{L}]->(b)") or run(f"(a)<-[:DEPENDS_ON*..{L}]-(b)")
    else:
        rows = run(f"(a)-[:{rels}*..{L}]-(b)")
    paths = []
    for r in rows:
        ev = register(ctx, "graph_explain_connection", {"from": from_id, "to": to_id}, r["ids"], [edge_key(*x) for x in r["rels"]],
                      " / ".join(f"{s}-{t}->{d}" for s, t, d in r["rels"]))
        paths.append({**r, "evidence_id": ev})
    return {"from": from_id, "to": to_id, "paths": paths}


# ------------------------------------------------------------------ 7. similar incidents
def _jac(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def _tok(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def incident_similar(ctx: Ctx, incident_id: str, phase: str = "triage", k: int = 5,
                     root_entity_id: str | None = None, root_kind: str | None = None) -> dict:
    cur = ctx.tg.read("""MATCH (i:Incident {id:$id}) RETURN i.title AS title, coalesce(i.detected_at, i.started_at) AS t0,
        [(i)-[:AFFECTED]->(x) | x.id] AS affected, [(i)<-[:PART_OF]-(a:Alert) | a.name] AS alerts,
        [(i)<-[:PART_OF]-(:Alert)-[:FIRED_ON]->(y) | y.id] AS alerting""", {"id": incident_id})
    if not cur:
        raise ValueError("unknown incident")
    cur = cur[0]
    S = list(set(cur["affected"]) | set(cur["alerting"]) | ({root_entity_id} if root_entity_id else set()))
    rows = ctx.tg.read("""MATCH (i:Incident)-[:AFFECTED]->(e:Component) WHERE e.id IN $S AND i.id <> $id AND i.started_at < $t0
        WITH DISTINCT i
        RETURN i.id AS id, i.key AS key, i.title AS title, i.started_at AS t, i.sev AS sev,
               [(i)-[:AFFECTED]->(x) | x.id] AS affected, [(i)<-[:PART_OF]-(a:Alert) | a.name] AS alerts,
               [(i)-[:CAUSED_BY]->(c:Change)-[:CHANGED]->(r) | [c.id, c.kind, r.id]] AS causes,
               [(i)-[:CAUSED_BY]->(r:Component) | r.id] AS nc_roots,
               [(i)-[:RESOLVED_BY]->(p:Person) | [p.id, p.name, p.handle, p.left_at]] AS resolvers,
               [(i)-[:MITIGATED_WITH]->(rb:Runbook) | [rb.id, rb.key]] AS runbooks""",
                       {"S": S, "id": incident_id, "t0": cur["t0"]})
    adj = set()
    if root_entity_id:
        adj = {r["x"] for r in ctx.tg.read("MATCH (c:Component {id:$c})-[:DEPENDS_ON]-(x:Component) RETURN DISTINCT x.id AS x",
                                           {"c": root_entity_id})}
    ct = _tok(cur["title"])
    vec_sim = {}
    has_vec = ctx.tg.read("MATCH (i:Incident {id:$id}) RETURN i.embedding IS NOT NULL AS v", {"id": incident_id})
    if has_vec and has_vec[0]["v"]:
        try:  # semantic neighbours (HNSW, cosine distance -> similarity); over-fetch, then post-filter by time
            vrows = ctx.tg.read("""MATCH (q:Incident {id:$id}) CALL db.idx.vector.queryNodes('Incident', 'embedding', 40, q.embedding)
                YIELD node, score WHERE node.id <> $id AND node.started_at < $t0 RETURN node.id AS id, 1 - score AS sim""",
                                {"id": incident_id, "t0": cur["t0"]})
            vec_sim = {v["id"]: max(0.0, v["sim"]) for v in vrows}
            # exact cosine for structural candidates outside the HNSW top-40
            rest = [r["id"] for r in rows if r["id"] not in vec_sim]
            if rest:
                for v in ctx.tg.read("""MATCH (q:Incident {id:$id}), (i:Incident) WHERE i.id IN $ids AND i.embedding IS NOT NULL
                        RETURN i.id AS id, 1 - vec.cosineDistance(i.embedding, q.embedding) AS sim""", {"id": incident_id, "ids": rest}):
                    vec_sim[v["id"]] = max(0.0, v["sim"])
            extra = [v for v in vec_sim if v not in {r["id"] for r in rows}][:15]
            if extra:
                rows += ctx.tg.read("""MATCH (i:Incident) WHERE i.id IN $ids
                    RETURN i.id AS id, i.key AS key, i.title AS title, i.started_at AS t, i.sev AS sev,
                           [(i)-[:AFFECTED]->(x) | x.id] AS affected, [(i)<-[:PART_OF]-(a:Alert) | a.name] AS alerts,
                           [(i)-[:CAUSED_BY]->(c:Change)-[:CHANGED]->(r) | [c.id, c.kind, r.id]] AS causes,
                           [(i)-[:CAUSED_BY]->(r:Component) | r.id] AS nc_roots,
                           [(i)-[:RESOLVED_BY]->(p:Person) | [p.id, p.name, p.handle, p.left_at]] AS resolvers,
                           [(i)-[:MITIGATED_WITH]->(rb:Runbook) | [rb.id, rb.key]] AS runbooks""", {"ids": extra})
        except Exception:
            vec_sim = {}
    out = []
    for r in rows:
        roots = [c[2] for c in r["causes"]] + r["nc_roots"]
        kinds = [c[1] for c in r["causes"]]
        js, jb = _jac(cur["alerts"], r["alerts"]), _jac(S, r["affected"])
        tx = vec_sim[r["id"]] if r["id"] in vec_sim else _jac(ct, _tok(r["title"]))  # semantic when embedded, else token overlap
        if phase == "post" and root_entity_id:
            s_root = 1.0 if root_entity_id in roots else (0.5 if adj & set(roots) else 0.0)
            s_kind = 1.0 if root_kind and root_kind in kinds else 0.0
            sim = 0.35 * s_root + 0.20 * s_kind + 0.15 * js + 0.15 * jb + 0.15 * tx
        else:
            s_root = s_kind = None
            sim = (0.15 * js + 0.15 * jb + 0.15 * tx) / 0.45
        days = (cur["t0"] - r["t"]) / DAY
        sim = 0.9 * sim + 0.05 * (0.99 ** days) + (0.05 if r["runbooks"] else 0)
        out.append({"incident_id": r["id"], "key": r["key"], "title": r["title"], "sev": r["sev"], "started_at": r["t"],
                    "similarity": round(sim, 3), "parts": {"root": s_root, "kind": s_kind, "symptoms": round(js, 2),
                                                           "blast": round(jb, 2), "text": round(tx, 2), "text_mode": "vector" if vec_sim else "tokens"},
                    "root_cause": [{"change_id": c[0], "kind": c[1], "entity_id": c[2]} for c in r["causes"]] +
                                  [{"entity_id": x} for x in r["nc_roots"]],
                    "fixed_by": [{"id": p[0], "name": p[1], "handle": p[2], "active": p[3] is None or p[3] > ctx.now} for p in r["resolvers"]],
                    "runbooks": [{"id": rb[0], "key": rb[1]} for rb in r["runbooks"]],
                    "shared": sorted(set(S) & set(r["affected"]))[:5]})
    out.sort(key=lambda x: -x["similarity"])
    out = out[:max(1, min(int(k), 10))]
    for x in out:
        nodes = [incident_id, x["incident_id"]] + x["shared"] + [p["id"] for p in x["fixed_by"]] + [rb["id"] for rb in x["runbooks"]]
        edges = [edge_key(x["incident_id"], "AFFECTED", s) for s in x["shared"]] + \
                [edge_key(x["incident_id"], "RESOLVED_BY", p["id"]) for p in x["fixed_by"]] + \
                [edge_key(x["incident_id"], "MITIGATED_WITH", rb["id"]) for rb in x["runbooks"]] + \
                [edge_key(x["incident_id"], "CAUSED_BY", c["change_id"]) for c in x["root_cause"] if c.get("change_id")]
        x["evidence_id"] = register(ctx, "incident_similar", {"incident_id": incident_id, "match": x["incident_id"]}, nodes, edges,
                                    f"{x['key']} similar ({x['similarity']}) fixed by {[p['name'] for p in x['fixed_by']]}")
    return {"incident_id": incident_id, "phase": phase, "similar": out}


# ------------------------------------------------------------------ 8. ownership (as-of)
def org_ownership(ctx: Ctx, entity_id: str, at: int | None = None) -> dict:
    at = at or ctx.now
    own = ctx.tg.read(f"""MATCH (t:Team)-[o:OWNS]->(e:Component {{id:$id}}) WHERE {VALID('o')}
        RETURN t.id AS team_id, t.name AS team, o.valid_from AS since, o.source_id AS source""", {"id": entity_id, "at": at})
    history = ctx.tg.read("""MATCH (t:Team)-[o:OWNS]->(e:Component {id:$id})
        RETURN t.name AS team, o.valid_from AS valid_from, o.valid_to AS valid_to, o.source_id AS source ORDER BY o.valid_from""",
                          {"id": entity_id})
    if not own:
        return {"entity_id": entity_id, "owner": None, "history": history}
    tid = own[0]["team_id"]
    shifts = ctx.tg.read("""MATCH (p:Person)-[:HAS_SHIFT]->(s:OnCallShift)-[:COVERS]->(t:Team {id:$tid})
        WHERE s.start <= $at AND s.end > $at
        RETURN p.id AS id, p.name AS name, p.handle AS handle, s.level AS level, s.id AS shift,
               (p.left_at IS NULL OR p.left_at > $at) AS active""", {"tid": tid, "at": at})
    oncall = [s for s in sorted(shifts, key=lambda s: s["level"] != "primary") if s["active"]]
    em = ctx.tg.read(f"""MATCH (p:Person {{role:'em'}})-[m:MEMBER_OF]->(t:Team {{id:$tid}}) WHERE {VALID('m')}
        RETURN p.id AS id, p.name AS name, p.handle AS handle LIMIT 1""", {"tid": tid, "at": at})
    nodes = [entity_id, tid] + [s["id"] for s in oncall] + [s["shift"] for s in oncall]
    edges = [edge_key(tid, "OWNS", entity_id)] + [edge_key(s["id"], "HAS_SHIFT", s["shift"]) for s in oncall] + \
            [edge_key(s["shift"], "COVERS", tid) for s in oncall]
    ev = register(ctx, "org_ownership", {"entity_id": entity_id, "at": at}, nodes, edges,
                  f"{entity_id} owned by {own[0]['team']}; on-call {[s['name'] for s in oncall]}")
    return {"entity_id": entity_id, "at": at, "owner": own[0], "oncall": oncall, "escalation": em, "history": history, "evidence_id": ev}


# ------------------------------------------------------------------ 9. experts (KNOWS on demand)
EXP_W = {"fixer": 5, "commander": 1.5, "helper": 1.5, "postmortem": 4, "design": 4, "wiki": 2, "runbook": 3, "ticket": 3,
         "change": 2, "review": 1.5, "answer": 1}
HALF = {"design": 365}


def org_experts(ctx: Ctx, entity_id: str, k: int = 5) -> dict:
    at = ctx.now
    p = {"id": entity_id, "at": at}
    q = ctx.tg.read
    sig = []
    sig += [(r["p"], r["role"], r["t"], r["src"]) for r in q("""MATCH (p:Person)-[r:RESPONDED]->(i:Incident)-[:AFFECTED]->(:Component {id:$id})
        WHERE i.started_at <= $at RETURN p.id AS p, r.role AS role, i.started_at AS t, i.id AS src""", p)]
    sig += [(r["p"], "change", r["t"], r["src"]) for r in q("""MATCH (p:Person)-[:AUTHORED]->(c:Change)-[:CHANGED]->(:Component {id:$id})
        WHERE c.ts <= $at RETURN p.id AS p, c.ts AS t, c.id AS src""", p)]
    sig += [(r["p"], "review", r["t"], r["src"]) for r in q("""MATCH (p:Person)-[:REVIEWED]->(c:Change)-[:CHANGED]->(:Component {id:$id})
        WHERE c.ts <= $at RETURN p.id AS p, c.ts AS t, c.id AS src""", p)]
    sig += [(r["p"], "ticket", r["t"], r["src"]) for r in q("""MATCH (t:Ticket)-[:ABOUT]->(:Component {id:$id}), (t)-[:RESOLVED_BY]->(p:Person)
        WHERE t.resolved_at <= $at RETURN p.id AS p, t.resolved_at AS t, t.id AS src""", p)]
    sig += [(r["p"], r["kind"], r["t"], r["src"]) for r in q("""MATCH (p:Person)-[:WROTE]->(d:Doc)-[:ABOUT]->(:Component {id:$id})
        WHERE d.created_at <= $at RETURN p.id AS p, d.kind AS kind, d.created_at AS t, d.id AS src""", p)]
    sig += [(r["p"], "runbook", r["t"], r["src"]) for r in q("""MATCH (p:Person)-[:WROTE]->(rb:Runbook)-[:MITIGATES]->(:Component {id:$id})
        RETURN p.id AS p, rb.last_verified_at AS t, rb.id AS src""", p)]
    sig += [(r["p"], "answer", r["t"], r["src"]) for r in q("""MATCH (p:Person)-[:SENT]->(m:Message)-[:IN]->(th:Thread)-[:MENTIONS]->(:Component {id:$id})
        WHERE th.kind = 'help' AND m.ts <= $at RETURN DISTINCT p.id AS p, th.started_at AS t, th.id AS src""", p)]
    people = {r["id"]: r for r in q("MATCH (p:Person) RETURN p.id AS id, p.name AS name, p.handle AS handle, p.left_at AS left_at, p.role AS role")}
    score, ev_by = defaultdict(float), defaultdict(list)
    for pid, typ, t, src in sig:
        if typ not in EXP_W or t is None:
            continue
        hl = HALF.get(typ, 180) * DAY
        v = EXP_W[typ] * 2 ** (-max(0, at - t) / hl)
        score[pid] += v
        ev_by[pid].append((v, typ, src))
    rows = []
    for pid, s in score.items():
        per = people.get(pid, {})
        active = per.get("left_at") is None or per["left_at"] > at
        top = sorted(ev_by[pid], reverse=True)[:3]
        rows.append({"id": pid, "name": per.get("name"), "handle": per.get("handle"), "active": active, "left_at": per.get("left_at"),
                     "score": round(s, 2), "top_evidence": [{"type": t, "source_id": src, "weight": round(v, 2)} for v, t, src in top]})
    rows.sort(key=lambda r: -r["score"])
    act = [r for r in rows if r["active"]]
    mx = act[0]["score"] if act else 1
    bus = sum(1 for r in act if r["score"] / mx >= 0.75) if act else 0
    departed = [r for r in rows if not r["active"]][:3]
    risk = bool(departed and act and departed[0]["score"] > act[0]["score"])
    exp = act[:max(1, min(int(k), 10))]
    nodes = [entity_id] + [r["id"] for r in exp + departed] + [e["source_id"] for r in exp for e in r["top_evidence"]]
    ev = register(ctx, "org_experts", {"entity_id": entity_id}, nodes, [], f"experts for {entity_id}: {[r['name'] for r in exp[:3]]}; bus factor {bus}")
    return {"entity_id": entity_id, "experts": exp, "bus_factor": bus, "departed_experts": departed,
            "knowledge_at_risk": risk, "evidence_id": ev}


# ------------------------------------------------------------------ 10. knowledge for entity (+ staleness)
def knowledge_for_entity(ctx: Ctx, node_id: str, kinds: list[str] | None = None, k: int = 3) -> dict:
    at = ctx.now
    kinds = kinds or ["Runbook", "Doc", "Decision", "Lesson"]
    out = {"node_id": node_id}
    last = ctx.tg.read("MATCH (c:Change)-[:CHANGED]->(:Component {id:$id}) WHERE c.ts <= $at RETURN max(c.ts) AS t", {"id": node_id, "at": at})
    last_change = (last[0]["t"] if last else None) or 0
    removed = _removed_deps(ctx, node_id, at)
    nodes, edges = [node_id], []
    if "Runbook" in kinds:
        rbs = ctx.tg.read("""MATCH (rb:Runbook)-[:MITIGATES]->(:Component {id:$id}) OPTIONAL MATCH (a:Person)-[:WROTE]->(rb)
            RETURN rb.id AS id, rb.key AS key, rb.title AS title, rb.status AS status, rb.version AS version,
                   rb.last_verified_at AS last_verified_at, rb.mentions AS mentions, rb.text AS text,
                   coalesce(a.name, '') AS author, a.left_at AS author_left_at ORDER BY rb.status, rb.key""", {"id": node_id})
        for r in rbs:
            why = []
            if r["status"] == "active" and r["last_verified_at"] and r["last_verified_at"] < last_change - 90 * DAY:
                why.append("not verified since recent changes")
            if r["author_left_at"] and r["author_left_at"] <= at:
                why.append("author has left the company")
            if removed & set(r["mentions"] or []):
                why.append(f"mentions removed dependency {sorted(removed & set(r['mentions']))}")
            r["stale"], r["stale_reasons"] = bool(why), why
            r.pop("author_left_at")
            nodes.append(r["id"])
            edges.append(edge_key(r["id"], "MITIGATES", node_id))
        out["runbooks"] = rbs
    if "Doc" in kinds:
        docs = ctx.tg.read("""MATCH (d:Doc)-[:ABOUT]->(:Component {id:$id}) WHERE d.created_at <= $at OPTIONAL MATCH (a:Person)-[:WROTE]->(d)
            RETURN d.id AS id, d.kind AS kind, d.title AS title, d.created_at AS created_at, coalesce(a.name,'') AS author,
                   a.left_at AS author_left_at, substring(d.text, 0, 400) AS snippet ORDER BY d.created_at DESC LIMIT $k""",
                           {"id": node_id, "at": at, "k": max(1, min(int(k) * 2, 12))})
        for d in docs:
            left = d.pop("author_left_at")
            d["author_left"] = bool(left and left <= at)
            nodes.append(d["id"])
            edges.append(edge_key(d["id"], "ABOUT", node_id))
        out["docs"] = docs
    if "Decision" in kinds:
        decs = ctx.tg.read("""MATCH (dec:Decision)-[:AFFECTS]->(:Component {id:$id}) RETURN dec.id AS id, dec.key AS key, dec.title AS title,
            dec.status AS status, dec.decided_at AS decided_at ORDER BY dec.decided_at DESC LIMIT $k""", {"id": node_id, "k": max(1, int(k))})
        out["decisions"] = decs
        nodes += [d["id"] for d in decs]
        edges += [edge_key(d["id"], "AFFECTS", node_id) for d in decs]
    if "Lesson" in kinds:
        out["lessons"] = ctx.tg.read("""MATCH (l:Lesson)-[:ABOUT]->(:Component {id:$id}) WHERE l.votes > 0
            RETURN l.id AS id, l.text AS text, l.votes AS votes ORDER BY l.votes DESC LIMIT $k""", {"id": node_id, "k": max(1, int(k))})
    out["evidence_id"] = register(ctx, "knowledge_for_entity", {"node_id": node_id}, nodes, edges, f"knowledge for {node_id}")
    return out


def _removed_deps(ctx: Ctx, node_id: str, at: int) -> set:
    rows = ctx.tg.read("""MATCH (:Component {id:$id})-[r:DEPENDS_ON]->(x:Component)
        RETURN x.name AS n, sum(CASE WHEN r.valid_to IS NULL OR r.valid_to > $at THEN 1 ELSE 0 END) AS live""",
                       {"id": node_id, "at": at})
    return {r["n"] for r in rows if r["live"] == 0}


# ------------------------------------------------------------------ 11. knowledge search
def knowledge_search(ctx: Ctx, query: str, kinds: list[str] | None = None, k: int = 8) -> dict:
    q = _ft(query)
    kinds = [x for x in (kinds or ["Doc", "Ticket", "Runbook", "Message"]) if x in ("Doc", "Ticket", "Runbook", "Message")]
    hits = []
    for lbl in kinds:
        try:
            rows = ctx.tg.read(f"""CALL db.idx.fulltext.queryNodes('{lbl}', $q) YIELD node, score
                RETURN node.id AS id, '{lbl}' AS kind, coalesce(node.title, node.key, '') AS title,
                       substring(node.text, 0, 300) AS snippet, score ORDER BY score DESC LIMIT 6""", {"q": q})
            hits += rows
        except Exception:
            continue
    top_ft = max((x["score"] for x in hits), default=0) or 1  # normalise full-text scores to 0..1 (max computed once)
    for h in hits:
        h["score"] = h["score"] / top_ft
        h["via"] = "fulltext"
    ft_hits, hits = hits, []
    try:
        from ..settings import settings as _s
        if _s.llm_enabled:
            from .embed import embed_query
            qv = embed_query(query)
            for lbl in [x for x in kinds if x != "Message"] + ["Thread"]:
                rows = ctx.tg.read(f"""CALL db.idx.vector.queryNodes('{lbl}', 'embedding', 6, vecf32($v)) YIELD node, score
                    RETURN node.id AS id, '{lbl}' AS kind, coalesce(node.title, node.key, '') AS title,
                           substring(coalesce(node.text, ''), 0, 300) AS snippet, 1 - score AS score""", {"v": qv})
                for r_ in rows:
                    r_["via"] = "vector"
                hits += rows
    except Exception:
        pass
    fused: dict = {}  # reciprocal rank fusion (k=60) of the full-text and vector rankings
    for ranking in (sorted(ft_hits, key=lambda h: -h["score"]), sorted(hits, key=lambda h: -h["score"])):
        for rank, h in enumerate(ranking):
            e = fused.setdefault(h["id"], {**h, "rrf": 0.0, "vias": set()})
            e["rrf"] += 1.0 / (60 + rank)
            e["vias"].add(h["via"])
    top = max((e["rrf"] for e in fused.values()), default=1.0)
    for e in fused.values():
        e["score"] = round(e["rrf"] / top, 3)
        e["via"] = "hybrid" if len(e.pop("vias")) > 1 else e["via"]
        e.pop("rrf")
    hits = sorted(fused.values(), key=lambda h: -h["score"])[:max(1, min(int(k), 15))]
    ev = register(ctx, "knowledge_search", {"query": query}, [h["id"] for h in hits], [], f"search '{query}': {len(hits)} hits")
    return {"query": query, "hits": hits, "evidence_id": ev}


# ------------------------------------------------------------------ 12. decision trace
def decision_trace(ctx: Ctx, decision: str) -> dict:
    did = decision
    if not decision.startswith("dec_"):
        r = ctx.tg.read("MATCH (d:Decision) WHERE toUpper(d.key) = toUpper($q) RETURN d.id AS id", {"q": decision})
        if not r and re.search(r"\bADR-\d+\b", decision, re.I):
            return {"decision": None, "note": f"no decision record {decision.upper()}"}  # an explicit key that doesn't exist
        if not r:
            # free text: broad (OR) full-text retrieval, then accept a decision only if its title shares at least two
            # content words with the question (otherwise "why did we pick the lunch menu?" would match some ADR)
            words = [w.lower() for w in _ft(decision).split(" | ") if len(w) > 3]
            cand = ctx.tg.read("CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score MATCH (d:Decision)-[:DOCUMENTED_IN]->(node) "
                               "RETURN d.id AS id, toLower(d.title) AS title, max(score) AS score ORDER BY score DESC LIMIT 15",
                               {"q": " | ".join(words)}) if words else []
            ws = set(words)
            scored = sorted(((sum(w in c["title"] for w in ws), i, c) for i, c in enumerate(cand)), key=lambda x: (-x[0], x[1]))
            r = [c for n, _, c in scored if n >= min(2, len(ws))][:1]  # most title words in common, then full-text rank
        if not r:
            return {"decision": None, "note": "no matching decision"}
        did = r[0]["id"]
    row = ctx.tg.read("""MATCH (d:Decision {id:$id})
        RETURN d.id AS id, d.key AS key, d.title AS title, d.status AS status, d.decided_at AS decided_at,
               [(d)-[:DOCUMENTED_IN]->(doc:Doc) | {id: doc.id, title: doc.title, snippet: substring(doc.text, 0, 600)}] AS docs,
               [(d)-[r:DISCUSSED_IN]->(t:Thread) | {id: t.id, channel: t.channel, source: r.source_id}] AS threads,
               [(d)-[:DECIDED_BY]->(p:Person) | {id: p.id, name: p.name}] AS decided_by,
               [(d)-[:SUPERSEDES]->(o:Decision) | {id: o.id, key: o.key, title: o.title}] AS supersedes,
               [(n:Decision)-[:SUPERSEDES]->(d) | {id: n.id, key: n.key, title: n.title}] AS superseded_by,
               [(d)-[:AFFECTS]->(c:Component) | {id: c.id, name: c.name}] AS affects""", {"id": did})
    if not row:
        return {"decision": None}
    d = row[0]
    nodes = [did] + [x["id"] for k_ in ("docs", "threads", "decided_by", "supersedes", "superseded_by", "affects") for x in d[k_]]
    edges = [edge_key(did, "DOCUMENTED_IN", x["id"]) for x in d["docs"]] + [edge_key(did, "DISCUSSED_IN", x["id"]) for x in d["threads"]] + \
            [edge_key(did, "DECIDED_BY", x["id"]) for x in d["decided_by"]] + [edge_key(did, "SUPERSEDES", x["id"]) for x in d["supersedes"]] + \
            [edge_key(x["id"], "SUPERSEDES", did) for x in d["superseded_by"]] + [edge_key(did, "AFFECTS", x["id"]) for x in d["affects"]]
    d["evidence_id"] = register(ctx, "decision_trace", {"decision": did}, nodes, edges, f"trace of {d['key']}")
    return {"decision": d}


# ------------------------------------------------------------------ 13. hubs (importance, NOT cause)
def graph_hubs(ctx: Ctx, metric: str = "pagerank", k: int = 10, of: list[str] | None = None) -> dict:
    """FalkorDB built-in algorithms over the dependency graph. PageRank = how much of the system leans on a
    component; betweenness = how many dependency paths run through it (a chokepoint). With `of`, also returns
    the rank of those components (Triage uses this to say "catalog is dependency hub #3 of 55").
    Note: algo.* procedures see every DEPENDS_ON edge, including ones closed via valid_to."""
    k = max(1, min(int(k), 20))
    if metric == "betweenness":
        rows = ctx.tg.read("""CALL algo.betweenness({nodeLabels:['Component'], relationshipTypes:['DEPENDS_ON'], samplingSize:64, samplingSeed:42})
            YIELD node, score RETURN node.id AS id, node.name AS name, score ORDER BY score DESC, id""")
    else:
        rows = ctx.tg.read("""CALL algo.pageRank('Component', 'DEPENDS_ON') YIELD node, score
            RETURN node.id AS id, node.name AS name, score ORDER BY score DESC, id""")
        metric = "pagerank"
    for i, r in enumerate(rows):
        r["rank"], r["score"] = i + 1, round(r["score"], 5)
    out = {"metric": metric, "n_components": len(rows), "note": "importance / blast-radius weight, not root-cause likelihood", "hubs": rows[:k]}
    if of:
        want = set(of)
        sel = [r for r in rows if r["id"] in want]
        out["ranked"] = sel
        out["evidence_id"] = register(ctx, "graph_hubs", {"metric": metric, "of": sorted(want)}, [r["id"] for r in sel], [],
                                      f"{metric} rank of {len(rows)}: " + ", ".join(f"{r['name']} #{r['rank']}" for r in sel[:6]))
    return out


# ------------------------------------------------------------------ 14. guarded read-only Cypher
WRITE_RE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD|FOREACH)\b", re.I)


def graph_cypher_readonly(ctx: Ctx, cypher: str, params: dict | None = None, purpose: str = "") -> dict:
    cypher = re.sub(r"/\*.*?\*/|//[^\n]*", " ", cypher, flags=re.S)  # comments could hide clauses from the checks below
    if WRITE_RE.search(cypher):
        return {"error": "read-only: write clauses are not allowed"}
    if re.search(r"\*\s*\]|\*\s*\d*\s*\.\.\s*\]", cypher):
        return {"error": "unbounded variable-length path: use *1..N with N <= 5"}
    for m in re.finditer(r"\*\s*(?:\d*\s*\.\.\s*)?(\d+)\s*\]", cypher):
        if int(m.group(1)) > 5:
            return {"error": "variable-length bound must be <= 5"}
    for m in re.finditer(r"\bCALL\s+([\w.]+)", cypher, re.I):  # every procedure, not just the first
        if not re.match(r"(db\.idx\.fulltext\.queryNodes|db\.idx\.vector\.queryNodes|algo\.)", m.group(1), re.I):
            return {"error": f"procedure {m.group(1)} is not allowed (only full-text, vector and algo.* procedures)"}
    q = cypher if re.search(r"\bLIMIT\b", cypher, re.I) else cypher.rstrip().rstrip(";") + " LIMIT 50"
    try:
        ctx.tg.g.explain(q, params or {})
        rows = ctx.tg.read(q, params or {}, timeout=3000)[:50]
    except Exception as e:
        return {"error": f"query failed: {str(e)[:200]}"}
    ids = [v for r in rows for v in r.values() if isinstance(v, str) and "_" in v and v.split("_")[0] + "_" in PREFIX_LABEL]
    ev = register(ctx, "graph_cypher_readonly", {"cypher": q, "purpose": purpose}, ids, [], f"ad-hoc query ({purpose}): {len(rows)} rows")
    return {"rows": rows, "cypher": q, "ad_hoc": True, "evidence_id": ev}


# ------------------------------------------------------------------ 15-17. memory writes / recall
def memory_note(ctx: Ctx, incident_id: str, kind: str, claim: str, evidence_ids: list[str], confidence: float = 0.5,
                about_id: str | None = None) -> dict:
    if kind not in ("hypothesis", "finding", "ruled_out"):
        return {"error": "kind must be hypothesis|finding|ruled_out"}
    known = {r["id"] for r in ctx.tg.read("MATCH (e:Evidence) WHERE e.id IN $ids AND e.incident_id = $inc RETURN e.id AS id",
                                          {"ids": evidence_ids, "inc": incident_id})}
    unknown = [e for e in evidence_ids if e not in known]
    if unknown or not evidence_ids:
        return {"error": f"rejected: unknown or foreign evidence ids {unknown}" if unknown else "rejected: cite at least one evidence id"}
    hid = "hyp_" + uuid.uuid4().hex[:10]
    status = {"hypothesis": "proposed", "finding": "supported", "ruled_out": "refuted"}[kind]
    ctx.tg.write("""MATCH (i:Incident {id:$inc}) CREATE (h:Hypothesis {id:$id, kind:$kind, claim:$claim, status:$status,
        confidence:$conf, about_id:$about, agent:$agent, ts:$ts, mem:'episodic'})<-[:HAS_HYPOTHESIS]-(i)
        WITH h UNWIND $ev AS eid MATCH (e:Evidence {id:eid}) CREATE (h)-[:SUPPORTED_BY]->(e)""",
                 {"inc": incident_id, "id": hid, "kind": kind, "claim": claim[:600], "status": status, "conf": float(confidence),
                  "about": about_id, "agent": ctx.agent, "ts": ctx.now, "ev": evidence_ids})
    return {"hypothesis_id": hid, "status": status}


def memory_handoff(ctx: Ctx, incident_id: str, to_role: str, summary: str, open_questions: list[str], evidence_ids: list[str]) -> dict:
    if to_role not in ("triage", "investigator", "responder", "scribe"):
        return {"error": "to_role must be triage|investigator|responder|scribe"}
    hid = "hand_" + uuid.uuid4().hex[:10]
    rows = ctx.tg.write("""MATCH (i:Incident {id:$inc}) MERGE (fa:Agent {id:$from}) ON CREATE SET fa.name = $fromn
        MERGE (ta:Agent {id:$to}) ON CREATE SET ta.name = $ton
        WITH i, fa, ta
        CREATE (h:Handoff {id:$id, from_agent:$fromn, to_agent:$ton, summary:$s, open_questions:$oq, verification_status:'unverified',
                ts:$ts, mem:'episodic'})
        CREATE (h)-[:FOR]->(i), (h)-[:FROM]->(fa), (h)-[:TO]->(ta)
        WITH h OPTIONAL MATCH (e:Evidence) WHERE e.id IN $ev
        FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | CREATE (h)-[:CARRIES]->(e))
        RETURN DISTINCT h.id AS id""",
                 {"inc": incident_id, "from": f"agent_{ctx.agent}", "to": f"agent_{to_role}", "id": hid, "fromn": ctx.agent,
                  "ton": to_role, "s": summary[:1000], "oq": open_questions[:10], "ts": ctx.now, "ev": evidence_ids})
    if not rows:
        return {"error": "unknown incident"}
    return {"handoff_id": hid}


def memory_inbox(ctx: Ctx, incident_id: str, role: str | None = None) -> dict:
    """Read the latest handoff addressed to this agent from the graph (shared state between agents) and mark it accepted.
    The evidence it carries can be cited directly by the receiving agent."""
    role = role or ctx.agent
    rows = ctx.tg.read("""MATCH (h:Handoff {to_agent:$role})-[:FOR]->(:Incident {id:$inc})
        OPTIONAL MATCH (h)-[:CARRIES]->(e:Evidence)
        WITH h, collect(e.id) AS ev ORDER BY h.ts DESC, h.id DESC LIMIT 1
        RETURN h.id AS id, h.from_agent AS from_agent, h.summary AS summary, h.open_questions AS open_questions, ev""",
                       {"role": role, "inc": incident_id})
    if not rows:
        return {"handoff": None}
    h = rows[0]
    ctx.tg.write("MATCH (h:Handoff {id:$id}) SET h.verification_status = 'accepted', h.accepted_by = $agent, h.accepted_at = $now",
                 {"id": h["id"], "agent": ctx.agent, "now": ctx.now})
    return {"handoff": {"id": h["id"], "from": h["from_agent"], "summary": h["summary"], "open_questions": h["open_questions"] or [],
                        "evidence_ids": [e for e in h["ev"] if e]}}


def memory_recall(ctx: Ctx, entity_id: str | None = None, query: str | None = None, k: int = 5) -> dict:
    rows = []
    if entity_id:
        rows += ctx.tg.read("""MATCH (l:Lesson)-[:ABOUT]->(:Component {id:$id}) RETURN l.id AS id, 'Lesson' AS kind, l.text AS text,
            l.votes AS votes, l.ts AS ts ORDER BY l.votes DESC LIMIT $k""", {"id": entity_id, "k": k})
        rows += ctx.tg.read("""MATCH (r:Reflection)-[:ABOUT]->(:Component {id:$id}) RETURN r.id AS id, 'Reflection' AS kind, r.text AS text,
            0 AS votes, r.ts AS ts ORDER BY r.ts DESC LIMIT $k""", {"id": entity_id, "k": k})
    return {"memories": [r for r in rows if r["kind"] != "Lesson" or (r["votes"] or 0) > 0][:k]}


# ------------------------------------------------------------------ 18-20. gated actions
def _gate(ctx: Ctx, incident_id: str) -> dict:
    r = ctx.tg.read("""MATCH (i:Incident {id:$id}) RETURN i.sev AS sev, coalesce(i.gate_score, 0.0) AS g,
        coalesce(i.verified_path, false) AS vp""", {"id": incident_id})
    return r[0] if r else {"sev": 5, "g": 0.0, "vp": False}


def _action(ctx: Ctx, incident_id: str, kind: str, target: str, tier: str, status: str, payload: dict, evidence_ids: list[str]) -> dict:
    idem = f"{incident_id}:{kind}:{target}"
    rows = ctx.tg.write("""MATCH (i:Incident {id:$inc})
        MERGE (a:Action {idem_key:$idem})
        ON CREATE SET a.id = $id, a.kind = $kind, a.target = $target, a.tier = $tier, a.status = $status, a.payload = $payload,
                      a.created_at = $ts, a.agent = $agent, a.mem = 'episodic', a.created = true
        ON MATCH SET a.created = false
        MERGE (a)-[:FOR]->(i)
        WITH a UNWIND (CASE WHEN size($ev) = 0 THEN [null] ELSE $ev END) AS eid
        OPTIONAL MATCH (e:Evidence {id:eid}) FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (a)-[:JUSTIFIED_BY]->(e))
        RETURN DISTINCT a.id AS id, a.status AS status, a.created AS created""",
                        {"inc": incident_id, "idem": idem, "id": "act_" + uuid.uuid4().hex[:10], "kind": kind, "target": target,
                         "tier": tier, "status": status, "payload": json.dumps(payload), "ts": ctx.now, "agent": ctx.agent,
                         "ev": evidence_ids})
    r = rows[0]
    return {"action_id": r["id"], "status": r["status"], "duplicate": not r["created"], "idem_key": idem}


def action_create_ticket(ctx: Ctx, incident_id: str, team_id: str, title: str, body: str, evidence_ids: list[str]) -> dict:
    g = _gate(ctx, incident_id)
    status = "executed" if (g["g"] >= 0.6 and g["vp"]) else "draft"
    return _action(ctx, incident_id, "ticket", team_id, "T1", status, {"title": title, "body": body}, evidence_ids)


def action_page_oncall(ctx: Ctx, incident_id: str, person_id: str, reason: str, evidence_ids: list[str]) -> dict:
    g = _gate(ctx, incident_id)
    status = "executed" if (g["sev"] == 1 and g["g"] >= 0.8 and g["vp"]) else "awaiting_approval"
    return _action(ctx, incident_id, "page", person_id, "T2", status, {"reason": reason}, evidence_ids)


def action_propose_remediation(ctx: Ctx, incident_id: str, kind: str, target_id: str, rationale: str, evidence_ids: list[str]) -> dict:
    return _action(ctx, incident_id, f"remediation:{kind}", target_id, "T3", "proposed", {"rationale": rationale}, evidence_ids)


TOOLS = {f.__name__: f for f in [graph_resolve, graph_schema, graph_neighbors, incident_blast_radius, incident_rank_root_causes,
                                  graph_explain_connection, incident_similar, org_ownership, org_experts, knowledge_for_entity,
                                  knowledge_search, decision_trace, graph_hubs, graph_cypher_readonly, memory_note, memory_handoff,
                                  memory_inbox, memory_recall, action_create_ticket, action_page_oncall, action_propose_remediation]}
ROLE_TOOLS = {
    "triage": ["graph_resolve", "incident_blast_radius", "incident_similar", "org_ownership", "graph_hubs", "memory_note", "memory_handoff"],
    "investigator": ["memory_inbox", "graph_resolve", "graph_neighbors", "incident_rank_root_causes", "graph_explain_connection",
                     "incident_similar", "knowledge_for_entity", "graph_cypher_readonly", "memory_note", "memory_handoff"],
    "responder": ["memory_inbox", "org_ownership", "org_experts", "knowledge_for_entity", "memory_recall", "memory_note",
                  "action_create_ticket", "action_page_oncall", "action_propose_remediation"],
    "brain": ["graph_resolve", "graph_neighbors", "incident_blast_radius", "org_ownership", "org_experts", "knowledge_for_entity",
              "knowledge_search", "decision_trace", "graph_cypher_readonly"],
}
