"""Integration tests against a local FalkorDB with the bootstrapped data (python -m graphable.bootstrap).
Run: .venv/bin/python -m pytest -q tests"""
import asyncio
import threading

import pytest

from graphable.agents.crew import run_crew
from graphable.core import tools as T
from graphable.core.db import TenantGraph, client
from graphable.core.evidence import Ctx
from graphable.core.verifier import verify
from graphable.sim.player import claim_task, drop_tenant, fresh_tenant, intake, load_scenario, play

try:
    _GRAPHS = client().list_graphs()
except Exception as e:  # FalkorDB unreachable: skip the whole module instead of failing at import
    pytest.skip(f"FalkorDB is not reachable ({type(e).__name__}: {e}); start it, then run python -m graphable.bootstrap",
                allow_module_level=True)
pytestmark = pytest.mark.skipif("golden_acme" not in _GRAPHS, reason="run python -m graphable.bootstrap first")
NOW = 1790856000000


@pytest.fixture(scope="module")
def acme():
    return TenantGraph("acme")


def test_exactly_one_owner_per_component_over_time(acme):
    for t in range(1759276800000, 1790812800000, 14 * 86_400_000):
        rows = acme.read("""MATCH (c:Component) OPTIONAL MATCH (:Team)-[o:OWNS]->(c)
            WHERE o.valid_from <= $t AND (o.valid_to IS NULL OR o.valid_to > $t) RETURN c.id AS c, count(o) AS n""", {"t": t})
        assert all(r["n"] == 1 for r in rows), [r for r in rows if r["n"] != 1]


def test_as_of_ownership_and_history(acme):
    ctx = Ctx(acme, now=NOW, register=False)
    assert T.org_ownership(ctx, "svc_promotions", at=1781000000000)["owner"]["team"] == "Checkout"
    assert T.org_ownership(ctx, "svc_promotions")["owner"]["team"] == "Orbit"
    assert len(T.org_ownership(ctx, "svc_promotions")["history"]) == 2


def test_cypher_guard_blocks_writes_and_unbounded_paths(acme):
    ctx = Ctx(acme, now=NOW, register=False)
    assert "error" in T.graph_cypher_readonly(ctx, "MATCH (n) DETACH DELETE n", purpose="x")
    assert "error" in T.graph_cypher_readonly(ctx, "MATCH p=(a)-[*]->(b) RETURN p", purpose="x")
    assert "error" in T.graph_cypher_readonly(ctx, "MATCH p=(a)-[*1..9]->(b) RETURN p", purpose="x")
    assert "rows" in T.graph_cypher_readonly(ctx, "MATCH (s:Service {tier:0}) RETURN s.name AS n", purpose="x")


def test_verifier_rejects_fabricated_and_foreign_evidence():
    tg = fresh_tenant("acme", "eval_pytest_verify")
    try:
        sc = load_scenario("acme", "hero", "H1")
        play(tg, sc)
        inc = intake(tg, sc["t0"], "inc_pv", "INC-PV")
        ctx = Ctx(tg, now=sc["t0"], incident_id="inc_pv")
        rc = T.incident_rank_root_causes(ctx, "inc_pv")
        top = rc["candidates"][0]
        good = {"text": "root", "evidence_ids": [top["evidence_id"]], "entity_ids": [top["change_id"], top["entity_id"]]}
        fake = {"text": "made up", "evidence_ids": ["ev_doesnotexist"], "entity_ids": ["svc_checkout"]}
        wrong_entity = {"text": "wrong", "evidence_ids": [top["evidence_id"]], "entity_ids": ["per_0001"]}
        v = verify(tg, "inc_pv", [good, fake, wrong_entity])
        assert [c["verified"] for c in v["claims"]] == [True, False, False]
        assert v["path_validity"] == 1.0
        assert "error" in T.memory_note(ctx, "inc_pv", "hypothesis", "x", ["ev_doesnotexist"])
    finally:
        drop_tenant("eval_pytest_verify")


def test_cas_claim_and_idempotent_pages():
    tg = fresh_tenant("acme", "eval_pytest_conc")
    try:
        tg.write("CREATE (:Incident {id:'inc_c', sev:3})")
        tg.write("CREATE (:Task {id:'task_c', status:'SUBMITTED', version:0})")
        wins, pages = [], []
        th = [threading.Thread(target=lambda i=i: wins.append(claim_task(TenantGraph("eval_pytest_conc"), "task_c", f"a{i}", NOW))) for i in range(16)]
        th += [threading.Thread(target=lambda i=i: pages.append(T.action_page_oncall(Ctx(TenantGraph("eval_pytest_conc"), now=NOW, agent=f"r{i}"),
                                                                                    "inc_c", "per_0001", "t", []))) for i in range(16)]
        [t.start() for t in th]
        [t.join() for t in th]
        assert sum(wins) == 1
        assert tg.read("MATCH (a:Action {kind:'page'}) RETURN count(a) AS n")[0]["n"] == 1
        assert all(p["status"] == "awaiting_approval" for p in pages)  # SEV3 never auto-pages
    finally:
        drop_tenant("eval_pytest_conc")


def test_tenant_isolation():
    cg = Ctx(TenantGraph("globex"), register=False)
    assert T.graph_resolve(cg, "ledger")["matches"] == []            # acme-only service
    assert TenantGraph("globex").read("MATCH (i:Incident {id:'inc_0287'}) RETURN count(i) AS n")[0]["n"] == 0


@pytest.mark.parametrize("sid,expect", [("H1", "ds_redis-cache"), ("H2", "svc_config-service"), ("H4", "non_change:svc_vault")])
def test_hero_scenarios(sid, expect):
    et = f"eval_pytest_{sid.lower()}"
    tg = fresh_tenant("acme", et)
    try:
        sc = load_scenario("acme", "hero", sid)
        play(tg, sc)
        inc = intake(tg, sc["t0"], f"inc_{et}", f"INC-{sid}")
        out = asyncio.run(run_crew(et, inc, inc["detected_at"] + 300_000, bus_mode="direct", auto_approve=True))
        root = out["investigation"]["root"]
        got = f"non_change:{root['entity_id']}" if root["type"] == "non_change" else root["entity_id"]
        assert got == expect
        assert out["verification"]["path_validity"] == 1.0
        assert all(c["verified"] for c in out["investigation"]["claims"])
        # coordination through the graph: each receiving agent read the handoff left for it, and the Responder got the root
        assert out["investigation"]["inbox"] and out["investigation"]["inbox"]["from"] == "triage"
        assert out["response"]["inbox"]["root"]["root_entity_id"] == root["entity_id"]
        assert tg.read("MATCH (h:Handoff) WHERE h.verification_status = 'accepted' RETURN count(h) AS n")[0]["n"] == 2
        # Triage used FalkorDB PageRank to rank the alerting services
        assert out["triage"]["hubs"] and out["triage"]["hubs"][0]["rank"] >= 1
    finally:
        drop_tenant(et)


def test_typo_tolerant_resolution(acme):
    ctx = Ctx(acme, register=False)
    assert T.graph_resolve(ctx, "chekout")["matches"][0]["id"] == "svc_checkout"   # one edit
    assert T.graph_resolve(ctx, "paymnets")["matches"][0]["id"] == "svc_payments"  # swapped letters (two edits)
    assert all(m["score"] < 0.2 for m in T.graph_resolve(ctx, "the lunch menu")["matches"])  # only weak candidates: the Brain ignores them


def test_task_claim_is_per_crew_run():
    tg = fresh_tenant("acme", "eval_pytest_claim")
    try:
        tg.write("CREATE (:Task {id:'task_r', status:'SUBMITTED', version:0})")
        assert claim_task(tg, "task_r", "triage:crewA", NOW) is True
        assert claim_task(tg, "task_r", "triage:crewB", NOW) is False   # same role, different crew run: loses
        assert claim_task(tg, "task_r", "triage:crewA", NOW) is True    # the holder can continue its own task
    finally:
        drop_tenant("eval_pytest_claim")


def test_brain_session_follow_up_does_not_hijack_named_entity():
    from graphable.agents.brain import ask
    et = "eval_pytest_brain"
    fresh_tenant("acme", et)
    try:
        a = asyncio.run(ask(et, "Who knows vault best?", NOW, session_id="t1"))
        assert "vault" in a["answer"].lower() and a["turn"] == 1
        b = asyncio.run(ask(et, "Who owns checkout?", NOW, session_id="t1"))     # names a different service: no carry-over
        assert b["entity"]["id"] == "svc_checkout"
        c = asyncio.run(ask(et, "Are its runbooks stale?", NOW, session_id="t1"))  # pronoun: follows the conversation
        assert c["entity"]["id"] == "svc_checkout" and c["turn"] == 3
        n = TenantGraph(et).read("MATCH (:Session {id:'t1'})-[:HAS_TURN]->(t:Turn) RETURN count(t) AS n")[0]["n"]
        assert n == 3
    finally:
        drop_tenant(et)


def test_decision_trace_does_not_guess():
    ctx = Ctx(TenantGraph("acme"), register=False)
    assert T.decision_trace(ctx, "ADR-999")["decision"] is None
    assert T.decision_trace(ctx, "adopt quantum blockchain for the cafeteria menu")["decision"] is None
    assert T.decision_trace(ctx, "Use idempotency keys in payouts writes")["decision"]["key"] == "ADR-041"
