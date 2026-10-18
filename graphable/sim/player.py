"""Scenario player + intake (ARCHITECTURE §4.7, §8.3).

play(): writes a scenario's events (changes, alerts, messages) into a tenant graph.
intake(): graph-based alert correlation -> one Incident (+ AFFECTED, PART_OF) and a triage Task.
fresh_tenant(): GRAPH.COPY of the golden snapshot so every scenario starts from the same history.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..core.db import TenantGraph, client
from ..ingest.pipeline import ingest_message
from ..settings import settings

MIN = 60_000


def fresh_tenant(src_tenant: str, new_tenant: str) -> TenantGraph:
    c = client()
    name = f"t_{new_tenant}"
    if name in c.list_graphs():
        c.select_graph(name).delete()
    c.select_graph(f"golden_{src_tenant}").copy(name)
    return TenantGraph(new_tenant)


def drop_tenant(tenant: str):
    c = client()
    if f"t_{tenant}" in c.list_graphs():
        c.select_graph(f"t_{tenant}").delete()


def load_scenario(tenant: str, kind: str, sid: str) -> dict:
    return json.loads((settings.data_dir / tenant / "scenarios" / kind / f"{sid}.json").read_text())


def play(tg: TenantGraph, scenario: dict, upto: int | None = None, run_tag: str | None = None) -> dict:
    """run_tag makes alert ids unique per play, so a scenario can be replayed in the same graph (the earlier play's
    alerts already belong to an incident). Changes and messages keep their ids: they are history, not events."""
    n = {"change": 0, "alert": 0, "message": 0}
    for ev in scenario["events"]:
        if upto is not None and ev["ts"] > upto:
            continue
        d = ev["data"]
        if ev["kind"] == "change":
            tg.write("""MATCH (e:Component {id:$e}), (a:Person {id:$a})
                MERGE (ch:Change {id:$id}) ON CREATE SET ch.kind=$kind, ch.ts=$ts, ch.summary=$summary, ch.canary=$canary,
                    ch.rollout_pct=$rp, ch.pr_number=$pr, ch.rolled_back=false, ch.mem='episodic'
                MERGE (ch)-[:CHANGED]->(e) MERGE (a)-[:AUTHORED]->(ch)
                WITH ch UNWIND $rev AS rid MATCH (rv:Person {id:rid}) MERGE (rv)-[:REVIEWED]->(ch)""",
                     {"e": d["entity_id"], "a": d["author_id"], "id": d["id"], "kind": d["kind"], "ts": d["ts"], "summary": d["summary"],
                      "canary": d.get("canary", False), "rp": d.get("rollout_pct", 100), "pr": d.get("pr_number"), "rev": d.get("reviewer_ids", [])})
        elif ev["kind"] == "alert":
            tg.write("""MATCH (e:Component {id:$e}) MERGE (a:Alert {id:$id}) ON CREATE SET a.name=$name, a.metric=$metric,
                a.severity=$sev, a.ts=$ts, a.value=$v, a.threshold=$th, a.mem='episodic' MERGE (a)-[:FIRED_ON]->(e)""",
                     {"e": d["entity_id"], "id": f"{d['id']}_{run_tag}" if run_tag else d["id"], "name": d["name"], "metric": d["metric"],
                      "sev": d["severity"], "ts": d["ts"],
                      "v": d["value"], "th": d["threshold"]})
        elif ev["kind"] == "message":
            ingest_message(tg.tenant, d, d.get("channel", "#announcements"))
        n[ev["kind"]] += 1
    return n


def intake(tg: TenantGraph, t0: int, incident_id: str, key: str, window_min: int = 20) -> dict | None:
    """Correlate unassigned alerts near t0: alerts within the window whose components are within 2 DEPENDS_ON hops
    (either direction) of each other form one incident. The largest cluster wins."""
    alerts = tg.read("""MATCH (a:Alert)-[:FIRED_ON]->(e:Component) WHERE a.ts >= $lo AND a.ts <= $hi AND NOT (a)-[:PART_OF]->(:Incident)
        RETURN a.id AS id, a.name AS name, a.ts AS ts, e.id AS e, e.name AS en, e.tier AS tier""",
                     {"lo": t0 - window_min * MIN, "hi": t0 + window_min * MIN})
    if not alerts:
        return None
    E = sorted({a["e"] for a in alerts})
    # Correlate through direct dependencies, or 2-hop paths whose middle node is NOT a hub: every service
    # depends on the mesh/config hubs, so hub-mediated paths would glue unrelated alerts together.
    hubs = [r["h"] for r in tg.read("""MATCH (u:Component)-[:DEPENDS_ON]->(n:Component) WITH n, count(u) AS deg WHERE deg >= 10
        RETURN n.id AS h""")]  # computed up front: works on FalkorDB 4.x and 6.x alike
    pairs = tg.read("""MATCH p=(a:Component)-[:DEPENDS_ON*1..2]-(b:Component) WHERE a.id IN $E AND b.id IN $E AND a.id < b.id
        AND all(x IN relationships(p) WHERE x.valid_to IS NULL OR x.valid_to > $t)
        AND all(n IN nodes(p) WHERE n = a OR n = b OR NOT n.id IN $hubs)
        RETURN DISTINCT a.id AS a, b.id AS b""", {"E": E, "t": t0, "hubs": hubs})
    related = {(p["a"], p["b"]) for p in pairs} | {(p["b"], p["a"]) for p in pairs}
    # alert-level union-find: two alerts correlate if same/related component AND within 5 minutes
    parent = {a["id"]: a["id"] for a in alerts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    srt = sorted(alerts, key=lambda a: a["ts"])
    for i, a in enumerate(srt):
        for b in srt[i + 1:]:
            if b["ts"] - a["ts"] > 5 * MIN:
                break
            if a["e"] == b["e"] or (a["e"], b["e"]) in related:
                parent[find(a["id"])] = find(b["id"])
    clusters = {}
    for a in alerts:
        clusters.setdefault(find(a["id"]), []).append(a)
    members = max(clusters.values(), key=lambda c: (len({a["e"] for a in c}), len(c)))
    best = sorted({a["e"] for a in members})
    first = min(members, key=lambda a: a["ts"])
    sym = "latency" if any("Latency" in a["name"] or "Slow" in a["name"] for a in members) else "errors"
    title = f"{first['en']} {'latency degraded' if sym == 'latency' else 'elevated error rate'}"
    tg.write("""CREATE (i:Incident {id:$id, key:$key, title:$title, sev:3, started_at:$t, detected_at:$t, status:'open', mem:'episodic'})
        WITH i UNWIND $E AS eid MATCH (e:Component {id:eid}) CREATE (i)-[:AFFECTED]->(e)""",
             {"id": incident_id, "key": key, "title": title, "t": first["ts"], "E": best})
    tg.write("""MATCH (i:Incident {id:$id}) UNWIND $A AS aid MATCH (a:Alert {id:aid}) CREATE (a)-[:PART_OF]->(i)""",
             {"id": incident_id, "A": [a["id"] for a in members]})
    tg.write("""MATCH (i:Incident {id:$id}) CREATE (:Task {id:$tid, type:'triage', status:'SUBMITTED', version:0, created_at:$t})-[:FOR]->(i)""",
             {"id": incident_id, "tid": f"task_{incident_id}_triage", "t": first["ts"]})
    try:
        from ..core.embed import embed_incident
        embed_incident(tg, incident_id)
    except Exception:
        pass  # embeddings are an enhancement; structural similarity still works
    return {"incident_id": incident_id, "key": key, "title": title, "alerting": best, "n_alerts": len(members),
            "noise_dropped": len(alerts) - len(members), "detected_at": first["ts"]}


def claim_task(tg: TenantGraph, task_id: str, agent: str, now: int, lease_ms: int = 60_000) -> bool:
    """Single-statement compare-and-set claim (one writer per graph makes this atomic). Re-entrant for the current holder."""
    rows = tg.write("""MATCH (t:Task {id:$id})
        WHERE t.status = 'SUBMITTED' OR (t.status = 'WORKING' AND (t.lease_until < $now OR t.claimed_by = $agent))
        SET t.status = 'WORKING', t.claimed_by = $agent, t.lease_until = $now + $lease, t.version = t.version + 1
        RETURN t.version AS v""", {"id": task_id, "now": now, "agent": agent, "lease": lease_ms})
    return bool(rows)


def complete_task(tg: TenantGraph, task_id: str, next_type: str | None, incident_id: str, now: int):
    tg.write("MATCH (t:Task {id:$id}) SET t.status = 'COMPLETED', t.completed_at = $now", {"id": task_id, "now": now})
    if next_type:
        tg.write("""MATCH (i:Incident {id:$inc}) MERGE (t:Task {id:$tid}) ON CREATE SET t.type=$type, t.status='SUBMITTED', t.version=0,
            t.created_at=$now MERGE (t)-[:FOR]->(i)""", {"inc": incident_id, "tid": f"task_{incident_id}_{next_type}", "type": next_type, "now": now})
