"""Evidence registry (P3): every read tool registers exactly what it returned. Agents may only
cite evidence ids; the verifier re-checks them against the live graph."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from .db import TenantGraph


@dataclass
class Ctx:
    tg: TenantGraph
    now: int = field(default_factory=lambda: int(time.time() * 1000))
    incident_id: str | None = None
    agent: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    register: bool = True


def edge_key(src: str, rel: str, dst: str) -> str:
    return f"{src}|{rel}|{dst}"


def path_edges(ids: list[str], rel: str = "DEPENDS_ON") -> list[str]:
    return [edge_key(a, rel, b) for a, b in zip(ids, ids[1:])]


def register(ctx: Ctx, tool: str, params: dict, node_ids: list[str], edge_keys: list[str], summary: str) -> str | None:
    if not ctx.register:
        return None
    eid = "ev_" + uuid.uuid4().hex[:10]
    ctx.tg.write("""CREATE (:Evidence {id:$id, tool:$tool, params:$params, node_ids:$n, edge_keys:$e, summary:$s,
                    ts:$ts, incident_id:$inc, agent:$agent, run_id:$run, mem:'episodic'})""",
                 {"id": eid, "tool": tool, "params": json.dumps(params, default=str), "n": sorted(set(node_ids)),
                  "e": sorted(set(edge_keys)), "s": summary[:500], "ts": ctx.now, "inc": ctx.incident_id,
                  "agent": ctx.agent, "run": ctx.run_id})
    if ctx.incident_id:
        ctx.tg.write("MATCH (e:Evidence {id:$id}), (i:Incident {id:$inc}) CREATE (e)-[:FOR]->(i)", {"id": eid, "inc": ctx.incident_id})
    if ctx.step_id:
        ctx.tg.write("MATCH (e:Evidence {id:$id}), (s:AgentStep {id:$sid}) CREATE (s)-[:PRODUCED]->(e)", {"id": eid, "sid": ctx.step_id})
    return eid


def fetch(tg: TenantGraph, ids: list[str]) -> dict[str, dict]:
    rows = tg.read("MATCH (e:Evidence) WHERE e.id IN $ids RETURN e.id AS id, e.tool AS tool, e.node_ids AS n, "
                   "e.edge_keys AS e, e.incident_id AS inc, e.summary AS s", {"ids": ids})
    return {r["id"]: r for r in rows}
