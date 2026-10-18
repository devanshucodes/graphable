"""System-level evaluation (ARCHITECTURE §14): full crew over MCP, concurrency, tenant isolation,
knowledge-update probes, Brain Q&A accuracy, and the learning curve.

  python -m graphable.evals.system --bus mcp        # MCP server must run with GRAPHABLE_ALLOW_EVAL_TOKENS=1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from collections import Counter

from ..agents.brain import ask
from ..agents.crew import run_crew
from ..agents.scribe import confirm
from ..core import tools as T
from ..core.db import TenantGraph
from ..core.evidence import Ctx
from ..settings import ROOT, settings
from ..sim.player import claim_task, drop_tenant, fresh_tenant, intake, play
from .rca import scenarios

OUT = ROOT / "evals_out"
MIN = 60_000


def _tok(tenant):
    return f"eval:{tenant}"


async def crew_eval(bus: str, policy: str = "offline", parallel: int = 4) -> dict:
    sem = asyncio.Semaphore(parallel)

    async def one(sc):
        async with sem:
            return await _crew_one(sc, bus, policy)
    rows = await asyncio.gather(*(one(sc) for sc in scenarios("test")))
    return _crew_summary(rows, bus, policy)


async def _crew_one(sc, bus, policy):
        et = f"eval_c{policy[:3]}{sc['id'].lower().replace('-', '')}"
        tg = await asyncio.to_thread(fresh_tenant, "acme", et)
        try:
            await asyncio.to_thread(play, tg, sc)
            inc = await asyncio.to_thread(intake, tg, sc["t0"], f"inc_{et}", f"INC-{sc['id']}")
            if not inc:
                return {"id": sc["id"], "error": "no incident"}
            events = []
            if bus == "mcp":
                settings.tenant_keys[_tok(et)] = et  # client side only; server accepts eval: tokens in dev mode
            out = await run_crew(et, inc, inc["detected_at"] + 5 * MIN, bus_mode=bus, emit=events.append, policy=policy)
            tr, inv, resp, v = sc["truth"], out["investigation"], out["response"], out["verification"]
            root = inv.get("root") or {}
            if tr["cause_type"] == "change":
                correct = root.get("change_id") in tr["root_change_ids"]
            else:
                correct = root.get("type") == "non_change" and root.get("entity_id") in tr["root_entity_ids"]
            acts = {a["kind"]: a.get("status") for a in resp.get("actions", [])}
            owner = (resp.get("owner") or {}).get("team_id")
            llm = inv.get("llm") or {}
            return {"id": sc["id"], "type": sc["type"], "correct": correct, "path_validity": v["path_validity"],
                    "claims_verified": v["n_verified"] / max(1, v["n_claims"]), "routed_to_correct_team": owner == tr["correct_team_id"],
                    "ticket": acts.get("ticket"), "page": acts.get("page"), "gate_score": v["gate_score"],
                    "auto_ticket_on_wrong_root": acts.get("ticket") == "executed" and not correct,
                    "llm_decision": llm.get("decision"), "llm_applied": llm.get("applied"), "llm_error": llm.get("error"),
                    "contested": bool(inv.get("contested")),
                    "tool_calls": sum(1 for e in events if e["type"] == "tool_call"), "seconds": out["elapsed_s"]}
        finally:
            await asyncio.to_thread(drop_tenant, et)


def _crew_summary(rows, bus, policy):
    ok = [r for r in rows if "error" not in r]
    mean = lambda k: round(sum(float(r[k]) for r in ok) / len(ok), 3) if ok else None
    return {"bus": bus, "policy": policy, "n": len(rows), "errors": len(rows) - len(ok), "root_correct": mean("correct"),
            "llm_overrides": sum(1 for r in ok if r.get("llm_applied")), "llm_errors": sum(1 for r in ok if r.get("llm_error")),
            "contested": sum(1 for r in ok if r.get("contested")),
            "contested_and_wrong": sum(1 for r in ok if r.get("contested") and not r["correct"]),
            "auto_ticket_precision": round(sum(1 for r in ok if r["ticket"] == "executed" and r["correct"]) /
                                           max(1, sum(1 for r in ok if r["ticket"] == "executed")), 3),
            "path_validity": mean("path_validity"),
            "claims_verified": mean("claims_verified"), "routed_to_correct_team": mean("routed_to_correct_team"),
            "auto_ticket_on_wrong_root": mean("auto_ticket_on_wrong_root"),
            "tickets": dict(Counter(r["ticket"] for r in ok)), "pages": dict(Counter(r["page"] for r in ok)),
            "avg_tool_calls": mean("tool_calls"), "avg_seconds": mean("seconds"), "rows": rows}


def concurrency_test() -> dict:
    sc = scenarios("test")[0]
    et = "eval_concurrency"
    tg = fresh_tenant("acme", et)
    try:
        play(tg, sc)
        inc = intake(tg, sc["t0"], "inc_conc", "INC-CONC")
        tg.write("CREATE (:Task {id:'task_conc', type:'triage', status:'SUBMITTED', version:0})")
        wins, page_results = [], []

        def claimer(i):
            wins.append(claim_task(TenantGraph(et), "task_conc", f"agent{i}", inc["detected_at"]))

        def pager(i):
            ctx = Ctx(TenantGraph(et), now=inc["detected_at"], incident_id="inc_conc", agent=f"responder{i}")
            page_results.append(T.action_page_oncall(ctx, "inc_conc", "per_0001", "test", []))
        for fn, n in ((claimer, 20), (pager, 20)):
            th = [threading.Thread(target=fn, args=(i,)) for i in range(n)]
            [t.start() for t in th]
            [t.join() for t in th]
        n_actions = tg.read("MATCH (a:Action {kind:'page'})-[:FOR]->(:Incident {id:'inc_conc'}) RETURN count(a) AS n")[0]["n"]
        return {"concurrent_claims": 20, "successful_claims": sum(wins), "concurrent_pages": 20, "page_actions_created": n_actions,
                "duplicates_reported": sum(1 for p in page_results if p.get("duplicate")), "pass": sum(wins) == 1 and n_actions == 1}
    finally:
        drop_tenant(et)


def isolation_test() -> dict:
    acme, globex = TenantGraph("acme"), TenantGraph("globex")
    ca, cg = Ctx(acme, register=False), Ctx(globex, register=False)
    r_a = T.graph_resolve(ca, "checkout")["matches"][0]["id"]
    r_g = T.graph_resolve(cg, "checkout")["matches"][0]["id"]
    acme_only = acme.read("MATCH (i:Incident) RETURN i.id AS id ORDER BY i.id DESC LIMIT 1")[0]["id"]
    leak_get_by_id = globex.read("MATCH (i:Incident {id:$id}) RETURN count(i) AS n", {"id": "inc_0287"})[0]["n"]
    ledger_in_globex = T.graph_resolve(cg, "ledger")["matches"]
    people_a = {r["id"] for r in acme.read("MATCH (p:Person) RETURN p.id AS id")}
    exp_g = T.org_experts(cg, r_g)["experts"]
    names_a = {r["n"] for r in acme.read("MATCH (p:Person) RETURN p.name AS n")}
    leaked_people = [e["name"] for e in exp_g if e["name"] in names_a and e["name"] not in
                     {r["n"] for r in globex.read("MATCH (p:Person) RETURN p.name AS n")}]
    acme_inc_globex = globex.read("MATCH (i:Incident {id:$id}) RETURN count(i) AS n", {"id": acme_only})[0]["n"]
    return {"same_name_resolves_per_tenant": [r_a, r_g], "globex_sees_acme_incident_by_id": bool(leak_get_by_id and acme_inc_globex),
            "globex_resolves_acme_only_service_ledger": bool(ledger_in_globex), "leaked_people": leaked_people,
            "pass": not leaked_people and not ledger_in_globex}


async def probes_eval() -> dict:
    d = settings.data_dir / "acme" / "scenarios" / "probes"
    ku = json.loads((d / "knowledge_update.json").read_text())
    qa = json.loads((d / "brain_qa.json").read_text())
    now = settings_now = 1790856000000  # 2026-10-01 12:00 UTC
    people = {r["id"]: r["name"] for r in TenantGraph("acme").read("MATCH (p:Person) RETURN p.id AS id, p.name AS name")}
    ku_rows = []
    for p in ku:
        if p["type"] in ("topology_now", "topology_as_of", "oncall", "disambiguation", "person_status"):
            continue  # answered by tools below
        r = await ask("acme", p["q"], now)
        ku_rows.append({"q": p["q"], "gold": p["answer"], "answer": r["answer"], "ok": (p["answer"] or "").lower() in r["answer"].lower()})
    # topology as-of (deterministic tools)
    tg = TenantGraph("acme")
    ctx = Ctx(tg, now=now, register=False)
    deps_now = [x["d"] for x in T.graph_neighbors(ctx, "svc_search", ["DEPENDS_ON"], "out")["edges"] if x["valid_to"] is None]
    ku_rows.append({"q": "Does search read from pg-catalog today?", "gold": "no", "answer": str("ds_pg-catalog" in deps_now), "ok": "ds_pg-catalog" not in deps_now})
    asof = T.incident_blast_radius(Ctx(tg, now=now, register=False), "ds_pg-catalog", 1, at=1768435200000)["impacted"]
    ku_rows.append({"q": "Did search read from pg-catalog on 2026-01-15?", "gold": "yes", "answer": str(any(x["id"] == "svc_search" for x in asof)),
                    "ok": any(x["id"] == "svc_search" for x in asof)})
    qa_rows = []
    for p in qa:
        r = await ask("acme", p["q"], now)
        a = r["answer"]
        if p["type"] in ("who_knows",):
            ok = any(people.get(x, "~") in a for x in p["answer_ids"][:3])
        elif p["type"] == "what_breaks":
            names = {x.replace("svc_", "").replace("ds_", "") for x in p["answer_ids"]}
            ok = (not names and "0 components" in a) or (names and sum(n in a for n in names) / len(names) >= 0.5) or str(len(names)) in a
        elif p["type"] == "why_decision":
            ok = p["decision_id"].replace("dec_", "ADR-") in a or any(e["id"] for e in r["citations"])
            ok = p["decision_id"].replace("dec_", "ADR-") in a
        elif p["type"] == "who_fixed_similar":
            ok = any(people.get(x, "~") in a for x in p["answer_ids"])
        elif p["type"] == "as_of_owner":
            ok = (p["answer"] or "").lower() in a.lower()
        elif p["type"] == "bus_factor":
            ok = "Bus factor 1" in a and any(people.get(x, "~") in a for x in p["answer_ids"])
        elif p["type"] == "staleness":
            ok = "stale" in a.lower()
        else:
            ok = False
        qa_rows.append({"type": p["type"], "q": p["q"], "ok": bool(ok), "answer": a[:200], "cited": len(r["citations"]) > 0})
    by = Counter(r["type"] for r in qa_rows)
    acc = Counter(r["type"] for r in qa_rows if r["ok"])
    return {"knowledge_update": {"n": len(ku_rows), "correct": sum(r["ok"] for r in ku_rows), "stale_answers": sum(not r["ok"] for r in ku_rows),
                                 "rows": ku_rows},
            "brain_qa": {"n": len(qa_rows), "correct": sum(r["ok"] for r in qa_rows), "cited_rate": round(sum(r["cited"] for r in qa_rows) / len(qa_rows), 3),
                         "by_type": {t: f"{acc[t]}/{by[t]}" for t in by}, "rows": qa_rows}}


async def learning_curve(bus: str = "direct") -> dict:
    et = "eval_learning"
    fresh_tenant("acme", et)
    rows = []
    try:
        for sc in scenarios("learning"):
            tg = TenantGraph(et)
            play(tg, sc)
            inc = intake(tg, sc["t0"], f"inc_{sc['id'].lower().replace('-', '')}", f"INC-{sc['id']}")
            out = await run_crew(et, inc, inc["detected_at"] + 5 * MIN, bus_mode=bus, policy="offline")
            inv = out["investigation"]
            root = inv.get("root") or {}
            correct = root.get("change_id") in sc["truth"]["root_change_ids"]
            confirm(tg, inc["incident_id"], correct, "eval-oncall", inc["detected_at"] + 30 * MIN,
                    actual_cause_id=None if correct else sc["truth"]["root_change_ids"][0])
            sim_keys = [s["key"] for s in inv.get("similar", [])]
            lesson = tg.read("MATCH (l:Lesson {id:'les_ds_pg-billing_config'}) RETURN l.votes AS v")
            acts = {a["kind"]: a.get("status") for a in out["response"].get("actions", [])}
            true_rank = next((i + 1 for i, c in enumerate(inv.get("candidates", [])) if c["change_id"] in sc["truth"]["root_change_ids"]), None)
            rows.append({"seq": sc["sequence"], "correct": correct, "true_cause_rank": true_rank, "score": root.get("score"), "H": (root.get("breakdown") or {}).get("H"),
                         "K": (root.get("breakdown") or {}).get("K"), "gate_score": out["verification"]["gate_score"],
                         "ticket": acts.get("ticket"), "recalled_previous_LC": sum(k.startswith("INC-LC") for k in sim_keys),
                         "lesson_votes": lesson[0]["v"] if lesson else 0})
    finally:
        drop_tenant(et)
    return {"rows": rows}


async def main_async(bus: str, policy: str = "offline", only_crew: bool = False):
    t = time.time()
    if only_crew:
        rep = {"crew": await crew_eval(bus, policy)}
        OUT.mkdir(exist_ok=True)
        (OUT / f"crew_{policy}_{bus}.json").write_text(json.dumps(rep, indent=1, default=str))
        print("CREW", json.dumps({k: v for k, v in rep["crew"].items() if k != "rows"}), f"seconds {time.time() - t:.0f}")
        return
    rep = {"crew": await crew_eval(bus, "offline")}
    rep["concurrency"] = concurrency_test()
    rep["isolation"] = isolation_test()
    rep["probes"] = await probes_eval()
    rep["learning_curve"] = await learning_curve("direct")
    rep["seconds"] = round(time.time() - t, 1)
    OUT.mkdir(exist_ok=True)
    (OUT / f"system_{bus}.json").write_text(json.dumps(rep, indent=1, default=str))
    s = {k: v for k, v in rep["crew"].items() if k != "rows"}
    print("CREW", json.dumps(s))
    print("CONCURRENCY", rep["concurrency"])
    print("ISOLATION", rep["isolation"])
    print("KNOWLEDGE-UPDATE", {k: v for k, v in rep["probes"]["knowledge_update"].items() if k != "rows"})
    print("BRAIN-QA", {k: v for k, v in rep["probes"]["brain_qa"].items() if k != "rows"})
    print("LEARNING", *rep["learning_curve"]["rows"], sep="\n  ")
    print("seconds", rep["seconds"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", default="direct", choices=["direct", "mcp"])
    ap.add_argument("--policy", default="offline", choices=["offline", "gemini"])
    ap.add_argument("--only-crew", action="store_true")
    a = ap.parse_args()
    asyncio.run(main_async(a.bus, a.policy, a.only_crew))


if __name__ == "__main__":
    main()
