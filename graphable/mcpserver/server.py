"""Graphable MCP server (FastMCP, streamable HTTP). Domain tools over FalkorDB (ARCHITECTURE §4.2, §7).

Tenant comes from the bearer token (never from tool arguments). Optional headers set by our own
agent runtime: X-Sim-Now (epoch ms, scenario clock), X-Agent, X-Run-Id, X-Step-Id.

  python -m graphable.mcpserver.server --port 8765
External clients (Claude Desktop, Cursor, ...): http://host:8765/mcp with  Authorization: Bearer <tenant key>
"""
from __future__ import annotations

import argparse
import os
import time

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ..core import tools as T
from ..core.db import TenantGraph
from ..core.evidence import Ctx
from ..settings import settings

mcp = FastMCP("graphable", instructions=(
    "Graph tools for incident response and company knowledge over a FalkorDB graph. "
    "Direction: (A)-[:DEPENDS_ON]->(B) means A calls B; if B fails, A is impacted. Cite evidence_id values returned by tools. "
    "Times are epoch milliseconds."))

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
MEM = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)


def _ctx(c: Context | None, incident_id: str | None = None) -> Ctx:
    req = getattr(c.request_context, "request", None) if c else None
    h = req.headers if req is not None else {}
    auth = h.get("authorization", "")
    token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else None
    tenant = settings.tenant_keys.get(token or "", None)
    # dev/eval only: ephemeral scenario tenants (never enabled in production)
    if tenant is None and token and token.startswith("eval:") and os.getenv("GRAPHABLE_ALLOW_EVAL_TOKENS") == "1":
        cand = token[5:]
        tenant = cand if cand.startswith(("eval_", "demo_")) else None
    if tenant is None:
        # stdio / local dev fallback: single default tenant from env
        tenant = os.getenv("GRAPHABLE_TENANT") if req is None else None
    if not tenant:
        raise PermissionError("unauthorized: missing or invalid bearer token")
    now = int(h.get("x-sim-now") or time.time() * 1000)
    # our runtime binds each MCP session to one incident; explicit tool args still take precedence
    incident_id = incident_id or h.get("x-incident-id") or None
    return Ctx(TenantGraph(tenant), now=now, incident_id=incident_id, agent=h.get("x-agent") or "external",
               run_id=h.get("x-run-id"), step_id=h.get("x-step-id"))


@mcp.tool(annotations=RO, description="Resolve free text (service alias, @handle, INC-/RB-/ADR- key, team) to graph ids.")
def graph_resolve(text: str, kinds: list[str] | None = None, k: int = 5, ctx: Context = None) -> dict:
    return T.graph_resolve(_ctx(ctx), text, kinds, k)


@mcp.tool(annotations=RO, description="Labels and relationship types with their direction explained in English.")
def graph_schema(ctx: Context = None) -> dict:
    return T.graph_schema(_ctx(ctx))


@mcp.tool(annotations=RO, description="Neighbors of a node. High-degree nodes return counts per relationship type first; then pass rel_types.")
def graph_neighbors(node_id: str, rel_types: list[str] | None = None, direction: str = "both", limit: int = 25, ctx: Context = None) -> dict:
    return T.graph_neighbors(_ctx(ctx), node_id, rel_types, direction, limit)


@mcp.tool(annotations=RO, description="Upstream services impacted if entity_id fails (walks AGAINST DEPENDS_ON), with hops, owners, entry points. max_hops 1..4.")
def incident_blast_radius(entity_id: str, max_hops: int = 3, incident_id: str | None = None, ctx: Context = None) -> dict:
    return T.incident_blast_radius(_ctx(ctx, incident_id), entity_id, max_hops)


@mcp.tool(annotations=RO, description="Rank root-cause candidates for an incident: changes on components downstream of the alerting services, "
                                      "scored by proximity, recency, coverage, frontier, change-kind prior and history. Returns score breakdowns, "
                                      "non-change candidates, abstain flag, and one evidence_id per candidate.")
def incident_rank_root_causes(incident_id: str, window_min: int = 120, max_hops: int = 5, top_k: int = 5, ctx: Context = None) -> dict:
    return T.incident_rank_root_causes(_ctx(ctx, incident_id), incident_id, window_min, max_hops, top_k)


@mcp.tool(annotations=RO, description="Shortest paths between two entities (any allowed relationship, or rel_types). max_len 1..5.")
def graph_explain_connection(from_id: str, to_id: str, max_len: int = 4, k: int = 3, rel_types: list[str] | None = None,
                             incident_id: str | None = None, ctx: Context = None) -> dict:
    return T.graph_explain_connection(_ctx(ctx, incident_id), from_id, to_id, max_len, k, rel_types)


@mcp.tool(annotations=RO, description="Past incidents similar to this one (symptoms, blast radius, text; after investigation also root and change kind). "
                                      "Returns who fixed them and which runbook was used.")
def incident_similar(incident_id: str, phase: str = "triage", k: int = 5, root_entity_id: str | None = None,
                     root_kind: str | None = None, ctx: Context = None) -> dict:
    return T.incident_similar(_ctx(ctx, incident_id), incident_id, phase, k, root_entity_id, root_kind)


@mcp.tool(annotations=RO, description="Owner team (as of time `at`, epoch ms; default now), current on-call, escalation, and ownership history with sources.")
def org_ownership(entity_id: str, at: int | None = None, incident_id: str | None = None, ctx: Context = None) -> dict:
    return T.org_ownership(_ctx(ctx, incident_id), entity_id, at)


@mcp.tool(annotations=RO, description="Who actually knows a component (computed from fixes, postmortems, runbooks, tickets, changes, reviews, "
                                      "help answers, with time decay). Includes bus factor and departed experts.")
def org_experts(entity_id: str, k: int = 5, incident_id: str | None = None, ctx: Context = None) -> dict:
    return T.org_experts(_ctx(ctx, incident_id), entity_id, k)


@mcp.tool(annotations=RO, description="Runbooks (with staleness reasons), docs, decisions and lessons attached to an entity.")
def knowledge_for_entity(node_id: str, kinds: list[str] | None = None, k: int = 3, incident_id: str | None = None, ctx: Context = None) -> dict:
    return T.knowledge_for_entity(_ctx(ctx, incident_id), node_id, kinds, k)


@mcp.tool(annotations=RO, description="Full-text search over docs, tickets, runbooks and chat messages.")
def knowledge_search(query: str, kinds: list[str] | None = None, k: int = 8, ctx: Context = None) -> dict:
    return T.knowledge_search(_ctx(ctx), query, kinds, k)


@mcp.tool(annotations=RO, description="Trace a decision (ADR key, id or text): docs, discussion threads, decider, supersedes chain, affected components.")
def decision_trace(decision: str, ctx: Context = None) -> dict:
    return T.decision_trace(_ctx(ctx), decision)


@mcp.tool(annotations=RO, description="Most central components (pagerank|betweenness). Measures importance/blast weight, NOT root-cause likelihood.")
def graph_hubs(metric: str = "pagerank", k: int = 10, ctx: Context = None) -> dict:
    return T.graph_hubs(_ctx(ctx), metric, k)


@mcp.tool(annotations=RO, description="Guarded read-only Cypher escape hatch: bounded paths (<=5), max 50 rows, no writes. State your purpose.")
def graph_cypher_readonly(cypher: str, purpose: str, params: dict | None = None, ctx: Context = None) -> dict:
    return T.graph_cypher_readonly(_ctx(ctx), cypher, params, purpose)


@mcp.tool(annotations=MEM, description="Write a hypothesis/finding/ruled_out note to the incident blackboard. Must cite evidence ids from this incident.")
def memory_note(incident_id: str, kind: str, claim: str, evidence_ids: list[str], confidence: float = 0.5,
                about_id: str | None = None, ctx: Context = None) -> dict:
    return T.memory_note(_ctx(ctx, incident_id), incident_id, kind, claim, evidence_ids, confidence, about_id)


@mcp.tool(annotations=MEM, description="Structured handoff to the next agent role (investigator|responder|scribe) with open questions and evidence.")
def memory_handoff(incident_id: str, to_role: str, summary: str, open_questions: list[str], evidence_ids: list[str], ctx: Context = None) -> dict:
    return T.memory_handoff(_ctx(ctx, incident_id), incident_id, to_role, summary, open_questions, evidence_ids)


@mcp.tool(annotations=RO, description="Recall lessons and reflections learned from past incidents about a component.")
def memory_recall(entity_id: str, k: int = 5, ctx: Context = None) -> dict:
    return T.memory_recall(_ctx(ctx), entity_id, None, k)


@mcp.tool(annotations=ACT, description="Create a ticket for a team. Server-side gate: auto-filed only if the incident has a verified path and score >= 0.6, else draft.")
def action_create_ticket(incident_id: str, team_id: str, title: str, body: str, evidence_ids: list[str], ctx: Context = None) -> dict:
    return T.action_create_ticket(_ctx(ctx, incident_id), incident_id, team_id, title, body, evidence_ids)


@mcp.tool(annotations=ACT, description="Page a person. Server-side gate: requires human approval unless SEV1 with score >= 0.8 and a verified path.")
def action_page_oncall(incident_id: str, person_id: str, reason: str, evidence_ids: list[str], ctx: Context = None) -> dict:
    return T.action_page_oncall(_ctx(ctx, incident_id), incident_id, person_id, reason, evidence_ids)


@mcp.tool(annotations=ACT, description="Propose a remediation (rollback|flag_off|runbook|scale|failover). Always a proposal; a human executes it.")
def action_propose_remediation(incident_id: str, kind: str, target_id: str, rationale: str, evidence_ids: list[str], ctx: Context = None) -> dict:
    return T.action_propose_remediation(_ctx(ctx, incident_id), incident_id, kind, target_id, rationale, evidence_ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    mcp.settings.host, mcp.settings.port = a.host, a.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
