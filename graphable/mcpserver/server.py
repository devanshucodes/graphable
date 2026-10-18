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

import anyio

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from ..core import tools as T
from ..core.db import TenantGraph
from ..core.evidence import Ctx
from ..settings import settings

mcp = FastMCP("graphable", instructions=(
    "Graph tools for incident response and company knowledge over a FalkorDB graph. "
    "Direction: (A)-[:DEPENDS_ON]->(B) means A calls B; if B fails, A is impacted. Cite evidence_id values returned by tools. "
    "Times are epoch milliseconds. Read tools never change the graph; they only write an audit Evidence record of what they returned."))

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
MEM = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)


def _tag(ctx: Context | None, name: str) -> None:
    if ctx is not None:
        object.__setattr__(ctx, "_graphable_tool", name)


WRITE_TOOLS = {"memory_note", "memory_handoff", "memory_inbox", "action_create_ticket", "action_page_oncall", "action_propose_remediation"}


def _ctx(c: Context | None, incident_id: str | None = None) -> Ctx:
    req = getattr(c.request_context, "request", None) if c else None
    h = req.headers if req is not None else {}
    # server-side tool policy: our agents only get their role's tools; unknown/external clients get the read-only tools
    tool = getattr(c, "_graphable_tool", None) if c else None
    agent = (h.get("x-agent") or "external") if req is not None else None
    if tool and agent is not None:
        allowed = T.ROLE_TOOLS.get(agent)
        if allowed is not None and tool not in allowed:
            raise PermissionError(f"agent '{agent}' may not call {tool}")
        if allowed is None and tool in WRITE_TOOLS:
            raise PermissionError(f"{tool} is reserved for Graphable's own agents")
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
async def graph_resolve(text: str, kinds: list[str] | None = None, k: int = 5, ctx: Context = None) -> dict:
    _tag(ctx, "graph_resolve")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.graph_resolve(c, text, kinds, k))


@mcp.tool(annotations=RO, description="Labels and relationship types with their direction explained in English.")
async def graph_schema(ctx: Context = None) -> dict:
    _tag(ctx, "graph_schema")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.graph_schema(c))


@mcp.tool(annotations=RO, description="Neighbors of a node. High-degree nodes return counts per relationship type first; then pass rel_types.")
async def graph_neighbors(node_id: str, rel_types: list[str] | None = None, direction: str = "both", limit: int = 25, ctx: Context = None) -> dict:
    _tag(ctx, "graph_neighbors")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.graph_neighbors(c, node_id, rel_types, direction, limit))


@mcp.tool(annotations=RO, description="Upstream services impacted if entity_id fails (walks AGAINST DEPENDS_ON), with hops, owners, entry points. max_hops 1..4.")
async def incident_blast_radius(entity_id: str, max_hops: int = 3, incident_id: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "incident_blast_radius")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.incident_blast_radius(c, entity_id, max_hops))


@mcp.tool(annotations=RO, description="Rank root-cause candidates for an incident: changes on components downstream of the alerting services, "
                                      "scored by proximity, recency, coverage, frontier, change-kind prior and history. Returns score breakdowns, "
                                      "non-change candidates, abstain flag, and one evidence_id per candidate.")
async def incident_rank_root_causes(incident_id: str, window_min: int = 120, max_hops: int = 5, top_k: int = 5, ctx: Context = None) -> dict:
    _tag(ctx, "incident_rank_root_causes")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.incident_rank_root_causes(c, incident_id, window_min, max_hops, top_k))


@mcp.tool(annotations=RO, description="Shortest paths between two entities (any allowed relationship, or rel_types). max_len 1..5.")
async def graph_explain_connection(from_id: str, to_id: str, max_len: int = 4, k: int = 3, rel_types: list[str] | None = None,
                             incident_id: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "graph_explain_connection")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.graph_explain_connection(c, from_id, to_id, max_len, k, rel_types))


@mcp.tool(annotations=RO, description="Past incidents similar to this one (symptoms, blast radius, text; after investigation also root and change kind). "
                                      "Returns who fixed them and which runbook was used.")
async def incident_similar(incident_id: str, phase: str = "triage", k: int = 5, root_entity_id: str | None = None,
                     root_kind: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "incident_similar")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.incident_similar(c, incident_id, phase, k, root_entity_id, root_kind))


@mcp.tool(annotations=RO, description="Owner team (as of time `at`, epoch ms; default now), current on-call, escalation, and ownership history with sources.")
async def org_ownership(entity_id: str, at: int | None = None, incident_id: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "org_ownership")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.org_ownership(c, entity_id, at))


@mcp.tool(annotations=RO, description="Who actually knows a component (computed from fixes, postmortems, runbooks, tickets, changes, reviews, "
                                      "help answers, with time decay). Includes bus factor and departed experts.")
async def org_experts(entity_id: str, k: int = 5, incident_id: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "org_experts")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.org_experts(c, entity_id, k))


@mcp.tool(annotations=RO, description="Runbooks (with staleness reasons), docs, decisions and lessons attached to an entity.")
async def knowledge_for_entity(node_id: str, kinds: list[str] | None = None, k: int = 3, incident_id: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "knowledge_for_entity")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.knowledge_for_entity(c, node_id, kinds, k))


@mcp.tool(annotations=RO, description="Hybrid search over docs, tickets, runbooks and chats: full-text plus vector similarity, fused by reciprocal rank. Returns ids, titles and snippets.")
async def knowledge_search(query: str, kinds: list[str] | None = None, k: int = 8, ctx: Context = None) -> dict:
    _tag(ctx, "knowledge_search")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.knowledge_search(c, query, kinds, k))


@mcp.tool(annotations=RO, description="Trace a decision (ADR key, id or text): docs, discussion threads, decider, supersedes chain, affected components.")
async def decision_trace(decision: str, ctx: Context = None) -> dict:
    _tag(ctx, "decision_trace")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.decision_trace(c, decision))


@mcp.tool(annotations=RO, description="Most central components (pagerank|betweenness). Measures importance/blast weight, NOT root-cause likelihood.")
async def graph_hubs(metric: str = "pagerank", k: int = 10, of: list[str] | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "graph_hubs")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.graph_hubs(c, metric, k, of))


@mcp.tool(annotations=RO, description="Guarded read-only Cypher escape hatch: bounded paths (<=5), max 50 rows, no writes. State your purpose.")
async def graph_cypher_readonly(cypher: str, purpose: str, params: dict | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "graph_cypher_readonly")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.graph_cypher_readonly(c, cypher, params, purpose))


@mcp.tool(annotations=MEM, description="Write a hypothesis/finding/ruled_out note to the incident blackboard. Must cite evidence ids from this incident.")
async def memory_note(incident_id: str, kind: str, claim: str, evidence_ids: list[str], confidence: float = 0.5,
                about_id: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "memory_note")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.memory_note(c, incident_id, kind, claim, evidence_ids, confidence, about_id))


@mcp.tool(annotations=MEM, description="Structured handoff to the next agent role (investigator|responder|scribe) with open questions and evidence.")
async def memory_handoff(incident_id: str, to_role: str, summary: str, open_questions: list[str], evidence_ids: list[str],
                         root_type: str | None = None, root_entity_id: str | None = None, root_change_id: str | None = None,
                         confidence: float | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "memory_handoff")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.memory_handoff(c, incident_id, to_role, summary, open_questions, evidence_ids,
                                                                   root_type, root_entity_id, root_change_id, confidence))


@mcp.tool(annotations=RO, description="Recall lessons and reflections learned from past incidents about a component.")
async def memory_recall(entity_id: str, k: int = 5, ctx: Context = None) -> dict:
    _tag(ctx, "memory_recall")
    c = _ctx(ctx)
    return await anyio.to_thread.run_sync(lambda: T.memory_recall(c, entity_id, None, k))


@mcp.tool(annotations=ACT, description="Create a ticket for a team. Server-side gate: auto-filed only if the incident has a verified path and score >= 0.6, else draft.")
async def action_create_ticket(incident_id: str, team_id: str, title: str, body: str, evidence_ids: list[str], ctx: Context = None) -> dict:
    _tag(ctx, "action_create_ticket")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.action_create_ticket(c, incident_id, team_id, title, body, evidence_ids))


@mcp.tool(annotations=MEM, description="Read the latest handoff addressed to you for this incident (summary, open questions, carried evidence ids) and mark it accepted.")
async def memory_inbox(incident_id: str, role: str | None = None, ctx: Context = None) -> dict:
    _tag(ctx, "memory_inbox")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.memory_inbox(c, incident_id, role))


@mcp.tool(annotations=ACT, description="Page a person. Server-side gate: requires human approval unless SEV1 with score >= 0.8 and a verified path.")
async def action_page_oncall(incident_id: str, person_id: str, reason: str, evidence_ids: list[str], ctx: Context = None) -> dict:
    _tag(ctx, "action_page_oncall")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.action_page_oncall(c, incident_id, person_id, reason, evidence_ids))


@mcp.tool(annotations=ACT, description="Propose a remediation (rollback|flag_off|runbook|scale|failover). Always a proposal; a human executes it.")
async def action_propose_remediation(incident_id: str, kind: str, target_id: str, rationale: str, evidence_ids: list[str], ctx: Context = None) -> dict:
    _tag(ctx, "action_propose_remediation")
    c = _ctx(ctx, incident_id)
    return await anyio.to_thread.run_sync(lambda: T.action_propose_remediation(c, incident_id, kind, target_id, rationale, evidence_ids))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    mcp.settings.host, mcp.settings.port = a.host, a.port
    # DNS-rebinding protection stays on, but the allow-list must include the Docker service name ("mcp"),
    # otherwise requests from the API container are rejected with 421 Invalid Host header.
    hosts = os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,[::1]:*,mcp:*").split(",")
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=[h.strip() for h in hosts if h.strip()],
        allowed_origins=[f"http://{h.strip()}" for h in hosts if h.strip()])
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
