"""Agent policies. Each role is a fixed procedure over MCP tools (Flow-of-Action / RCACopilot style):
the algorithm proposes, the agent decides and explains. Two implementations per role:

  * offline  - deterministic procedure (no LLM). Used without GEMINI_API_KEY, and as the eval baseline.
  * gemini   - LangGraph ReAct agent over the same role-filtered MCP tools, with a structured output
               contract. Falls back to the offline procedure on any LLM failure.
Both return the same dicts, so the crew graph, verifier and gate are identical in either mode.
"""
from __future__ import annotations

import asyncio

import json
import math
import re

from pydantic import BaseModel, Field

from ..settings import settings

FAULT_BY_ALERT = [("Cert", "cert_expiry"), ("TLS", "cert_expiry"), ("UpstreamErrors", "external_outage"),
                  ("PodRestarts", "node_failure"), ("DataValidation", "data_issue"), ("Saturation", "capacity"),
                  ("QueueLag", "capacity")]
REMEDIATION = {"cert_expiry": "renew_cert", "external_outage": "failover_vendor", "node_failure": "failover_zone",
               "data_issue": "data_fix", "capacity": "scale_out", "unknown": "runbook"}


def _sev(blasts: list[dict], n_alerting: int) -> int:
    entry = {e for b in blasts for e in b.get("entry_points_hit", [])}
    cust = max([b.get("customer_facing_hit", 0) for b in blasts] or [0])
    if entry and n_alerting >= 5 and cust >= 12:
        return 1
    if entry and (n_alerting >= 3 or cust >= 8):
        return 2
    if cust >= 3 or entry:
        return 3
    return 4 if n_alerting >= 2 else 5


# ====================================================================== offline procedures
async def triage_offline(bus, st: dict) -> dict:
    inc, alerting = st["incident_id"], st["alerting"]
    # FalkorDB PageRank over DEPENDS_ON decides where Triage looks first: the most depended-on alerting services
    # have the widest blast radius, so they set severity and get the first page.
    hubs = await bus.call("graph_hubs", metric="pagerank", k=5, of=alerting, incident_id=inc)
    ranked_all = sorted(hubs.get("ranked") or [], key=lambda r: r["rank"]) if "error" not in hubs else []
    order = [r["id"] for r in ranked_all] + [a for a in alerting if a not in {r["id"] for r in ranked_all}]
    hub_ranked = ranked_all[:3]
    blasts = []
    for e in order[:3]:
        b = await bus.call("incident_blast_radius", entity_id=e, max_hops=2, incident_id=inc)
        if "error" not in b:
            blasts.append(b)
    own = await bus.call("org_ownership", entity_id=order[0], incident_id=inc)
    sim = await bus.call("incident_similar", incident_id=inc, phase="triage", k=3)
    sev = _sev(blasts, len(alerting))
    ev = [b["evidence_id"] for b in blasts if b.get("evidence_id")]
    entry = sorted({e for b in blasts for e in b.get("entry_points_hit", [])})
    hub_txt = (f"Most central alerting component by PageRank: {hub_ranked[0]['name']} (#{hub_ranked[0]['rank']} of {hubs.get('n_components')})."
               if hub_ranked else "")
    if ev:
        await bus.call("memory_note", incident_id=inc, kind="finding", evidence_ids=ev + ([hubs["evidence_id"]] if hubs.get("evidence_id") else []),
                       confidence=0.9,
                       claim=f"SEV{sev}: {len(alerting)} components alerting; upstream impact reaches entry points {entry or 'none'}. {hub_txt}".strip())
    top_sim = (sim.get("similar") or [])[:3]
    summary = (f"SEV{sev}. Alerting: {', '.join(alerting[:6])}. Paged team: {own.get('owner', {}).get('team') if own.get('owner') else '?'}. "
               + (f"Looks like {top_sim[0]['key']} ({top_sim[0]['similarity']}). " if top_sim else "No close past incident. ") + hub_txt).strip()
    await bus.call("memory_handoff", incident_id=inc, to_role="investigator", summary=summary,
                   open_questions=["Which change or component is the root cause?", "Is this a change-induced incident?"],
                   evidence_ids=ev + [s["evidence_id"] for s in top_sim if s.get("evidence_id")] + ([hubs["evidence_id"]] if hubs.get("evidence_id") else []))
    return {"severity": sev, "entry_points": entry, "similar": top_sim, "paged_owner": own.get("owner"), "summary": summary,
            "evidence_ids": ev, "hubs": [{"id": r["id"], "name": r["name"], "rank": r["rank"], "score": r["score"]} for r in hub_ranked],
            "n_components": hubs.get("n_components")}


def _path(ids: list[str]) -> str:
    return " -> ".join(i.split("_", 1)[1] if i.startswith(("svc_", "ds_")) else i for i in ids)


def _fault_type(alert_names: list[str], entity: str, engine: str | None = None) -> str:
    if (engine or "").startswith("external"):  # third-party dependency (e.g. a payment provider), read from the graph
        return "external_outage"
    for k, f in FAULT_BY_ALERT:
        if any(k in a for a in alert_names):
            return f
    return "unknown"


async def investigate_offline(bus, st: dict) -> dict:
    inc = st["incident_id"]
    rc = await bus.call("incident_rank_root_causes", incident_id=inc, top_k=5)
    if "error" in rc:
        return {"error": rc["error"], "claims": [], "confidence": 0.0, "abstain": True}
    claims, notes = [], []
    cands, ncs = rc.get("candidates", []), rc.get("non_change_candidates", [])
    if cands and not rc["abstain"]:
        top = cands[0]
        root = {"type": "change", "change_id": top["change_id"], "entity_id": top["entity_id"], "entity_name": top["entity_name"],
                "change_kind": top["change_kind"], "summary": top["change_summary"], "score": top["score"], "author_id": top["author_id"],
                "path": top["path"], "breakdown": top["breakdown"], "evidence_id": top["evidence_id"]}
        what = (top["change_summary"] or "").split(": ", 1)[-1]
        claims.append({"role": "root_cause", "text": f"Root cause: the {top['change_kind']} change on {top['entity_name']} ({what}), "
                       f"{round(top['minutes_before_detection'])} min before detection; impact path {_path(top['path'])}.",
                       "evidence_ids": [top["evidence_id"]], "entity_ids": [top["change_id"], top["entity_id"]]})
        if len(cands) > 1 and top["score"] - cands[1]["score"] >= 0.08:
            notes.append(("ruled_out", f"Ruled out {cands[1]['change_id']} on {cands[1]['entity_name']} (score {cands[1]['score']} vs {top['score']})",
                          [cands[1]["evidence_id"]], cands[1]["change_id"]))
        sim = await bus.call("incident_similar", incident_id=inc, phase="post", root_entity_id=top["entity_id"], root_kind=top["change_kind"], k=3)
    else:
        nc = ncs[0] if ncs else None
        if nc is None:
            return {"claims": [], "confidence": 0.0, "abstain": True, "root": None, "candidates": cands}
        ftype = _fault_type(rc.get("alert_names", []), nc["entity_id"], nc.get("engine"))
        root = {"type": "non_change", "entity_id": nc["entity_id"], "entity_name": nc["entity_name"], "fault_type": ftype,
                "score": nc["score"], "path": nc["path"], "breakdown": nc["breakdown"], "evidence_id": nc["evidence_id"]}
        why = (f"the best-scoring change scored {cands[0]['score']:.2f}, below the {rc['theta']} threshold" if cands
               else "no change landed in the window")
        label = {"cert_expiry": "certificate expiry", "external_outage": "third-party outage"}.get(ftype, ftype.replace("_", " "))
        claims.append({"role": "root_cause", "text": f"Not change-induced: {why}. Most likely origin: {nc['entity_name']} ({label}); "
                       f"impact path {_path(nc['path'])}.",
                       "evidence_ids": [nc["evidence_id"]], "entity_ids": [nc["entity_id"]]})
        sim = await bus.call("incident_similar", incident_id=inc, phase="post", root_entity_id=nc["entity_id"], k=3)
    similar = [s for s in (sim.get("similar") or []) if s["similarity"] >= 0.45][:3]
    if similar:
        # among near-equal matches (within 0.03), prefer the most recent one fixed by someone still at the company
        near = [x for x in similar if x["similarity"] >= similar[0]["similarity"] - 0.03]
        s = max(near, key=lambda x: (any(p.get("active") for p in x["fixed_by"]), x["started_at"]))
        similar = [s] + [x for x in similar if x is not s]
        fixers = [p["id"] for p in s["fixed_by"]]
        claims.append({"role": "memory", "text": f"Matches past incident {s['key']} ({round(s['similarity'] * 100)}% similar), fixed by "
                       f"{', '.join(p['name'] for p in s['fixed_by']) or 'unknown'}" +
                       (f" using {s['runbooks'][0]['key']}." if s["runbooks"] else "."),
                       "evidence_ids": [s["evidence_id"]], "entity_ids": [s["incident_id"]] + fixers})
    kn = await bus.call("knowledge_for_entity", node_id=root["entity_id"], kinds=["Runbook", "Lesson"], incident_id=inc)
    rbs = [r for r in kn.get("runbooks", []) if r["status"] == "active"]
    sim_rb = next((rb["key"] for s in similar for rb in s["runbooks"]), None)
    pick = next((r for r in rbs if r["key"] == sim_rb), None) or next((r for r in rbs if not r["stale"]), None) or (rbs[0] if rbs else None)
    if pick and kn.get("evidence_id"):
        claims.append({"role": "runbook", "text": f"Runbook {pick['key']}" + (f" (STALE: {'; '.join(pick['stale_reasons'])})" if pick["stale"] else ""),
                       "evidence_ids": [kn["evidence_id"]], "entity_ids": [pick["id"]]})
    hyp = await bus.call("memory_note", incident_id=inc, kind="hypothesis", claim=claims[0]["text"], evidence_ids=claims[0]["evidence_ids"],
                         confidence=root["score"], about_id=root.get("change_id") or root["entity_id"])
    for kind, text, ev, about in notes:
        await bus.call("memory_note", incident_id=inc, kind=kind, claim=text, evidence_ids=ev, confidence=0.2, about_id=about)
    await bus.call("memory_handoff", incident_id=inc, to_role="responder", summary=claims[0]["text"],
                   open_questions=["Who owns the root component and who is on call?", "Which remediation is safest?"],
                   evidence_ids=[e for c in claims for e in c["evidence_ids"]],
                   root_type=root["type"], root_entity_id=root["entity_id"], root_change_id=root.get("change_id"), confidence=root["score"])
    nw = rc.get("newest_change")
    return {"root": root, "claims": claims, "confidence": root["score"], "abstain": root["type"] == "non_change",
            "newest_change": {**nw, "is_root": nw["change_id"] == root.get("change_id")} if nw else None,
            "similar": similar, "runbook": pick, "hypothesis_id": hyp.get("hypothesis_id"),
            "candidates": [{k: c.get(k) for k in ("change_id", "entity_id", "entity_name", "score", "breakdown", "change_summary",
                                                   "minutes_before_detection", "evidence_id", "path")} for c in cands],
            "non_change_candidates": [{k: c.get(k) for k in ("entity_id", "entity_name", "score", "evidence_id")} for c in ncs]}


async def respond_offline(bus, st: dict) -> dict:
    inc, inv = st["incident_id"], st["investigation"]
    root = inv.get("root")
    hand = ((st.get("inbox") or {}).get("root")) or {}
    if hand.get("root_entity_id") and root and hand["root_entity_id"] != root.get("entity_id"):
        # the graph handoff is the source of truth for what the Investigator concluded
        root = {**root, "entity_id": hand["root_entity_id"], "change_id": hand.get("root_change_id"), "type": hand.get("root_type") or root["type"]}
    if not root:
        return {"actions": [], "status_update": "Investigating; no root cause identified yet."}
    e = root["entity_id"]
    own = await bus.call("org_ownership", entity_id=e, incident_id=inc)
    ex = await bus.call("org_experts", entity_id=e, k=3, incident_id=inc)
    mem = await bus.call("memory_recall", entity_id=e, k=3)
    owner = own.get("owner") or {}
    oncall = (own.get("oncall") or [None])[0]
    experts = ex.get("experts", [])
    ev_root = [c["evidence_ids"][0] for c in inv["claims"] if c["role"] == "root_cause" and c.get("verified", True)]
    if not ev_root and st.get("inbox"):  # fall back to the evidence the Investigator handed over through the graph
        ev_root = (st["inbox"].get("evidence_ids") or [])[:2]
    ev_org = [x for x in (own.get("evidence_id"), ex.get("evidence_id")) if x]
    actions = []
    rb = inv.get("runbook")
    if owner:
        actions.append(("ticket", await bus.call("action_create_ticket", incident_id=inc, team_id=owner["team_id"],
                                                 title=f"[{st.get('key')}] {root['entity_name']}: " + (root.get("summary") or root.get("fault_type", "")),
                                                 body=inv["claims"][0]["text"], evidence_ids=ev_root + ev_org)))
    if oncall:
        actions.append(("page", await bus.call("action_page_oncall", incident_id=inc, person_id=oncall["id"],
                                               reason=f"Root cause on {root['entity_name']} (owned by {owner.get('team')})", evidence_ids=ev_root + ev_org)))
    if root["type"] == "change":
        actions.append(("remediation", await bus.call("action_propose_remediation", incident_id=inc, kind="rollback", target_id=root["change_id"],
                                                      rationale=f"Roll back {root['change_id']}" + (f"; then follow {rb['key']}" if rb else ""),
                                                      evidence_ids=ev_root)))
    else:
        actions.append(("remediation", await bus.call("action_propose_remediation", incident_id=inc, kind=REMEDIATION.get(root.get("fault_type"), "runbook"),
                                                      target_id=e, rationale=f"{root.get('fault_type')} on {root['entity_name']}" +
                                                      (f"; follow {rb['key']}" if rb else ""), evidence_ids=ev_root)))
    consult = [x for x in experts if x["active"] and (not oncall or x["id"] != oncall["id"])][:1]
    update = (f"[{st.get('key')}] SEV{st['triage']['severity']} — {inv['claims'][0]['text']} Owner: {owner.get('team')}. "
              f"On-call: {oncall['name'] if oncall else 'n/a'}." + (f" Consulting {consult[0]['name']} (top expert)." if consult else "") +
              (f" Runbook: {rb['key']}." if rb else ""))
    if ex.get("bus_factor") == 1 or ex.get("departed_experts"):
        dep = ex.get("departed_experts") or []
        update += f" Knowledge risk: bus factor {ex.get('bus_factor')}" + (f"; {dep[0]['name']} (former expert) has left." if dep else ".")
    return {"owner": owner, "oncall": oncall, "experts": experts[:3], "bus_factor": ex.get("bus_factor"),
            "departed_experts": ex.get("departed_experts", []), "lessons": mem.get("memories", []),
            "actions": [{"kind": k, **v} for k, v in actions], "status_update": update}


# ====================================================================== Gemini (LLM) agents
class Claim(BaseModel):
    text: str = Field(description="one factual sentence")
    evidence_ids: list[str] = Field(description="evidence_id values returned by tools that support the claim")
    entity_ids: list[str] = Field(default_factory=list, description="graph ids named in the claim (change id, component id, incident id...)")


class InvestigationOut(BaseModel):
    decision: str = Field(description="'accept' the top-ranked candidate, 'override' with another listed candidate, or 'non_change'")
    root_change_id: str | None = Field(default=None, description="chosen change id (for accept/override)")
    root_entity_id: str = Field(description="component id of the root cause")
    fault_type: str | None = Field(default=None, description="for non_change: cert_expiry|capacity|external_outage|node_failure|data_issue|unknown")
    rationale: list[Claim] = Field(description="2-4 claims, each citing evidence ids")


INVESTIGATOR_PROMPT = """You are the Investigator in an incident-response crew working over a company knowledge graph.
A deterministic ranker has already scored root-cause candidates (coverage = how many alerting services it explains,
minimal explanation, learned change-type risk, recency, history). Your job is to REVIEW, not to re-guess:
1. Read the candidates, scores and evidence below.
2. Optionally check the top 2 with tools (graph_explain_connection with rel_types ["DEPENDS_ON"], graph_neighbors,
   incident_similar, knowledge_for_entity, graph_cypher_readonly). At most 6 tool calls.
3. Decide: 'accept' the top candidate, 'override' with another LISTED candidate only if tool evidence clearly contradicts
   the top one, or 'non_change' if no change explains the alerts.
Rules: cite only evidence_id values that appear in this message or in tool results; never invent ids.
Direction: (A)-[:DEPENDS_ON]->(B) means A calls B; if B fails, A is impacted."""


def _wrap_tools(bus, mcp_tools: dict, budget: int = 6):
    """Expose MCP tools to the LLM *through the bus* so every LLM tool call is role-filtered and logged as an AgentStep.
    The tool budget is enforced in code (prompts alone do not stop loops)."""
    from langchain_core.tools import StructuredTool
    out, used = [], {"n": 0}
    for name, t in mcp_tools.items():
        if name.startswith(("memory_", "action_")):
            continue  # the LLM reviews; writes stay in the deterministic procedure

        async def _call(_name=name, **kw):
            if used["n"] >= budget:
                return json.dumps({"error": f"tool budget of {budget} calls exhausted: stop calling tools and give your final decision now"})
            used["n"] += 1
            return json.dumps(await bus.call(_name, **kw), default=str)[:6000]
        out.append(StructuredTool.from_function(coroutine=_call, name=name, description=t.description, args_schema=t.args_schema))
    return out


async def investigate_gemini(bus, st: dict) -> dict:  # pragma: no cover - needs API key
    base = await investigate_offline(bus, st)  # deterministic proposal (+ memory writes, handoff)
    # show the graph's answer right away; Gemini's review follows and may only mark it contested
    bus.emit({"type": "provisional", "investigation": {k: base.get(k) for k in
                                                        ("root", "claims", "candidates", "similar", "runbook", "newest_change", "confidence")}})
    if not hasattr(bus, "tools"):
        base["llm"] = {"skipped": "Gemini review requires the MCP bus"}
        return base
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langgraph.prebuilt import create_react_agent
        tools = _wrap_tools(bus, await bus.tools())
        llm = ChatGoogleGenerativeAI(model=settings.gemini_model_pro, temperature=0, google_api_key=settings.gemini_api_key,
                                     timeout=120, max_retries=1)
        agent = create_react_agent(llm, tools, prompt=INVESTIGATOR_PROMPT, response_format=InvestigationOut)
        hand = st.get("inbox") or {}  # Triage's handoff, read from the graph
        brief = {"incident_id": st["incident_id"], "triage": hand.get("summary") or st["triage"]["summary"],
                 "open_questions": hand.get("open_questions", []), "handoff_evidence_ids": hand.get("evidence_ids", []),
                 "change_candidates": base.get("candidates", [])[:5],
                 "non_change_candidates": base.get("non_change_candidates", [])[:3],
                 "deterministic_choice": {k: (base.get("root") or {}).get(k) for k in ("type", "change_id", "entity_id", "score", "evidence_id")},
                 "evidence_by_claim": [{"text": c["text"], "evidence_ids": c["evidence_ids"]} for c in base.get("claims", [])],
                 "similar_past_incidents": [{k: s[k] for k in ("key", "similarity", "root_cause", "fixed_by", "evidence_id")} for s in base.get("similar", [])]}
        # bounded: if Gemini is slow or down, the graph-based conclusion stands on its own (caught below)
        res = await asyncio.wait_for(agent.ainvoke({"messages": [("user", json.dumps(brief, default=str))]}, {"recursion_limit": 40}), 180)
        out: InvestigationOut = res["structured_response"]
        root = base.get("root") or {}
        agree = (out.decision == "accept" and (not out.root_change_id or out.root_change_id == root.get("change_id"))) or \
                (out.decision == "non_change" and root.get("type") == "non_change")
        llm = {"decision": out.decision, "root_change_id": out.root_change_id, "root_entity_id": out.root_entity_id,
               "agrees": agree, "applied": False}
        if not agree:
            # Measured on 40 test incidents: letting the LLM override fixed 1 and broke 3 (look-alike traps).
            # So dissent is treated as uncertainty: the deterministic answer stands, the incident is marked contested,
            # confidence is halved (no auto-ticket, human sees both views).
            base["contested"] = {"llm_decision": out.decision, "llm_root_change_id": out.root_change_id,
                                 "llm_root_entity_id": out.root_entity_id, "fault_type": out.fault_type}
            base["confidence"] = round(float(base.get("confidence") or 0) * 0.5, 4)
        def _toks(t):
            return set(re.findall(r"[a-z0-9_]+", t.lower()))
        seen = [_toks(c["text"]) for c in base.get("claims", [])]
        rationale = []
        for c in out.rationale:  # keep only rationale that adds something beyond the deterministic claims
            tk = _toks(c.text)
            if any(len(tk & sv) / max(1, len(tk)) >= 0.7 for sv in seen):
                continue
            seen.append(tk)
            rationale.append(c.model_dump() | {"role": "llm_rationale"})
        base["claims"] = base["claims"] + rationale
        base["llm"] = llm
        return base
    except Exception as e:
        base["llm"] = {"error": str(e)[:300]}
        return base


POLICIES = {
    "offline": {"triage": triage_offline, "investigator": investigate_offline, "responder": respond_offline},
    "gemini": {"triage": triage_offline, "investigator": investigate_gemini, "responder": respond_offline},
}


def policy_mode() -> str:
    return "gemini" if settings.llm_enabled else "offline"
