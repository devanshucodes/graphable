"""Tool buses: how agents call tools. McpBus goes through the real MCP server (streamable HTTP);
DirectBus calls the same functions in-process (fallback / fast eval). Both log every call as an
AgentStep in the graph (P9: the audit trail is the data model) and link the evidence it produced."""
from __future__ import annotations

import inspect
import json
import time
import uuid
from typing import Any, Callable

from ..core import tools as T
from ..core.db import TenantGraph
from ..core.evidence import Ctx
from ..settings import settings


def _evidence_ids(x) -> list[str]:
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "evidence_id" and isinstance(v, str):
                out.append(v)
            else:
                out += _evidence_ids(v)
    elif isinstance(x, list):
        for v in x:
            out += _evidence_ids(v)
    return out


class RunLog:
    """AgentRun / AgentStep provenance (PROV: AgentRun INFORMED_BY previous AgentRun)."""

    def __init__(self, tg: TenantGraph, agent: str, incident_id: str | None, now: int, prev_run: str | None = None):
        self.tg, self.agent, self.incident_id, self.now = tg, agent, incident_id, now
        self.run_id = f"run_{agent}_{uuid.uuid4().hex[:8]}"
        self.last_step, self.n = None, 0
        tg.write("""MATCH (a:Agent {id:$aid}) CREATE (r:AgentRun {id:$id, agent:$agent, started_at:$now, mem:'episodic'})-[:OF]->(a)
            WITH r OPTIONAL MATCH (i:Incident {id:$inc}) FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | CREATE (r)-[:WORKED_ON]->(i))
            WITH r OPTIONAL MATCH (p:AgentRun {id:$prev}) FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (r)-[:INFORMED_BY]->(p))""",
                 {"aid": f"agent_{agent}", "id": self.run_id, "agent": agent, "now": now, "inc": incident_id, "prev": prev_run})

    def step(self, tool: str, args: dict, result: Any, ms: float) -> str:
        self.n += 1
        sid = f"{self.run_id}_s{self.n:02d}"
        evs = _evidence_ids(result)
        self.tg.write("""MATCH (r:AgentRun {id:$run}) CREATE (s:AgentStep {id:$sid, agent:$agent, tool:$tool, args:$args, n:$n, ts:$ts,
                latency_ms:$ms, n_evidence:$ne, error:$err, mem:'episodic'})-[:IN_RUN]->(r)
            WITH s OPTIONAL MATCH (p:AgentStep {id:$prev}) FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:NEXT]->(s))
            WITH s UNWIND (CASE WHEN size($evs) = 0 THEN [null] ELSE $evs END) AS eid
            OPTIONAL MATCH (e:Evidence {id:eid}) FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | CREATE (s)-[:PRODUCED]->(e))""",
                      {"run": self.run_id, "sid": sid, "agent": self.agent, "tool": tool, "args": json.dumps(args, default=str)[:800],
                       "n": self.n, "ts": self.now, "ms": round(ms, 1), "ne": len(evs), "prev": self.last_step,
                       "err": result.get("error") if isinstance(result, dict) else None, "evs": evs})
        self.last_step = sid
        return sid

    def end(self, summary: str = ""):
        self.tg.write("MATCH (r:AgentRun {id:$id}) SET r.ended_at = $now, r.n_steps = $n, r.summary = $s",
                      {"id": self.run_id, "now": self.now, "n": self.n, "s": summary[:500]})


class Bus:
    def __init__(self, tg: TenantGraph, agent: str, incident_id: str | None, now: int, emit: Callable | None = None,
                 prev_run: str | None = None):
        self.tg, self.agent, self.incident_id, self.now = tg, agent, incident_id, now
        self.log = RunLog(tg, agent, incident_id, now, prev_run)
        self.emit = emit or (lambda e: None)
        self.calls = []

    async def call(self, name: str, **args) -> dict:
        if name not in T.ROLE_TOOLS.get(self.agent, T.TOOLS) and self.agent in T.ROLE_TOOLS:
            raise PermissionError(f"{self.agent} may not call {name}")
        t = time.time()
        try:
            res = await self._call(name, args)
        except Exception as e:  # tool errors are returned to the agent, not raised (MCP isError semantics)
            res = {"error": str(e)[:300]}
        ms = (time.time() - t) * 1000
        sid = self.log.step(name, args, res, ms)
        self.calls.append({"tool": name, "args": args, "ms": round(ms, 1), "step_id": sid})
        self.emit({"type": "tool_call", "agent": self.agent, "tool": name, "args": args, "ms": round(ms, 1),
                   "evidence_ids": _evidence_ids(res), "error": res.get("error") if isinstance(res, dict) else None})
        return res


class DirectBus(Bus):
    mode = "direct"

    async def _call(self, name, args):
        ctx = Ctx(self.tg, now=self.now, incident_id=args.get("incident_id") or self.incident_id, agent=self.agent, run_id=self.log.run_id)
        fn = T.TOOLS[name]
        if name == "memory_recall":
            return fn(ctx, args.get("entity_id"), None, args.get("k", 5))
        params = inspect.signature(fn).parameters
        return fn(ctx, **{k: v for k, v in args.items() if k in params})


class McpBus(Bus):
    """Real MCP: tools discovered and invoked over streamable HTTP via langchain-mcp-adapters."""
    mode = "mcp"

    def __init__(self, *a, token: str, url: str | None = None, **kw):
        super().__init__(*a, **kw)
        self.token, self.url = token, url or settings.mcp_url
        self._tools = None

    async def tools(self):
        if self._tools is None:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            client = MultiServerMCPClient({"graphable": {"transport": "streamable_http", "url": self.url, "headers": {
                "Authorization": f"Bearer {self.token}", "X-Sim-Now": str(self.now), "X-Agent": self.agent, "X-Run-Id": self.log.run_id,
                **({"X-Incident-Id": self.incident_id} if self.incident_id else {})}}})
            allowed = set(T.ROLE_TOOLS.get(self.agent, T.TOOLS))
            self._tools = {t.name: t for t in await client.get_tools() if t.name in allowed}
        return self._tools

    async def _call(self, name, args):
        tools = await self.tools()
        out = await tools[name].ainvoke(args)
        text = out[0]["text"] if isinstance(out, list) and out and isinstance(out[0], dict) else str(out)
        if text.startswith("Error executing tool"):
            return {"error": text}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}


def make_bus(mode: str, tg: TenantGraph, agent: str, incident_id: str | None, now: int, emit=None, prev_run=None, token=None):
    if mode == "mcp":
        return McpBus(tg, agent, incident_id, now, emit, prev_run, token=token or _token_for(tg.tenant))
    return DirectBus(tg, agent, incident_id, now, emit, prev_run)


def _token_for(tenant: str) -> str:
    for k, t in settings.tenant_keys.items():
        if t == tenant:
            return k
    return f"eval:{tenant}"
