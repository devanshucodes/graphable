"""Validation gates (DATA_SPEC §11). Errors abort generation; distribution drift is a warning."""
from __future__ import annotations

import re
from collections import Counter

from .config import DAY_MS
from .world import World

PII = [re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), re.compile(r"\b(?:\d[ -]?){15,16}\b")]


def validate(w: World, tl, scen: dict) -> dict:
    errors, warnings = [], []
    cfg = w.cfg
    P, E = set(w.people), set(w.entities())
    T = set(w.teams)

    def need(cond, msg):
        if not cond:
            errors.append(msg)

    # referential integrity
    for c in tl.changes:
        need(c["entity_id"] in E, f"change {c['id']} bad entity")
        need(c["author_id"] in P, f"change {c['id']} bad author")
    for i in tl.incidents:
        need(all(r["person_id"] in P for r in i["responders"]), f"{i['id']} bad responder")
        need(all(e in E for e in i["affected_entity_ids"]), f"{i['id']} bad entity")
    for a in tl.alerts:
        need(a["entity_id"] in E, f"alert {a['id']} bad entity")
    th_ids = {t["id"] for t in tl.threads}
    for m in tl.messages:
        need(m["thread_id"] in th_ids and m["author_id"] in P, f"message {m['id']} bad ref")
    for t in tl.tickets:
        need(t["assignee_id"] in P and all(e in E for e in t["entity_ids"]), f"ticket {t['id']} bad ref")
    for d in tl.docs:
        need(d["author_id"] in P, f"doc {d['id']} bad author")
    for o in w.ownership:
        need(o["team_id"] in T and o["entity_id"] in E, "ownership bad ref")

    # nobody acts outside employment
    def active(pid, t):
        return w.person_active(pid, t)
    bad_auth = [c["id"] for c in tl.changes if not active(c["author_id"], c["ts"])]
    bad_msg = [m["id"] for m in tl.messages if not active(m["author_id"], m["ts"])]
    need(not bad_auth, f"{len(bad_auth)} changes by inactive people")
    if bad_msg:
        warnings.append(f"{len(bad_msg)} messages by people outside employment window")

    # exactly one owner per service at every sampled instant
    t = cfg.history_start
    while t <= cfg.history_end:
        for e in E:
            n = sum(1 for o in w.ownership if o["entity_id"] == e and w.active(o, t))
            need(n == 1, f"{e} has {n} owners at {t}")
        t += 7 * DAY_MS

    # temporal sanity
    need(all(cfg.history_start <= c["ts"] <= cfg.history_end for c in tl.changes), "change outside history window")
    need(all(i["started_at"] <= cfg.history_end for i in tl.incidents), "incident outside history window")
    for s in scen.get("test", []) + scen.get("hero", []) + scen.get("learning", []) + scen.get("train", []):
        need(cfg.test_start <= s["t0"] <= cfg.test_end, f"scenario {s['id']} outside test window")

    # topology
    indeg = Counter(d["dst"] for d in w.dependencies if d["valid_to"] is None)
    if cfg.full_org_events:
        hubs = [e for e, n in indeg.items() if n >= 10]
        need(len(hubs) >= 3, f"too few hubs: {hubs}")
        pairs = {(d["src"], d["dst"]) for d in w.dependencies if d["valid_to"] is None}
        need(("svc_orders", "svc_inventory") in pairs and ("svc_inventory", "svc_orders") in pairs, "missing orders<->inventory cycle")

    # prose
    texts = [m["text"] for m in tl.messages] + [d["text"] for d in tl.docs] + [x["text"] for x in tl.tickets]
    need(all(isinstance(x, str) and x for x in texts), "unrendered artifacts")
    inj = sum(1 for x in texts if "Ignore previous instructions" in x)
    if cfg.full_org_events:
        need(inj == 3, f"expected exactly 3 injection traps, found {inj}")
    pii = sum(1 for x in texts for p in PII if p.search(x))
    need(pii == 0, f"{pii} PII-like patterns in prose")
    need(all(p["email"].endswith(cfg.email_domain) for p in w.people.values()), "non-fictional email domain")

    # distributions (warnings)
    if cfg.full_org_events:
        n_inc = len(tl.incidents)
        chk = {
            "changes ~10000": (len(tl.changes), 10000), "incidents ~400": (n_inc, 400),
            "change-induced ~250": (sum(t["cause_type"] == "change" for t in tl.incidents_truth), 250),
            "messages ~15000": (len(tl.messages), 15000), "tickets ~3000": (len(tl.tickets), 3000),
            "threads ~2000": (len(tl.threads), 2000),
        }
        for k, (got, want) in chk.items():
            if abs(got - want) / want > 0.15:
                warnings.append(f"{k}: got {got}")
        silent = sum(t["silent_root"] for t in tl.incidents_truth) / max(1, n_inc)
        if abs(silent - 0.30) > 0.08:
            warnings.append(f"silent-root share {silent:.2f}")
        sev = Counter(i["sev"] for i in tl.incidents)
        for s_, want in {1: .03, 2: .12, 3: .35, 4: .30, 5: .20}.items():
            if abs(sev[s_] / n_inc - want) > 0.05:
                warnings.append(f"SEV{s_} share {sev[s_] / n_inc:.2f}")
    return {"errors": errors, "warnings": warnings, "ok": not errors}
