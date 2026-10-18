"""Held-out evaluation and demo scenarios (never loaded as history): 40 test scenarios,
learning-curve set, knowledge-update probes, Brain Q&A set, and 4 hero demo scenarios."""
from __future__ import annotations

import random

from . import catalog as C
from .config import DAY_MS, HOUR_MS, MIN_MS, iso, ms
from .timeline import Sim, Timeline
from .world import World, oncall_at


class ScenarioBuilder:
    def __init__(self, w: World, tl: Timeline, sim: Sim):
        self.w, self.tl, self.sim = w, tl, sim
        self.rng = random.Random(w.cfg.seed + 3)
        sim.rng = self.rng  # scenario randomness must not disturb history
        self.seq = 0

    def _id(self, sid, kind):
        # standard id prefixes (chg_/alt_/msg_) so every tool and the verifier can resolve labels
        self.seq += 1
        return f"{kind}_{sid.lower().replace('-', '')}_{self.seq:04d}"

    def _change(self, sid, e, ts, kind=None, mechanism=None, author=None, bad=False):
        owner = self.w.owner(e, ts)
        c = self.sim._make_change(e, ts, owner, kind=kind, mechanism=mechanism, author=author, force_bad=bad)
        if mechanism and c["kind"] == "config":
            key = C.CONFIG_KEYS.get(mechanism)
            if key:
                c["summary"] = f"{self.sim.name(e)}: {key[0]} {key[1]} -> {key[2]}"
        c["id"] = self._id(sid, "chg")
        for k in [k for k in c if k.startswith("_")]:
            c.pop(k)
        return c

    def _alerts(self, sid, roots, t0, symptom, root_alerts, silent, force_alerting=()):
        hit = self.sim.propagate(roots, t0, symptom)
        for e in force_alerting:
            hit.setdefault(e, 1)
        alerting = ({} if silent else {r: 0 for r in roots}) | hit
        out = []
        for e, hop in sorted(alerting.items(), key=lambda x: x[1]):
            names = root_alerts if e in roots else C.SYMPTOM_ALERTS[symptom]
            for a in self.rng.sample(names, min(len(names), self.rng.randint(1, 2))):
                out.append({"id": self._id(sid, "alt"), "name": a, "entity_id": e, "metric": a.lower(),
                            "severity": "critical" if hop <= 1 else "warning",
                            "ts": t0 + hop * self.rng.randint(1, 4) * MIN_MS + self.rng.randint(0, 90) * 1000,
                            "resolved_ts": None, "value": round(self.rng.uniform(1.2, 6), 2), "threshold": 1.0})
        # background noise alerts in the scenario window
        for _ in range(self.rng.randint(1, 4)):
            out.append({"id": self._id(sid, "alt"), "name": self.rng.choice(C.NOISE_ALERTS),
                        "entity_id": self.rng.choice(self.w.entities()), "metric": "noise", "severity": "warning",
                        "ts": t0 + self.rng.randint(-30, 30) * MIN_MS, "resolved_ts": None, "value": 1.1, "threshold": 1.0})
        return sorted(out, key=lambda a: a["ts"]), alerting

    def _distractors(self, sid, k, root_change_ts, t0, near):
        out = []
        ents = self.w.entities()
        for i in range(k):
            r = self.rng.random()
            if r < 0.4 and near:
                e = self.rng.choice(near)  # upstream / alerting services: tempting but innocent
            else:
                e = self.rng.choice([x for x in ents if not x.endswith("paylynx")])
            if i % 3 == 0 and root_change_ts:
                ts = self.rng.randint(root_change_ts + MIN_MS, t0 - MIN_MS)  # newer than the root: the decoy
            else:
                ts = self.rng.randint(t0 - 2 * HOUR_MS, t0 - MIN_MS)
            out.append(self._change(sid, e, ts, kind=self.rng.choice(["code", "code", "config", "flag"])))
        return out

    def _truth(self, roots, cause, mech, root_changes, alerting, silent, t):
        return {"root_entity_ids": roots, "cause_type": cause, "root_change_ids": [c["id"] for c in root_changes],
                "mechanism": mech, "silent_root": silent, "alerting_entity_ids": sorted(alerting),
                "correct_team_id": self.w.owner(roots[0], t),
                "correct_oncall_id": oncall_at(self.w, self.w.owner(roots[0], t), t),
                "expected_experts": self.sim.experts(roots[0], t, 3)}

    def _pack(self, sid, stype, t0, changes, alerts, truth, messages=(), extra=None):
        ev = [{"kind": "change", "ts": c["ts"], "data": c} for c in changes] + \
             [{"kind": "alert", "ts": a["ts"], "data": a} for a in alerts] + \
             [{"kind": "message", "ts": m["ts"], "data": m} for m in messages]
        ev.sort(key=lambda e: e["ts"])
        return {"id": sid, "type": stype, "t0": t0, "t0_iso": iso(t0), "events": ev, "truth": truth, **(extra or {})}

    # ---------------- test scenarios ----------------
    def train_scenarios(self):
        """Separate tuning set (different rng, prefix TR) so weights/threshold are never fit on the test set."""
        saved_rng, saved_sim_rng = self.rng, self.sim.rng
        self.rng = random.Random(self.w.cfg.seed + 5)
        self.sim.rng = self.rng
        try:
            return self.test_scenarios(prefix="TR")
        finally:
            self.rng, self.sim.rng = saved_rng, saved_sim_rng

    def test_scenarios(self, prefix: str = "TS"):
        w, rng, cfg = self.w, self.rng, self.w.cfg
        plan = [("change_induced", k) for k in (1, 3, 6, 9, 12, 15) for _ in range(4)] + \
               [("non_change", 4)] * 8 + [("multi_root", 6)] * 4 + [("look_alike", 6)] * 4
        out = []
        frag = {e: w.fragility[e] for e in w.entities() if not e.endswith("paylynx")}
        for i, (stype, k) in enumerate(plan[:cfg.n_test_scenarios], 1):
            sid = f"{prefix}-{i:02d}"
            t0 = rng.randint(cfg.test_start + 6 * HOUR_MS, cfg.test_start + 9 * DAY_MS)
            if stype in ("change_induced", "multi_root"):
                n_roots = 2 if stype == "multi_root" else 1
                roots = rng.choices(list(frag), weights=list(frag.values()), k=n_roots)
                roots = list(dict.fromkeys(roots))
                root_changes, mechs = [], []
                for r in roots:
                    kind = rng.choice(["config", "code", "config", "migration"] if r.startswith("ds_pg") else
                                      ["config", "code"] if r.startswith("ds_") else ["code", "config", "flag", "dependency_bump"])
                    mech = rng.choice(C.MECHANISMS[kind])
                    c = self._change(sid, r, t0 - rng.randint(8, 90) * MIN_MS, kind=kind, mechanism=mech[0], bad=True)
                    root_changes.append(c)
                    mechs.append(mech)
                silent = rng.random() < 0.3
                alerts, alerting = self._alerts(sid, roots, t0, mechs[0][1], mechs[0][2], silent)
                near = [e for e in alerting if e not in roots]
                dis = self._distractors(sid, k, min(c["ts"] for c in root_changes), t0, near)
                truth = self._truth(roots, "change", [m[0] for m in mechs], root_changes, alerting, silent, t0)
                out.append(self._pack(sid, stype, t0, root_changes + dis, alerts, truth, extra={"distractors": k}))
            elif stype == "non_change":
                cause = ["capacity", "cert_expiry", "external_outage", "node_failure", "data_issue", "flag_drift", "capacity", "cert_expiry"][(i - 1) % 8]
                mech = C.NON_CHANGE[cause]
                root = "ds_ext-paylynx" if cause == "external_outage" else rng.choice(
                    [e for e, s in w.services.items() if s["customer_facing"] and s["tier"] <= 1]) if cause == "capacity" else \
                    rng.choice([e for e in w.entities() if not e.endswith("paylynx")])
                silent = False
                alerts, alerting = self._alerts(sid, [root], t0, mech[1], mech[2], silent)
                dis = self._distractors(sid, k, None, t0, [e for e in alerting if e != root])
                truth = self._truth([root], cause, [mech[0]], [], alerting, silent, t0)
                out.append(self._pack(sid, stype, t0, dis, alerts, truth, extra={"distractors": k}))
            else:  # look_alike: same symptom signature as the redis eviction motif, different root
                root = rng.choice(["svc_config-service", "ds_pg-catalog", "svc_pricing", "ds_pg-inventory"])
                root = root if (root in w.services or root in w.datastores) else rng.choice(w.entities())
                kind = "config" if root.startswith(("svc_config", "svc_pricing")) else "migration"
                mech = next(m for m in C.MECHANISMS[kind] if m[1] == "latency")
                c = self._change(sid, root, t0 - rng.randint(10, 60) * MIN_MS, kind=kind, mechanism=mech[0], bad=True)
                force = [e for e in ("svc_cart", "svc_checkout") if e in w.services]
                alerts, alerting = self._alerts(sid, [root], t0, "latency", ["LatencyP99Breach", "CacheHitRateDrop"], True, force)
                # tempting redis-cache change that is innocent
                red = [self._change(sid, "ds_redis-cache", t0 - rng.randint(5, 40) * MIN_MS, kind="config")] if "ds_redis-cache" in w.datastores else []
                dis = self._distractors(sid, k, c["ts"], t0, [e for e in alerting if e != root])
                truth = self._truth([root], "change", [mech[0]], [c], alerting, True, t0)
                truth["look_alike_of"] = "motif_redis_evict"
                out.append(self._pack(sid, stype, t0, [c] + red + dis, alerts, truth, extra={"distractors": k + len(red)}))
        return out

    # ---------------- learning-curve set ----------------
    def learning_set(self):
        """Cold-start learning curve: a NEW failure pattern never seen in history (pg-billing idle-timeout config),
        recurring 8 times. Memory has to learn it from confirmed incidents."""
        w, rng = self.w, self.rng
        if "ds_pg-billing" not in w.datastores:
            return []
        out = []
        mech = ("idle connection timeout lowered", "errors", ["UpstreamTimeouts", "HighErrorRate"], "rollback", "timeouts")
        for i in range(8):
            sid = f"LC-{i + 1:02d}"
            t0 = w.cfg.test_start + i * DAY_MS + 15 * HOUR_MS
            c = self._change(sid, "ds_pg-billing", t0 - rng.randint(10, 40) * MIN_MS, kind="config", bad=True)
            c["summary"] = "pg-billing: db.idle_timeout_s 300 -> 5"
            alerts, alerting = self._alerts(sid, ["ds_pg-billing"], t0, mech[1], mech[2], True,
                                            [e for e in ("svc_billing",) if e in w.services])
            dis = self._distractors(sid, 4, c["ts"], t0, [e for e in alerting])
            out.append(self._pack(sid, "learning", t0, [c] + dis, alerts,
                                  self._truth(["ds_pg-billing"], "change", [mech[0]], [c], alerting, True, t0),
                                  extra={"sequence": i + 1, "motif_id": "motif_pg_billing_idle_timeout"}))
        return out

    # ---------------- hero scenarios ----------------
    def hero(self):
        w, rng = self.w, self.rng
        if not w.cfg.full_org_events:
            return []
        sp = w.special
        twin = None
        for inc, tr in zip(self.tl.incidents, self.tl.incidents_truth):
            if tr["motif_id"] == "motif_redis_evict" and inc["resolver_id"] == sp["priya"]:
                twin = inc
        hero_mech = next(m for m in C.MECHANISMS["config"] if m[0] == "cache eviction policy changed")
        out = []
        # H1 repeat offender
        t0 = ms(2026, 10, 6, 14, 9)
        c = self._change("H1", "ds_redis-cache", ms(2026, 10, 6, 14, 2), kind="config", mechanism=hero_mech[0],
                         author=sp["junior_redis"], bad=True)
        c["reviewer_ids"] = []
        alerts, alerting = self._alerts("H1", ["ds_redis-cache"], t0, "latency", hero_mech[2], True,
                                        ["svc_cart", "svc_checkout", "svc_payments"])
        dis = self._distractors("H1", 4, c["ts"], t0, ["svc_cart", "svc_checkout", "svc_payments"])
        tr = self._truth(["ds_redis-cache"], "change", [hero_mech[0]], [c], alerting, True, t0)
        tr.update({"twin_incident_id": twin["id"] if twin else None, "twin_incident_key": twin["key"] if twin else None,
                   "expected_fixer_id": sp["priya"], "expected_runbook_key": "RB-redis-07"})
        out.append(self._pack("H1", "hero_repeat_offender", t0, [c] + dis, alerts, tr,
                              extra={"story": "Junior engineer changes redis-cache eviction policy; silent root; memory recalls "
                                              f"{twin['key'] if twin else 'the twin incident'} fixed by Priya with RB-redis-07."}))
        # H2 decoy deploy
        t0 = ms(2026, 10, 8, 9, 18)
        mech = next(m for m in C.MECHANISMS["config"] if m[0] == "timeout lowered below dependency p99")
        c = self._change("H2", "svc_config-service", ms(2026, 10, 8, 9, 10), kind="config", mechanism=mech[0], bad=True)
        decoy = self._change("H2", "svc_web-bff", ms(2026, 10, 8, 9, 14), kind="code")
        alerts, alerting = self._alerts("H2", ["svc_config-service"], t0, "errors", mech[2], True,
                                        ["svc_api-gateway", "svc_web-bff", "svc_checkout", "svc_orders"])
        dis = [decoy] + self._distractors("H2", 8, c["ts"], t0, ["svc_api-gateway", "svc_web-bff", "svc_checkout", "svc_orders"])
        out.append(self._pack("H2", "hero_decoy_deploy", t0, [c] + dis, alerts,
                              self._truth(["svc_config-service"], "change", [mech[0]], [c], alerting, True, t0) | {"decoy_change_id": decoy["id"]},
                              extra={"story": "Silent config-service timeout change at 09:10; newest change (web-bff deploy 09:14) is innocent."}))
        # H3 the org moved (live ingest)
        t_msg = ms(2026, 10, 12, 10, 0)
        msg = {"id": "msg_h3_0001", "channel": "#announcements", "author_id": sp["lena"], "ts": t_msg,
               "text": "FYI from today checkout is owned by Orbit, ping @lena for anything urgent"}
        t0 = ms(2026, 10, 12, 15, 30)
        c2 = self._change("H3", "ds_redis-cache", t0 - 12 * MIN_MS, kind="config", mechanism=hero_mech[0], bad=True)
        alerts, alerting = self._alerts("H3", ["ds_redis-cache"], t0, "latency", hero_mech[2], True, ["svc_checkout"])
        tr = self._truth(["ds_redis-cache"], "change", [hero_mech[0]], [c2], alerting, True, t0)
        tr.update({"ownership_change": {"entity_id": "svc_checkout", "from_team": w.owner("svc_checkout", t_msg - 1),
                                        "to_team": "team_orbit", "source_message_id": msg["id"], "valid_from": t_msg},
                   "expected_page_team_for_checkout": "team_orbit",
                   "as_of_question": {"q": f"Who owned checkout during {twin['key'] if twin else 'the twin incident'}?",
                                      "answer_team_id": w.owner("svc_checkout", twin["started_at"]) if twin else None}})
        out.append(self._pack("H3", "hero_org_moved", t0, [c2], alerts, tr, messages=[msg],
                              extra={"story": "Live chat message moves checkout to Orbit; paging target changes; as-of query still correct."}))
        # H4 not a change
        t0 = ms(2026, 10, 13, 3, 40)
        mech = C.NON_CHANGE["cert_expiry"]
        alerts, alerting = self._alerts("H4", ["svc_vault"], t0, "errors", mech[2], False, ["svc_auth", "svc_payments", "svc_payouts"])
        dis = self._distractors("H4", 3, None, t0, ["svc_auth", "svc_payments"])
        vault_rbs = [r["key"] for r in self.tl.runbooks if r["entity_id"] == "svc_vault" and r["status"] == "active"]
        tr = self._truth(["svc_vault"], "cert_expiry", [mech[0]], [], alerting, False, t0)
        tr.update({"expect_abstain_from_change": True, "departed_expert_id": sp["vault_expert"],
                   "expected_successor_id": sp["vault_successor"], "expected_bus_factor": 1, "stale_runbooks": vault_rbs})
        out.append(self._pack("H4", "hero_not_a_change", t0, dis, alerts, tr,
                              extra={"story": "Vault TLS cert expired; no suspect change; vault expert left 2026-06-20; runbook stale; bus factor 1."}))
        return out

    # ---------------- probes ----------------
    def probes(self):
        w, rng, tl = self.w, self.rng, self.tl
        if not w.cfg.full_org_events:
            return {"knowledge_update": [], "brain_qa": []}
        tq = w.cfg.test_start
        name = lambda e: (w.services.get(e) or w.datastores.get(e))["name"]
        tname = lambda t: w.teams[t]["name"] if t else None
        ku = [
            {"q": "Who owns promotions now?", "answer": tname(w.owner("svc_promotions", tq)), "type": "current_owner"},
            {"q": "Who owned promotions on 2026-06-15?", "answer": tname(w.owner("svc_promotions", ms(2026, 6, 15))), "type": "as_of_owner"},
            {"q": "Who owns ledger now?", "answer": tname(w.owner("svc_ledger", tq)), "type": "current_owner"},
            {"q": "Who owned ledger on 2026-03-01?", "answer": tname(w.owner("svc_ledger", ms(2026, 3, 1))), "type": "as_of_owner"},
            {"q": "Does search read from pg-catalog today?", "answer": "no", "type": "topology_now"},
            {"q": "Did search read from pg-catalog on 2026-01-15?", "answer": "yes", "type": "topology_as_of"},
            {"q": f"Is {w.people[w.special['vault_expert']]['name']} still at the company?", "answer": "no (left 2026-06-20)", "type": "person_status"},
            {"q": "Which team is Sam Okafor on?", "answer": tname(w.team_of(w.special["sam_a"], tq)), "type": "disambiguation"},
            {"q": "Which team is Sam Lindqvist on?", "answer": tname(w.team_of(w.special["sam_b"], tq)), "type": "disambiguation"},
            {"q": "Who is primary on-call for Payments on 2026-10-01 12:00 UTC?",
             "answer": w.people.get(oncall_at(w, "team_payments", tq + 12 * HOUR_MS), {}).get("name"), "type": "oncall"},
        ]
        qa = []
        svcs = list(w.services)
        for e in rng.sample(svcs, 6):
            qa.append({"type": "who_knows", "q": f"Who knows {name(e)} best?",
                       "answer_ids": self.sim.experts(e, tq, 3), "entity_id": e})
        for d in rng.sample([d for d in tl.decisions if d["doc_id"]], 6):
            qa.append({"type": "why_decision", "q": f"Why did we decide: {d['title']}?", "answer": d["facts"]["context"],
                       "gold_sources": [d["thread_id"], d["doc_id"]], "decision_id": d["id"]})
        _, rev = w.deps_at(tq)
        for e in rng.sample(w.entities(), 6):
            seen, frontier = set(), [e]
            while frontier:
                x = frontier.pop()
                for dd in rev.get(x, []):
                    if dd["src"] not in seen:
                        seen.add(dd["src"])
                        frontier.append(dd["src"])
            qa.append({"type": "what_breaks", "q": f"What breaks if {name(e)} goes down?", "answer_ids": sorted(seen), "entity_id": e})
        motif_incs = [(i, t) for i, t in zip(tl.incidents, tl.incidents_truth) if t["motif_id"]]
        for inc, tr in rng.sample(motif_incs, min(6, len(motif_incs))):
            qa.append({"type": "who_fixed_similar", "q": f"Who has fixed incidents like {inc['key']} before?",
                       "answer_ids": sorted({i["resolver_id"] for i, t in motif_incs if t["motif_id"] == tr["motif_id"]}),
                       "incident_id": inc["id"]})
        for e, t in (("svc_promotions", ms(2026, 6, 1)), ("svc_promotions", ms(2026, 8, 1)),
                     ("svc_billing", ms(2026, 2, 1)), ("svc_billing", ms(2026, 5, 1))):
            qa.append({"type": "as_of_owner", "q": f"Who owned {name(e)} on {iso(t)[:10]}?", "answer": tname(w.owner(e, t))})
        qa.append({"type": "bus_factor", "q": "What is the bus factor of vault and who should we train?", "answer": 1,
                   "answer_ids": [w.special["vault_successor"]]})
        qa.append({"type": "staleness", "q": "Which vault runbooks are stale and why?",
                   "answer_ids": [r["id"] for r in tl.runbooks if r["entity_id"] == "svc_vault" and r["status"] == "active"],
                   "answer": "author left on 2026-06-20 and not verified since"})
        return {"knowledge_update": ku, "brain_qa": qa}
