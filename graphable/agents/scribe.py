"""Scribe: consolidation and learning on incident close (Track 2; ARCHITECTURE §10).

Writes, idempotently: the agents' conclusion as SUSPECTED_CAUSE (never as fact), SIMILAR_TO links, ExpeL-style
Lesson votes, runbook step statistics, and a *proposed* runbook when none exists (Voyager/CoALA gate:
procedural memory is only promoted after verification or human approval).
confirm() is the human/verification signal: it promotes SUSPECTED_CAUSE to CAUSED_BY and upvotes lessons.
"""
from __future__ import annotations

import uuid

from ..core.db import TenantGraph


def consolidate(tg: TenantGraph, incident_id: str, inv: dict, resp: dict, now: int) -> dict:
    out = {}
    root = (inv or {}).get("root")
    if root:
        tgt = root.get("change_id") or root["entity_id"]
        lbl = "Change" if root.get("change_id") else "Component"
        tg.write(f"""MATCH (i:Incident {{id:$i}}), (x:{lbl} {{id:$x}}) MERGE (i)-[s:SUSPECTED_CAUSE]->(x)
            ON CREATE SET s.by = 'investigator', s.score = $score, s.ts = $now, s.verified = $v""",
                 {"i": incident_id, "x": tgt, "score": root["score"], "now": now,
                  "v": any(c.get("verified") for c in inv.get("claims", []) if c.get("role") == "root_cause")})
        out["suspected_cause"] = tgt
    for s in (inv or {}).get("similar", [])[:3]:
        tg.write("""MATCH (a:Incident {id:$a}), (b:Incident {id:$b}) MERGE (a)-[r:SIMILAR_TO]->(b)
            ON CREATE SET r.score = $s, r.basis = 'scribe', r.computed_at = $now""",
                 {"a": incident_id, "b": s["incident_id"], "s": s["similarity"], "now": now})
    out["similar_links"] = len((inv or {}).get("similar", [])[:3])
    if root:
        key = f"les_{root['entity_id']}_{root.get('change_kind') or root.get('fault_type') or 'x'}"
        rb = (inv.get("runbook") or {}).get("key")
        text = (f"{root.get('change_kind') or root.get('fault_type')} on {root['entity_name']} has caused incidents "
                f"({incident_id}); check recent {root.get('change_kind') or 'related'} changes first" + (f", fix via {rb}." if rb else "."))
        tg.write("""MATCH (i:Incident {id:$i}), (c:Component {id:$c})
            MERGE (l:Lesson {id:$id}) ON CREATE SET l.text = $t, l.votes = 0, l.status = 'proposed', l.ts = $now, l.mem = 'procedural'
            MERGE (l)-[:ABOUT]->(c) MERGE (l)-[:DERIVED_FROM]->(i)""",
                 {"i": incident_id, "c": root["entity_id"], "id": key, "t": text, "now": now})
        out["lesson"] = key
        has_rb = tg.read("MATCH (rb:Runbook {status:'active'})-[:MITIGATES]->(:Component {id:$c}) RETURN count(rb) AS n", {"c": root["entity_id"]})[0]["n"]
        if not has_rb:
            rid = "rb_auto_" + uuid.uuid4().hex[:6]
            steps = [f"Check recent changes on {root['entity_name']}", "Roll back the suspect change" if root.get("change_id") else
                     f"Apply {root.get('fault_type')} mitigation", "Verify alerts clear on upstream services", "Record the outcome"]
            tg.write("""MATCH (c:Component {id:$c}) CREATE (rb:Runbook {id:$id, key:$key, title:$title, status:'proposed', version:1,
                created_at:$now, last_verified_at:null, text:$text, mentions:[c.name], induced_from:$inc, mem:'procedural'})-[:MITIGATES]->(c)""",
                     {"c": root["entity_id"], "id": rid, "key": "RB-auto-" + rid[-6:], "title": f"{root['entity_name']}: induced runbook",
                      "now": now, "text": "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)), "inc": incident_id})
            out["proposed_runbook"] = rid
    tg.write("MATCH (i:Incident {id:$i}) SET i.status = 'mitigating', i.consolidated_at = $now", {"i": incident_id, "now": now})
    return out


def confirm(tg: TenantGraph, incident_id: str, correct: bool, by: str, now: int, actual_cause_id: str | None = None) -> dict:
    """Human/verification feedback. Correct => promote SUSPECTED_CAUSE to CAUSED_BY, upvote lesson, count runbook success.
    Wrong => downvote lesson (retired at 0 votes, ExpeL), keep the suspicion as refuted."""
    if correct:
        tg.write("""MATCH (i:Incident {id:$i})-[s:SUSPECTED_CAUSE]->(x) SET s.confirmed = true
            MERGE (i)-[c:CAUSED_BY]->(x) ON CREATE SET c.source_id = $by, c.confidence = 1.0, c.recorded_at = $now""",
                 {"i": incident_id, "by": f"confirmed:{by}", "now": now})
        tg.write("MATCH (l:Lesson)-[:DERIVED_FROM]->(:Incident {id:$i}) SET l.votes = l.votes + 1, l.status = 'active'", {"i": incident_id})
        tg.write("MATCH (h:Hypothesis)<-[:HAS_HYPOTHESIS]-(:Incident {id:$i}) WHERE h.kind = 'hypothesis' SET h.status = 'confirmed'",
                 {"i": incident_id})
    else:
        tg.write("MATCH (i:Incident {id:$i})-[s:SUSPECTED_CAUSE]->() SET s.confirmed = false", {"i": incident_id})
        tg.write("""MATCH (l:Lesson)-[:DERIVED_FROM]->(:Incident {id:$i}) SET l.votes = l.votes - 1,
            l.status = CASE WHEN l.votes - 1 <= 0 THEN 'retired' ELSE l.status END""", {"i": incident_id})
        tg.write("MATCH (h:Hypothesis)<-[:HAS_HYPOTHESIS]-(:Incident {id:$i}) WHERE h.kind = 'hypothesis' SET h.status = 'refuted'", {"i": incident_id})
        if actual_cause_id:  # learn from failure (Reflexion): record the real cause with human provenance + a reflection
            lbl = "Change" if actual_cause_id.startswith("chg_") else "Component"
            tg.write(f"""MATCH (i:Incident {{id:$i}}), (x:{lbl} {{id:$x}})
                MERGE (i)-[c:CAUSED_BY]->(x) ON CREATE SET c.source_id = $by, c.confidence = 1.0, c.recorded_at = $now
                WITH i, x OPTIONAL MATCH (x)-[:CHANGED]->(comp:Component)
                WITH i, x, coalesce(comp, x) AS c0 OPTIONAL MATCH (i)-[:SUSPECTED_CAUSE]->(wrong)
                CREATE (r:Reflection {{id:$rid, ts:$now, mem:'episodic',
                        text: 'Agent blamed ' + coalesce(wrong.summary, wrong.name, wrong.id, '?') + '; actual cause: ' + coalesce(x.summary, x.name, x.id)}})
                CREATE (r)-[:ABOUT]->(c0), (r)-[:DERIVED_FROM]->(i)""",
                     {"i": incident_id, "x": actual_cause_id, "by": f"human:{by}", "now": now, "rid": f"refl_{incident_id}"})
    tg.write("MATCH (i:Incident {id:$i}) SET i.status = 'resolved', i.feedback = $c", {"i": incident_id, "c": correct})
    return {"incident_id": incident_id, "confirmed": correct}
