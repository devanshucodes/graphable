"""Bulk loader for systems of record (deterministic, templated Cypher, UNWIND batches).

Loads world/ + events/ + artifact metadata and text. Knowledge that lives only in prose
(root causes, runbooks used, decision links, mentions) is NOT loaded here: the Ingestor
extracts it with provenance (ARCHITECTURE §6). truth/ is never loaded.

Usage: python -m graphable.ingest.loader --tenant acme [--golden]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..core.db import TenantGraph, client
from ..core.schema import migrate
from ..settings import settings

BATCH = 2000


def _jsonl(p: Path):
    with p.open() as f:
        for line in f:
            yield json.loads(line)


def _batches(rows, n=BATCH):
    buf = []
    for r in rows:
        buf.append(r)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _run(tg, q, rows, label):
    t, n = time.time(), 0
    for b in _batches(rows):
        tg.write(q, {"rows": b}, timeout=120_000)
        n += len(b)
    print(f"  {label:<28} {n:>7,}  ({time.time() - t:.1f}s)")
    return n


BT = "valid_from: r.valid_from, valid_to: r.valid_to, recorded_at: r.recorded_at, expired_at: r.expired_at, source_id: r.source_id"


def load(tenant: str, data_dir: Path | None = None, fresh: bool = True) -> dict:
    d = (data_dir or settings.data_dir) / tenant
    tg = TenantGraph(tenant)
    if fresh and tg.exists():
        tg.g.delete()
        tg = TenantGraph(tenant)
    t0 = time.time()
    tg.write("CREATE (:Meta {id:'meta', tenant:$t, loaded_at:timestamp()})", {"t": tenant})
    migrate(tg, constraints=False)  # constraints after bulk load (faster)
    W, E, A = d / "world", d / "events", d / "artifacts"
    c = {}
    # ---------- nodes ----------
    c["Service"] = _run(tg, """UNWIND $rows AS r CREATE (n:Component:Service {id:r.id, name:r.name, aliases:r.aliases,
        aliases_text: reduce(s='', a IN r.aliases | s + ' ' + replace(a,'-',' ')) + ' ' + replace(r.name,'-',' '),
        domain:r.domain, tier:r.tier, language:r.language, runtime:r.runtime, repo:r.repo,
        slo_availability:r.slo_availability, slo_latency_p99_ms:r.slo_latency_p99_ms, entry_point:r.entry_point,
        customer_facing:r.customer_facing, regions:r.regions, created_at:r.created_at, kind:'service', mem:'semantic'})""",
                          _jsonl(W / "services.jsonl"), "Service")
    c["Datastore"] = _run(tg, """UNWIND $rows AS r CREATE (n:Component:Datastore {id:r.id, name:r.name, aliases:r.aliases,
        aliases_text: reduce(s='', a IN r.aliases | s + ' ' + replace(a,'-',' ')) + ' ' + replace(r.name,'-',' '),
        engine:r.engine, tier:r.tier, version:r.version, replicas:r.replicas, kind:'datastore', mem:'semantic'})""",
                            _jsonl(W / "datastores.jsonl"), "Datastore")
    c["Region"] = _run(tg, "UNWIND $rows AS r CREATE (:Region {id:r.id, name:r.name})", _jsonl(W / "regions.jsonl"), "Region")
    c["Team"] = _run(tg, """UNWIND $rows AS r CREATE (:Team {id:r.id, key:r.key, name:r.name, created_at:r.created_at,
        dissolved_at:r.dissolved_at, mem:'semantic'})""", _jsonl(W / "teams.jsonl"), "Team")
    c["Person"] = _run(tg, """UNWIND $rows AS r CREATE (:Person {id:r.id, name:r.name, handle:r.handle,
        former_handles:[h IN r.former_handles | h.handle], email:r.email, role:r.role, seniority:r.seniority,
        timezone:r.timezone, joined_at:r.joined_at, left_at:r.left_at, active: r.left_at IS NULL, mem:'semantic'})""",
                         _jsonl(W / "people.jsonl"), "Person")
    for lbl in ["Component", "Service", "Datastore", "Team", "Person", "Region"]:
        pass
    c["Endpoint"] = _run(tg, """UNWIND $rows AS r MATCH (s:Service {id:r.service_id})
        CREATE (s)-[:EXPOSES]->(:Endpoint {id:r.id, method:r.method, path:r.path, public:r.public, p99_ms:r.p99_ms})""",
                           _jsonl(W / "endpoints.jsonl"), "Endpoint")
    c["FeatureFlag"] = _run(tg, """UNWIND $rows AS r MATCH (s:Service {id:r.service_id})
        CREATE (f:FeatureFlag {id:r.id, key:r.key, created_at:r.created_at, retired_at:r.retired_at, rollout_pct:r.rollout_pct})
        CREATE (f)-[:GATES]->(s)""", _jsonl(W / "flags.jsonl"), "FeatureFlag")
    # ---------- bi-temporal org + topology ----------
    c["MEMBER_OF"] = _run(tg, f"""UNWIND $rows AS r MATCH (p:Person {{id:r.person_id}}), (t:Team {{id:r.team_id}})
        CREATE (p)-[:MEMBER_OF {{{BT}}}]->(t)""", _jsonl(W / "memberships.jsonl"), "MEMBER_OF")
    c["OWNS"] = _run(tg, f"""UNWIND $rows AS r MATCH (t:Team {{id:r.team_id}}), (e:Component {{id:r.entity_id}})
        CREATE (t)-[:OWNS {{{BT}}}]->(e)""", _jsonl(W / "ownership.jsonl"), "OWNS")
    c["REPORTS_TO"] = _run(tg, f"""UNWIND $rows AS r MATCH (p:Person {{id:r.person_id}}), (m:Person {{id:r.manager_id}})
        CREATE (p)-[:REPORTS_TO {{{BT}}}]->(m)""", _jsonl(W / "reporting.jsonl"), "REPORTS_TO")
    c["DEPENDS_ON"] = _run(tg, f"""UNWIND $rows AS r MATCH (a:Component {{id:r.src}}), (b:Component {{id:r.dst}})
        CREATE (a)-[:DEPENDS_ON {{kind:r.kind, critical:r.critical, p50_ms:r.p50_ms, calls_per_min:r.calls_per_min, {BT}}}]->(b)""",
                             _jsonl(W / "dependencies.jsonl"), "DEPENDS_ON")
    c["OnCallShift"] = _run(tg, """UNWIND $rows AS r MATCH (p:Person {id:r.person_id}), (t:Team {id:r.team_id})
        CREATE (p)-[:HAS_SHIFT]->(s:OnCallShift {id:r.id, level:r.level, start:r.start, end:r.end})-[:COVERS]->(t)""",
                            _jsonl(W / "oncall.jsonl"), "OnCallShift")
    # ---------- events ----------
    c["Change"] = _run(tg, """UNWIND $rows AS r MATCH (e:Component {id:r.entity_id}), (a:Person {id:r.author_id})
        CREATE (ch:Change {id:r.id, kind:r.kind, ts:r.ts, summary:r.summary, canary:r.canary, rollout_pct:r.rollout_pct,
                flag_key:r.flag_key, pr_number:r.pr_number, version_to:r.version_to, rolled_back:r.rolled_back,
                rollback_of:r.rollback_of, mem:'episodic'})
        CREATE (ch)-[:CHANGED]->(e), (a)-[:AUTHORED]->(ch)
        WITH ch, r UNWIND r.reviewer_ids AS rid MATCH (rv:Person {id:rid}) CREATE (rv)-[:REVIEWED]->(ch)""",
                       _jsonl(E / "changes.jsonl"), "Change")
    c["Incident"] = _run(tg, """UNWIND $rows AS r
        CREATE (i:Incident {id:r.id, key:r.key, title:r.title, sev:r.sev, started_at:r.started_at, detected_at:r.detected_at,
                mitigated_at:r.mitigated_at, resolved_at:r.resolved_at, status:r.status, customer_impact:r.customer_impact,
                status_page:r.status_page, mitigation:r.mitigation, mem:'episodic'})
        WITH i, r UNWIND r.affected_entity_ids AS eid MATCH (e:Component {id:eid}) CREATE (i)-[:AFFECTED]->(e)""",
                         _jsonl(E / "incidents.jsonl"), "Incident")
    _run(tg, """UNWIND $rows AS r MATCH (i:Incident {id:r.id}) UNWIND r.responders AS x MATCH (p:Person {id:x.person_id})
        CREATE (p)-[:RESPONDED {role:x.role}]->(i)""", _jsonl(E / "incidents.jsonl"), "RESPONDED")
    _run(tg, """UNWIND $rows AS r MATCH (i:Incident {id:r.id}), (p:Person {id:r.resolver_id}) CREATE (i)-[:RESOLVED_BY]->(p)""",
         _jsonl(E / "incidents.jsonl"), "RESOLVED_BY")
    _run(tg, """UNWIND $rows AS r MATCH (i:Incident {id:r.id}), (t:Team {id:r.paged_team_id}) CREATE (i)-[:PAGED]->(t)""",
         _jsonl(E / "incidents.jsonl"), "PAGED")
    c["Alert"] = _run(tg, """UNWIND $rows AS r MATCH (e:Component {id:r.entity_id})
        CREATE (a:Alert {id:r.id, name:r.name, metric:r.metric, severity:r.severity, ts:r.ts, resolved_ts:r.resolved_ts,
                value:r.value, threshold:r.threshold, mem:'episodic'})-[:FIRED_ON]->(e)
        WITH a, r WHERE r.incident_ref IS NOT NULL MATCH (i:Incident {id:r.incident_ref}) CREATE (a)-[:PART_OF]->(i)""",
                      _jsonl(E / "alerts.jsonl"), "Alert")
    # ---------- artifacts (metadata + text; extraction adds knowledge later) ----------
    c["Thread"] = _run(tg, """UNWIND $rows AS r CREATE (t:Thread {id:r.id, channel:r.channel, started_at:r.started_at,
        kind:r.kind, title:r.title, mem:'episodic'})
        WITH t, r WHERE r.incident_id IS NOT NULL MATCH (i:Incident {id:r.incident_id}) CREATE (t)-[:ABOUT]->(i)""",
                       _jsonl(A / "threads.jsonl"), "Thread")
    c["Message"] = _run(tg, """UNWIND $rows AS r MATCH (t:Thread {id:r.thread_id}), (p:Person {id:r.author_id})
        CREATE (m:Message {id:r.id, ts:r.ts, text:r.text})-[:IN]->(t), (p)-[:SENT]->(m)""",
                        _jsonl(A / "messages.jsonl"), "Message")
    _run(tg, """UNWIND $rows AS r MATCH (m:Message {id:r.id}), (q:Message {id:r.reply_to}) CREATE (m)-[:REPLY_TO]->(q)""",
         (m for m in _jsonl(A / "messages.jsonl") if m.get("reply_to")), "REPLY_TO")
    c["Ticket"] = _run(tg, """UNWIND $rows AS r MATCH (a:Person {id:r.assignee_id}), (rp:Person {id:r.reporter_id})
        CREATE (t:Ticket {id:r.id, key:r.key, type:r.type, status:r.status, priority:r.priority, title:r.title, text:r.text,
                created_at:r.created_at, resolved_at:r.resolved_at, labels:r.labels})
        CREATE (t)-[:ASSIGNED_TO]->(a), (rp)-[:REPORTED]->(t)
        WITH t, r UNWIND r.entity_ids AS eid MATCH (e:Component {id:eid}) CREATE (t)-[:ABOUT]->(e)""",
                       _jsonl(A / "tickets.jsonl"), "Ticket")
    _run(tg, """UNWIND $rows AS r MATCH (t:Ticket {id:r.id}), (p:Person {id:r.resolver_id}) CREATE (t)-[:RESOLVED_BY]->(p)""",
         (t for t in _jsonl(A / "tickets.jsonl") if t.get("resolver_id")), "Ticket RESOLVED_BY")
    _run(tg, """UNWIND $rows AS r MATCH (t:Ticket {id:r.id}), (i:Incident {id:r.incident_id}) CREATE (t)-[:FOLLOW_UP_OF]->(i)""",
         (t for t in _jsonl(A / "tickets.jsonl") if t.get("incident_id")), "FOLLOW_UP_OF")
    c["Doc"] = _run(tg, """UNWIND $rows AS r MATCH (a:Person {id:r.author_id})
        CREATE (d:Doc {id:r.id, kind:r.kind, title:r.title, text:r.text, created_at:r.created_at})<-[:WROTE]-(a)
        WITH d, r UNWIND r.entity_ids AS eid MATCH (e:Component {id:eid}) CREATE (d)-[:ABOUT]->(e)""",
                    _jsonl(A / "docs.jsonl"), "Doc")
    _run(tg, """UNWIND $rows AS r MATCH (d:Doc {id:r.id}), (i:Incident {id:r.incident_id}) CREATE (d)-[:POSTMORTEM_OF]->(i)""",
         (x for x in _jsonl(A / "docs.jsonl") if x.get("incident_id")), "POSTMORTEM_OF")
    c["Runbook"] = _run(tg, """UNWIND $rows AS r MATCH (e:Component {id:r.entity_id}), (a:Person {id:r.author_id})
        CREATE (rb:Runbook {id:r.id, key:r.key, title:r.title, topic:r.topic, version:r.version, status:r.status,
                created_at:r.created_at, last_verified_at:r.last_verified_at, text:r.text, mentions:r.mentions, mem:'procedural'})
        CREATE (rb)-[:MITIGATES]->(e), (a)-[:WROTE]->(rb)
        WITH rb, r UNWIND range(0, size(r.steps)-1) AS i
        CREATE (rb)-[:HAS_STEP]->(:Step {id: r.id + '_s' + toString(i+1), n:i+1, text:r.steps[i], success_count:0,
                failure_count:0, mem:'procedural'})""", _jsonl(A / "runbooks.jsonl"), "Runbook+Step")
    tg.write("MATCH (rb:Runbook)-[:HAS_STEP]->(a:Step), (rb)-[:HAS_STEP]->(b:Step) WHERE b.n = a.n + 1 CREATE (a)-[:NEXT]->(b)")
    _run(tg, """UNWIND $rows AS r MATCH (a:Runbook {id:r.id}), (b:Runbook {id:r.supersedes}) CREATE (a)-[:SUPERSEDES]->(b)""",
         (x for x in _jsonl(A / "runbooks.jsonl") if x.get("supersedes")), "Runbook SUPERSEDES")
    c["Decision"] = _run(tg, """UNWIND $rows AS r CREATE (d:Decision {id:r.id, key:r.key, title:r.title, status:r.status,
        decided_at:r.decided_at, mem:'semantic'})
        WITH d, r WHERE r.doc_id IS NOT NULL MATCH (doc:Doc {id:r.doc_id}) CREATE (d)-[:DOCUMENTED_IN]->(doc)""",
                         _jsonl(A / "decisions_meta.jsonl"), "Decision (ADR registry)")
    # agents (static roster)
    tg.write("""UNWIND ['triage','investigator','responder','brain','scribe','ingestor'] AS a
        CREATE (:Agent {id:'agent_' + a, name:a})""")
    migrate(tg, constraints=True)
    stats = tg.read("MATCH (n) RETURN count(n) AS nodes")[0] | tg.read("MATCH ()-[r]->() RETURN count(r) AS edges")[0]
    mem = memory_mb(tg)
    print(f"[{tenant}] loaded {stats['nodes']:,} nodes, {stats['edges']:,} edges in {time.time() - t0:.1f}s; memory ≈ {mem} MB")
    return {"tenant": tenant, **stats, "memory_mb": mem, "counts": c}


def memory_mb(tg: TenantGraph) -> float | None:
    try:
        r = tg.g.execute_command("GRAPH.MEMORY", "USAGE", tg.name)
        if isinstance(r, list):
            kv = dict(zip(r[::2], r[1::2]))
            v = kv.get(b"total_graph_sz_mb") or kv.get("total_graph_sz_mb")
            return float(v) if v is not None else None
    except Exception:
        return None
    return None


def make_golden(tenant: str):
    """Snapshot for one-click demo reset (GRAPH.COPY)."""
    src, dst = f"t_{tenant}", f"golden_{tenant}"
    c = client()
    if dst in c.list_graphs():
        c.select_graph(dst).delete()
    c.select_graph(src).copy(dst)
    print(f"golden snapshot {dst} created")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="all")
    ap.add_argument("--golden", action="store_true")
    a = ap.parse_args()
    for t in (["acme", "globex"] if a.tenant == "all" else [a.tenant]):
        load(t)
        if a.golden:
            make_golden(t)


if __name__ == "__main__":
    main()
