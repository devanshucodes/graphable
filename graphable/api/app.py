"""FastAPI gateway (ARCHITECTURE §11): REST + SSE for the web app.

  uvicorn graphable.api.app:app --port 8000
Demo tenant `demo_main` is a GRAPH.COPY of golden_acme; POST /v1/admin/reset restores it.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal

from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..agents.brain import ask
from ..agents.crew import resume_crew, run_crew
from ..agents.scribe import confirm
from ..core import tools as T
from ..core.db import TenantGraph, client
from ..core.evidence import Ctx
from ..ingest.pipeline import ingest_message
from ..settings import ROOT, settings
from ..sim.player import fresh_tenant, intake, load_scenario, play

DEMO = os.getenv("DEMO_TENANT", "demo_main")
DEMO_NOW = int(os.getenv("DEMO_NOW", "1791936000000"))  # 2026-10-14T00:00Z: the demo clock (after all hero scenarios)
MIN = 60_000


def tenant_dep(x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key is None:
        return DEMO
    t = settings.tenant_keys.get(x_api_key)
    if not t:
        raise HTTPException(401, "invalid API key")
    return t


SNAPSHOT = os.getenv("DEMO_SNAPSHOT")  # hosted mode: a DUMP of the demo graph shipped with the backend (no golden copy in the DB)


def restore_demo():
    """Put the demo graph back in its starting state. Locally: GRAPH.COPY of the golden snapshot. Hosted (DEMO_SNAPSHOT set):
    delete the graph, then RESTORE it from the snapshot file, so the database only ever holds one copy."""
    if SNAPSHOT:
        payload = Path(SNAPSHOT).read_bytes()
        con = client().connection
        con.delete(f"t_{DEMO}")
        con.restore(f"t_{DEMO}", 0, payload)
    else:
        fresh_tenant("acme", DEMO)


def ensure_demo():
    if f"t_{DEMO}" not in client().list_graphs():  # first start, or a database that lost its data (no persistence)
        restore_demo()


@asynccontextmanager
async def lifespan(app):
    ensure_demo()
    yield


app = FastAPI(title="Graphable API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ run manager (live agent events)
class Run:
    def __init__(self, tenant, incident_id):
        self.tenant, self.incident_id = tenant, incident_id
        self.events, self.listeners, self.status, self.result = [], [], "running", None
        self.task: asyncio.Task | None = None  # keep a strong reference to the background crew task

    def emit(self, e: dict):
        e = {"seq": len(self.events) + 1, "t": time.time(), **e}
        self.events.append(e)
        for q in list(self.listeners):
            q.put_nowait(e)


RUNS: dict[tuple, Run] = {}  # keyed by (tenant, incident_id)


async def _bus_mode() -> str:
    if os.getenv("BUS_MODE") in ("direct", "mcp"):
        return os.environ["BUS_MODE"]
    try:  # a real MCP initialize handshake: any 4xx/5xx (e.g. 421 Invalid Host) means the bus is unusable
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.post(settings.mcp_url, headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                             json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                                 "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "graphable-probe", "version": "1"}}})
        return "mcp" if r.status_code < 400 else "direct"
    except Exception:
        return "direct"


async def _drive(run: Run, inc: dict, now: int):
    try:
        mode = await _bus_mode()
        run.emit({"type": "start", "incident_id": inc["incident_id"], "bus": mode, "alerting": inc["alerting"], "title": inc["title"]})
        out = await run_crew(run.tenant, inc, now, bus_mode=mode, auto_approve=False, emit=run.emit, thread_id=f"{run.tenant}:{run.incident_id}")
        run.result = _summ(out)
        run.status = "awaiting_approval" if "__interrupt__" in out else "closed"
        run.emit({"type": "result", "status": run.status, "result": run.result})
    except Exception as e:  # surface failures to the UI instead of hanging the stream
        run.status = "error"
        run.emit({"type": "error", "error": str(e)[:300]})


def _summ(out: dict) -> dict:
    keep = ("triage", "investigation", "verification", "response", "gate", "scribe", "elapsed_s")
    return json.loads(json.dumps({k: out.get(k) for k in keep}, default=str))


# ------------------------------------------------------------------ endpoints
@app.get("/v1/health")
def health():
    try:
        ensure_demo()  # also self-heals a hosted database that restarted empty
        TenantGraph(DEMO).read("RETURN 1 AS ok")
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "falkordb": db_ok, "llm": settings.llm_enabled, "demo_tenant": DEMO}


@app.get("/v1/overview")
def overview(tenant: str = Depends(tenant_dep)):
    tg = TenantGraph(tenant)
    counts = _counts(tg)
    edges = tg.read("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    total_nodes = tg.read("MATCH (n) RETURN count(n) AS n")[0]["n"]
    recent = tg.read("""MATCH (i:Incident) RETURN i.id AS id, i.key AS key, i.title AS title, i.sev AS sev, i.status AS status,
        i.started_at AS started_at ORDER BY i.started_at DESC LIMIT 12""")
    teams = tg.read("""MATCH (t:Team) WHERE t.dissolved_at IS NULL OPTIONAL MATCH (t)-[o:OWNS]->(c:Component) WHERE o.valid_to IS NULL
        RETURN t.id AS id, t.name AS name, count(c) AS owns ORDER BY owns DESC""")
    return {"tenant": tenant, "counts": counts, "edges": edges, "nodes": total_nodes, "recent_incidents": recent, "teams": teams, "llm": settings.llm_enabled}


def _counts(tg: TenantGraph) -> dict:
    out = {}
    for l in ["Service", "Datastore", "Person", "Team", "Change", "Incident", "Alert", "Message", "Ticket", "Doc", "Runbook", "Decision",
              "Evidence", "Hypothesis", "Lesson", "AgentRun", "Action"]:
        out[l] = tg.read(f"MATCH (n:{l}) RETURN count(n) AS n")[0]["n"]
    return out


@app.get("/v1/scenarios")
def list_scenarios():
    d = settings.data_dir / "acme" / "scenarios"
    out = {}
    for kind in ("hero", "test", "learning"):
        out[kind] = [{"id": json.loads(p.read_text())["id"], "type": json.loads(p.read_text())["type"],
                      "story": json.loads(p.read_text()).get("story")} for p in sorted((d / kind).glob("*.json"))]
    return out


class PlayReq(BaseModel):
    kind: Literal["hero", "test", "learning"] = "hero"
    sid: str = Field(default="H1", pattern=r"^[A-Za-z]{1,3}-?\d{1,3}$")


@app.post("/v1/sim/play")
async def sim_play(req: PlayReq, tenant: str = Depends(tenant_dep)):
    try:
        sc = load_scenario("acme", req.kind, req.sid)
    except FileNotFoundError:
        raise HTTPException(404, f"unknown scenario {req.kind}/{req.sid}")
    tg = TenantGraph(tenant)
    tag = uuid.uuid4().hex[:6]
    n = await asyncio.to_thread(play, tg, sc, None, tag)
    inc_id = f"inc_{req.sid.lower().replace('-', '')}_{tag[:4]}"
    inc = await asyncio.to_thread(intake, tg, sc["t0"], inc_id, f"INC-{req.sid.upper()}-{tag[:4].upper()}")
    if not inc:
        raise HTTPException(409, "No alerts to correlate. Click Reset demo and try again.")
    run = Run(tenant, inc_id)
    RUNS[(tenant, inc_id)] = run
    run.task = asyncio.create_task(_drive(run, inc, inc["detected_at"] + 5 * MIN))
    return {"incident_id": inc_id, "key": inc["key"], "played": n, "intake": inc, "story": sc.get("story")}


@app.get("/v1/incidents/{incident_id}/stream")
async def stream(incident_id: str, tenant: str = Depends(tenant_dep)):
    run = RUNS.get((tenant, incident_id))
    if not run:
        raise HTTPException(404, "no live run for this incident")
    q: asyncio.Queue = asyncio.Queue()
    for e in run.events:
        q.put_nowait(e)
    run.listeners.append(q)

    async def gen():
        try:
            while True:
                e = await q.get()
                yield {"event": e["type"], "data": json.dumps(e, default=str)}
                if e["type"] == "error" or (e["type"] == "result" and e["status"] in ("closed", "error")):
                    break
        finally:
            run.listeners.remove(q)
    return EventSourceResponse(gen())


@app.get("/v1/incidents/{incident_id}")
def incident(incident_id: str, tenant: str = Depends(tenant_dep)):
    tg = TenantGraph(tenant)
    r = tg.read("""MATCH (i:Incident {id:$id}) RETURN i AS inc,
        [(i)<-[:PART_OF]-(a:Alert)-[:FIRED_ON]->(c) | {id:a.id, name:a.name, ts:a.ts, component:c.name, component_id:c.id}] AS alerts,
        [(i)-[:HAS_HYPOTHESIS]->(h) | {id:h.id, kind:h.kind, claim:h.claim, status:h.status, confidence:h.confidence, agent:h.agent}] AS hypotheses,
        [(a:Action)-[:FOR]->(i) | {id:a.id, kind:a.kind, target:a.target, tier:a.tier, status:a.status, payload:a.payload}] AS actions,
        [(h:Handoff)-[:FOR]->(i) | {id:h.id, from:h.from_agent, to:h.to_agent, summary:h.summary, open_questions:h.open_questions}] AS handoffs,
        [(i)-[:SUSPECTED_CAUSE]->(x) | {id:x.id, label:coalesce(x.summary, x.name)}] AS suspected,
        [(i)-[:CAUSED_BY]->(x) | {id:x.id, label:coalesce(x.summary, x.name)}] AS caused_by,
        [(i)-[s:SIMILAR_TO]->(j) | {id:j.id, key:j.key, score:s.score}] AS similar""", {"id": incident_id})
    if not r:
        raise HTTPException(404)
    run = RUNS.get((tenant, incident_id))
    return {**r[0], "ownership_moves": _ownership_moves(tg, incident_id), "run": {"status": run.status, "result": run.result} if run else None}


def _ownership_moves(tg: TenantGraph, incident_id: str) -> list[dict]:
    """Alerting services whose owner changed (from a chat message, not the catalog) in the 7 days before the incident:
    their alerts now page the new team, while history keeps the old owner."""
    return tg.read("""MATCH (i:Incident {id:$id})<-[:PART_OF]-(:Alert)-[:FIRED_ON]->(c:Component)
        WITH DISTINCT i, c, coalesce(i.detected_at, i.started_at) AS t0
        MATCH (nt:Team)-[o:OWNS]->(c) WHERE o.valid_from <= t0 AND o.valid_from > t0 - 7 * 86400000
          AND (o.valid_to IS NULL OR o.valid_to > t0) AND coalesce(o.source_id, 'catalog') <> 'catalog'
        OPTIONAL MATCH (ot:Team)-[prev:OWNS]->(c) WHERE prev.valid_to = o.valid_from
        OPTIONAL MATCH (m:Message {id:o.source_id})-[:IN]->(th:Thread)
        OPTIONAL MATCH (au:Person)-[:SENT]->(m)
        OPTIONAL MATCH (p:Person)-[:HAS_SHIFT]->(s:OnCallShift)-[:COVERS]->(nt) WHERE s.start <= t0 AND s.end > t0
        RETURN c.id AS entity_id, c.name AS entity_name, ot.name AS from_team, nt.name AS to_team, o.source_id AS source_id,
               th.channel AS source_channel, au.name AS source_author, o.valid_from AS valid_from,
               collect(DISTINCT {id: p.id, name: p.name}) AS oncall""", {"id": incident_id})


class Decision(BaseModel):
    decision: str = "approve"


@app.post("/v1/actions/{action_id}/{decision}")
async def decide(action_id: str, decision: str, tenant: str = Depends(tenant_dep)):
    if decision not in ("approve", "reject"):
        raise HTTPException(400)
    tg = TenantGraph(tenant)
    inc = tg.read("MATCH (a:Action {id:$id})-[:FOR]->(i:Incident) RETURN i.id AS i", {"id": action_id})
    if not inc:
        raise HTTPException(404)
    iid = inc[0]["i"]
    run = RUNS.get((tenant, iid))
    if run and run.status == "resuming":
        raise HTTPException(409, "This incident is already being resumed.")
    if run and run.status == "awaiting_approval":
        run.status = "resuming"  # set before the first await: a double click cannot resume the crew twice
        pending = tg.read("MATCH (a:Action)-[:FOR]->(:Incident {id:$i}) WHERE a.status = 'awaiting_approval' RETURN a.id AS id", {"i": iid})
        decisions = {action_id: decision}  # only the action the human decided on; any others stay pending
        try:
            out = await resume_crew(tenant, iid, decisions, emit=run.emit, thread_id=f"{tenant}:{iid}")
        except Exception as e:
            run.status = "error"
            run.emit({"type": "error", "error": str(e)[:300]})
            raise HTTPException(500, "resuming the crew failed")
        run.result = _summ(out)
        run.status = "closed"
        run.emit({"type": "result", "status": "closed", "result": run.result})
    else:
        tg.write("MATCH (a:Action {id:$id}) SET a.status = $s, a.decided_by = 'human'", {"id": action_id, "s": "approved" if decision == "approve" else "rejected"})
    return {"action_id": action_id, "decision": decision, "incident_id": iid}


class Feedback(BaseModel):
    correct: bool
    actual_cause_id: str | None = None
    by: str = "oncall"


@app.post("/v1/incidents/{incident_id}/feedback")
def feedback(incident_id: str, fb: Feedback, tenant: str = Depends(tenant_dep)):
    tg = TenantGraph(tenant)
    r = tg.read("MATCH (i:Incident {id:$id}) RETURN i.feedback AS fb", {"id": incident_id})
    if not r:
        raise HTTPException(404, "unknown incident")
    if r[0]["fb"] is not None:
        raise HTTPException(409, f"Feedback was already recorded for this incident ({'correct' if r[0]['fb'] else 'wrong'}).")
    out = confirm(tg, incident_id, fb.correct, fb.by, int(time.time() * 1000), fb.actual_cause_id)
    tg.write("MATCH (i:Incident {id:$id}) SET i.feedback = $c", {"id": incident_id, "c": fb.correct})
    return out


class AskReq(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    at: int | None = Field(default=None, ge=0, le=4_102_444_800_000)
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@app.post("/v1/brain/ask")
async def brain(req: AskReq, tenant: str = Depends(tenant_dep)):
    if not req.question.strip():
        raise HTTPException(422, "Ask a question.")
    return await ask(tenant, req.question, req.at if req.at is not None else DEMO_NOW, bus_mode=await _bus_mode(), session_id=req.session_id)


@app.get("/v1/entities/{entity_id}")
def entity(entity_id: str, at: int | None = None, tenant: str = Depends(tenant_dep)):
    tg = TenantGraph(tenant)
    now = at or DEMO_NOW
    ctx = Ctx(tg, now=now, register=False)
    base = tg.read("MATCH (c:Component {id:$id}) RETURN c AS c", {"id": entity_id})
    if not base:
        raise HTTPException(404)
    deps = tg.read("""MATCH (c:Component {id:$id})-[r:DEPENDS_ON]->(d:Component) RETURN d.id AS id, d.name AS name, r.kind AS kind,
        r.critical AS critical, r.valid_from AS valid_from, r.valid_to AS valid_to""", {"id": entity_id})
    changes = tg.read("""MATCH (ch:Change)-[:CHANGED]->(:Component {id:$id}) WHERE ch.ts <= $now OPTIONAL MATCH (p:Person)-[:AUTHORED]->(ch)
        RETURN ch.id AS id, ch.kind AS kind, ch.ts AS ts, ch.summary AS summary, ch.rolled_back AS rolled_back, p.name AS author
        ORDER BY ch.ts DESC LIMIT 8""", {"id": entity_id, "now": now})
    incidents = tg.read("""MATCH (i:Incident)-[:AFFECTED]->(:Component {id:$id}) RETURN i.id AS id, i.key AS key, i.title AS title, i.sev AS sev,
        i.started_at AS started_at, [(i)-[:CAUSED_BY]->(x) | coalesce(x.summary, x.name)] AS cause ORDER BY i.started_at DESC LIMIT 8""", {"id": entity_id})
    blast = T.incident_blast_radius(ctx, entity_id, 3)
    return {"entity": base[0]["c"], "ownership": T.org_ownership(ctx, entity_id), "experts": T.org_experts(ctx, entity_id),
            "knowledge": T.knowledge_for_entity(ctx, entity_id, ["Runbook", "Doc", "Decision", "Lesson"], 4),
            "depends_on": deps, "blast_radius": {"n": blast["n_impacted"], "entry_points": blast["entry_points_hit"]},
            "recent_changes": changes, "incidents": incidents}


@app.get("/v1/entities")
def entities(tenant: str = Depends(tenant_dep)):
    return TenantGraph(tenant).read("""MATCH (c:Component) OPTIONAL MATCH (t:Team)-[o:OWNS]->(c) WHERE o.valid_to IS NULL
        RETURN c.id AS id, c.name AS name, c.kind AS kind, c.tier AS tier, coalesce(t.name, '') AS owner ORDER BY c.tier, c.name""")


@app.get("/v1/graph/topology")
def topology(tenant: str = Depends(tenant_dep)):
    tg = TenantGraph(tenant)
    nodes = tg.read("""MATCH (c:Component) OPTIONAL MATCH (t:Team)-[o:OWNS]->(c) WHERE o.valid_to IS NULL
        RETURN c.id AS id, c.name AS label, c.kind AS type, c.tier AS tier, coalesce(t.name,'') AS owner, coalesce(c.domain, 'data') AS group""")
    edges = tg.read("""MATCH (a:Component)-[r:DEPENDS_ON]->(b:Component) WHERE r.valid_to IS NULL
        RETURN a.id AS source, b.id AS target, r.kind AS kind, r.critical AS critical""")
    return {"nodes": nodes, "edges": edges}


@app.get("/v1/graph/incident/{incident_id}")
def incident_graph(incident_id: str, tenant: str = Depends(tenant_dep)):
    """Subgraph that justifies the crew's conclusion: alerting components, root-cause path, change, author, memory links."""
    tg = TenantGraph(tenant)
    ev = tg.read("MATCH (e:Evidence)-[:FOR]->(:Incident {id:$id}) RETURN e.tool AS tool, e.node_ids AS n, e.edge_keys AS k", {"id": incident_id})
    keys = {k for e in ev if e["tool"] in ("incident_rank_root_causes", "incident_similar") for k in (e["k"] or [])}
    alerting = [r["id"] for r in tg.read("MATCH (:Incident {id:$id})<-[:PART_OF]-(:Alert)-[:FIRED_ON]->(c) RETURN DISTINCT c.id AS id", {"id": incident_id})]
    root = tg.read("MATCH (:Incident {id:$id})-[:SUSPECTED_CAUSE]->(x) OPTIONAL MATCH (x)-[:CHANGED]->(c) RETURN x.id AS x, c.id AS c", {"id": incident_id})
    ids = set(alerting) | {incident_id} | {p for k in keys for p in (k.split("|")[0], k.split("|")[2])}
    nodes = _nodes(tg, sorted(ids))
    edges = [{"source": k.split("|")[0], "type": k.split("|")[1], "target": k.split("|")[2]} for k in keys]
    edges += [{"source": incident_id, "type": "ALERTED_ON", "target": a} for a in alerting]
    return {"nodes": nodes, "edges": edges, "alerting": alerting, "root": root[0] if root else None}


def _nodes(tg: TenantGraph, ids: list[str]) -> list[dict]:
    out = []
    by_lbl = {}
    for i in ids:
        try:
            by_lbl.setdefault(T.label_of(i), []).append(i)
        except ValueError:
            continue
    for lbl, xs in by_lbl.items():
        out += tg.read(f"MATCH (n:{lbl}) WHERE n.id IN $ids RETURN n.id AS id, coalesce(n.name, n.key, n.summary, n.title, n.id) AS label, "
                       f"'{lbl}' AS type, n.tier AS tier", {"ids": xs})
    return out


class IngestReq(BaseModel):
    author_id: str = Field(max_length=64)
    text: str = Field(max_length=2000)
    channel: str = Field(default="#announcements", max_length=64)
    ts: int | None = Field(default=None, ge=0, le=4_102_444_800_000)


@app.post("/v1/ingest/message")
def ingest(req: IngestReq, tenant: str = Depends(tenant_dep)):
    if not TenantGraph(tenant).read("MATCH (p:Person {id:$a}) RETURN p.id AS id", {"a": req.author_id}):
        raise HTTPException(422, "Unknown author.")
    if not req.text.strip():
        raise HTTPException(422, "Write a message first.")
    msg = {"id": "msg_live_" + uuid.uuid4().hex[:8], "author_id": req.author_id, "ts": req.ts or DEMO_NOW - 3600_000, "text": req.text}
    out = ingest_message(tenant, msg, req.channel)
    ctx = Ctx(TenantGraph(tenant), now=DEMO_NOW, register=False)
    out["ownership_now"] = {f["dst"]: T.org_ownership(ctx, f["dst"]) for f in out["facts"] if f["rel"] == "OWNS"}
    return out


@app.get("/v1/memory")
def memory(tenant: str = Depends(tenant_dep)):
    tg = TenantGraph(tenant)
    q = tg.read
    return {
        "counts": {l: q(f"MATCH (n:{l}) RETURN count(n) AS n")[0]["n"] for l in
                   ["Evidence", "Hypothesis", "Handoff", "AgentRun", "AgentStep", "Action", "Lesson", "Reflection", "Answer", "Task"]},
        "lessons": q("""MATCH (l:Lesson) OPTIONAL MATCH (l)-[:ABOUT]->(c:Component) RETURN l.id AS id, l.text AS text, l.votes AS votes,
            l.status AS status, c.name AS component ORDER BY l.votes DESC LIMIT 20"""),
        "reflections": q("""MATCH (r:Reflection) OPTIONAL MATCH (r)-[:ABOUT]->(c:Component) RETURN r.id AS id, r.text AS text, c.name AS component
            ORDER BY r.ts DESC LIMIT 20"""),
        "proposed_runbooks": q("MATCH (rb:Runbook {status:'proposed'}) RETURN rb.id AS id, rb.key AS key, rb.title AS title, rb.text AS text LIMIT 20"),
        "handoffs": q("""MATCH (h:Handoff)-[:FOR]->(i:Incident) RETURN h.from_agent AS from, h.to_agent AS to, h.summary AS summary,
            h.open_questions AS open_questions, i.key AS incident ORDER BY h.ts DESC LIMIT 20"""),
        "runs": q("""MATCH (r:AgentRun) OPTIONAL MATCH (r)-[:INFORMED_BY]->(p:AgentRun) RETURN r.id AS id, r.agent AS agent, r.n_steps AS steps,
            p.agent AS informed_by, r.summary AS summary ORDER BY r.started_at DESC LIMIT 30"""),
    }


@app.get("/v1/people")
def people(tenant: str = Depends(tenant_dep)):
    return TenantGraph(tenant).read("""MATCH (p:Person) WHERE p.left_at IS NULL OPTIONAL MATCH (p)-[m:MEMBER_OF]->(t:Team) WHERE m.valid_to IS NULL
        RETURN p.id AS id, p.name AS name, p.handle AS handle, p.role AS role, coalesce(t.name, '') AS team ORDER BY p.name""")


@app.post("/v1/admin/reset")
def reset():
    for run in RUNS.values():  # stop in-flight crews so their writes don't land in the fresh graph
        if run.task and not run.task.done():
            run.task.cancel()
    RUNS.clear()
    restore_demo()
    return {"reset": DEMO}


@app.get("/v1/eval")
def eval_results():
    d = ROOT / "evals_out"
    out = {}
    for name in ("rca_test", "rca_train", "system_mcp", "crew_gemini_mcp", "crew_offline_mcp"):
        p = d / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text())
    out["extraction"] = {p.stem.replace("extraction_", ""): json.loads(p.read_text()) for p in sorted(d.glob("extraction_*.json"))}
    return out
