"""Incident crew as a LangGraph state machine (ARCHITECTURE §8.2):

  triage -> investigate -> verify -(retry once if unsupported)-> respond -> gate -(interrupt for approval)-> close

LangGraph state is a thin cursor (ids + small results); the graph (FalkorDB) is the source of truth:
tasks are claimed with compare-and-set, handoffs/hypotheses/evidence/actions are graph nodes.
"""
from __future__ import annotations

import operator
import time
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..core.db import TenantGraph
from ..core.verifier import verify
from ..sim.player import claim_task, complete_task
from .bus import make_bus
from .policies import POLICIES, policy_mode
from .scribe import consolidate


class CrewState(TypedDict, total=False):
    tenant: str
    incident_id: str
    key: str
    alerting: list[str]
    now: int
    bus_mode: str
    policy: str
    auto_approve: bool
    triage: dict
    investigation: dict
    verification: dict
    response: dict
    gate: dict
    attempts: int
    last_run: str
    scribe: dict
    events: Annotated[list, operator.add]


def _emit_fn(config) -> Callable:
    return (config or {}).get("configurable", {}).get("emit") or (lambda e: None)


def _bus(st: CrewState, agent: str, config):
    tg = TenantGraph(st["tenant"])
    emit = _emit_fn(config)
    return tg, make_bus(st.get("bus_mode", "direct"), tg, agent, st["incident_id"], st["now"], emit, st.get("last_run"))


async def triage_node(st: CrewState, config=None) -> dict:
    tg, bus = _bus(st, "triage", config)
    claim_task(tg, f"task_{st['incident_id']}_triage", "triage", st["now"])
    _emit_fn(config)({"type": "phase", "phase": "triage", "agent": "triage"})
    out = await POLICIES[st.get("policy", "offline")]["triage"](bus, st)
    tg.write("MATCH (i:Incident {id:$i}) SET i.sev = $s", {"i": st["incident_id"], "s": out["severity"]})
    complete_task(tg, f"task_{st['incident_id']}_triage", "investigate", st["incident_id"], st["now"])
    bus.log.end(out["summary"])
    _emit_fn(config)({"type": "handoff", "from": "triage", "to": "investigator", "summary": out["summary"]})
    return {"triage": out, "last_run": bus.log.run_id, "events": [{"phase": "triage", "calls": len(bus.calls)}]}


async def investigate_node(st: CrewState, config=None) -> dict:
    tg, bus = _bus(st, "investigator", config)
    claim_task(tg, f"task_{st['incident_id']}_investigate", "investigator", st["now"])
    _emit_fn(config)({"type": "phase", "phase": "investigate", "agent": "investigator"})
    out = await POLICIES[st.get("policy", "offline")]["investigator"](bus, st)
    bus.log.end(out["claims"][0]["text"] if out.get("claims") else "no conclusion")
    return {"investigation": out, "attempts": st.get("attempts", 0) + 1, "last_run": bus.log.run_id,
            "events": [{"phase": "investigate", "calls": len(bus.calls)}]}


def verify_node(st: CrewState, config=None) -> dict:
    tg = TenantGraph(st["tenant"])
    inv = st["investigation"]
    v = verify(tg, st["incident_id"], inv.get("claims", []))
    root_ok = any(c["verified"] for c in v["claims"] if c.get("role") == "root_cause")
    g = round(float(inv.get("confidence") or 0) * (1.0 if root_ok else 0.0), 4)
    tg.write("MATCH (i:Incident {id:$i}) SET i.gate_score = $g, i.verified_path = $vp", {"i": st["incident_id"], "g": g, "vp": root_ok})
    inv = {**inv, "claims": v["claims"]}
    _emit_fn(config)({"type": "verified", "n_verified": v["n_verified"], "n_claims": v["n_claims"], "path_validity": v["path_validity"],
                      "gate_score": g, "dropped": [c["text"] for c in v["claims"] if not c["verified"]]})
    complete_task(tg, f"task_{st['incident_id']}_investigate", "respond", st["incident_id"], st["now"])
    return {"verification": {**v, "root_verified": root_ok, "gate_score": g}, "investigation": inv}


def after_verify(st: CrewState) -> str:
    if not st["verification"]["root_verified"] and st.get("attempts", 0) < 2 and st.get("policy") == "gemini":
        return "investigate"
    return "respond"


async def respond_node(st: CrewState, config=None) -> dict:
    tg, bus = _bus(st, "responder", config)
    claim_task(tg, f"task_{st['incident_id']}_respond", "responder", st["now"])
    _emit_fn(config)({"type": "phase", "phase": "respond", "agent": "responder"})
    out = await POLICIES[st.get("policy", "offline")]["responder"](bus, st)
    bus.log.end(out.get("status_update", ""))
    for a in out.get("actions", []):
        _emit_fn(config)({"type": "action", **{k: a.get(k) for k in ("kind", "action_id", "status", "duplicate")}})
    return {"response": out, "last_run": bus.log.run_id, "events": [{"phase": "respond", "calls": len(bus.calls)}]}


def gate_node(st: CrewState, config=None) -> dict:
    tg = TenantGraph(st["tenant"])
    pending = tg.read("""MATCH (a:Action)-[:FOR]->(:Incident {id:$i}) WHERE a.status = 'awaiting_approval'
        RETURN a.id AS id, a.kind AS kind, a.target AS target, a.payload AS payload""", {"i": st["incident_id"]})
    decisions = {}
    if pending:
        if st.get("auto_approve"):
            decisions = {p["id"]: "approve" for p in pending}
            by = "auto-eval"
        else:
            _emit_fn(config)({"type": "awaiting_approval", "actions": pending})
            decisions = interrupt({"awaiting_approval": pending})  # resumed via Command(resume={action_id: approve|reject})
            by = "human"
        for aid, d in decisions.items():
            tg.write("MATCH (a:Action {id:$id}) SET a.status = $s, a.decided_by = $by, a.decided_at = $now",
                     {"id": aid, "s": "approved" if d == "approve" else "rejected", "by": by, "now": st["now"]})
    return {"gate": {"pending": len(pending), "decisions": decisions}}


def close_node(st: CrewState, config=None) -> dict:
    tg = TenantGraph(st["tenant"])
    complete_task(tg, f"task_{st['incident_id']}_respond", None, st["incident_id"], st["now"])
    sc = consolidate(tg, st["incident_id"], st.get("investigation"), st.get("response"), st["now"])
    _emit_fn(config)({"type": "closed", "scribe": sc})
    return {"scribe": sc}


def build_graph(checkpointer=None):
    g = StateGraph(CrewState)
    g.add_node("triage", triage_node)
    g.add_node("investigate", investigate_node)
    g.add_node("verify", verify_node)
    g.add_node("respond", respond_node)
    g.add_node("gate", gate_node)
    g.add_node("close", close_node)
    g.add_edge(START, "triage")
    g.add_edge("triage", "investigate")
    g.add_edge("investigate", "verify")
    g.add_conditional_edges("verify", after_verify, {"investigate": "investigate", "respond": "respond"})
    g.add_edge("respond", "gate")
    g.add_edge("gate", "close")
    g.add_edge("close", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


GRAPH = build_graph()


async def run_crew(tenant: str, incident: dict, now: int, bus_mode: str = "direct", auto_approve: bool = True,
                   emit: Callable | None = None, policy: str | None = None, thread_id: str | None = None) -> dict:
    t = time.time()
    st = {"tenant": tenant, "incident_id": incident["incident_id"], "key": incident.get("key"), "alerting": incident["alerting"],
          "now": now, "bus_mode": bus_mode, "policy": policy or policy_mode(), "auto_approve": auto_approve, "attempts": 0, "events": []}
    cfg = {"configurable": {"thread_id": thread_id or f"{tenant}:{incident['incident_id']}", "emit": emit}}
    out = await GRAPH.ainvoke(st, cfg)
    out["elapsed_s"] = round(time.time() - t, 2)
    return out


async def resume_crew(tenant: str, incident_id: str, decisions: dict, emit: Callable | None = None, thread_id: str | None = None) -> dict:
    cfg = {"configurable": {"thread_id": thread_id or f"{tenant}:{incident_id}", "emit": emit}}
    return await GRAPH.ainvoke(Command(resume=decisions), cfg)
