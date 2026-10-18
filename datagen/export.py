"""Export to JSONL + manifest (DATA_SPEC §10)."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .config import iso


def _clean(rec):
    if isinstance(rec, dict):
        out = {}
        for k, v in rec.items():
            if k.startswith("_"):
                continue
            out[k] = _clean(v)
            if isinstance(v, int) and not isinstance(v, bool) and (k == "ts" or k.endswith("_at") or k in ("start", "end", "t", "t0")) and v > 10**11:
                out[k + "_iso"] = iso(v)
        return out
    if isinstance(rec, list):
        return [_clean(x) for x in rec]
    return rec


def _write(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(_clean(r), ensure_ascii=False) + "\n")
            n += 1
    return n


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), indent=1, ensure_ascii=False))


def export(w, tl, scen: dict, report: dict, out: Path, render_mode: str, sim) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    W, EV, AR, TR, SC = out / "world", out / "events", out / "artifacts", out / "truth", out / "scenarios"
    counts["services"] = _write(W / "services.jsonl", w.services.values())
    counts["datastores"] = _write(W / "datastores.jsonl", w.datastores.values())
    counts["regions"] = _write(W / "regions.jsonl", w.regions)
    counts["endpoints"] = _write(W / "endpoints.jsonl", w.endpoints)
    counts["flags"] = _write(W / "flags.jsonl", w.flags)
    counts["teams"] = _write(W / "teams.jsonl", w.teams.values())
    counts["people"] = _write(W / "people.jsonl", w.people.values())
    counts["memberships"] = _write(W / "memberships.jsonl", w.memberships)
    counts["ownership"] = _write(W / "ownership.jsonl", w.ownership)
    counts["reporting"] = _write(W / "reporting.jsonl", w.reporting)
    counts["dependencies"] = _write(W / "dependencies.jsonl", w.dependencies)
    counts["oncall"] = _write(W / "oncall.jsonl", w.oncall)
    counts["changes"] = _write(EV / "changes.jsonl", tl.changes)
    counts["alerts"] = _write(EV / "alerts.jsonl", tl.alerts)
    counts["incidents"] = _write(EV / "incidents.jsonl", tl.incidents)
    counts["org_events"] = _write(EV / "org_events.jsonl", w.org_events)
    counts["threads"] = _write(AR / "threads.jsonl", tl.threads)
    counts["messages"] = _write(AR / "messages.jsonl", ({k: v for k, v in m.items() if k not in ("facts", "intent")} for m in tl.messages))
    counts["tickets"] = _write(AR / "tickets.jsonl", ({k: v for k, v in t.items() if k != "facts"} for t in tl.tickets))
    counts["docs"] = _write(AR / "docs.jsonl", ({k: v for k, v in d.items() if k != "facts"} for d in tl.docs))
    counts["runbooks"] = _write(AR / "runbooks.jsonl", tl.runbooks)
    counts["decisions_meta"] = _write(AR / "decisions_meta.jsonl", ({k: v for k, v in d.items() if k not in ("facts", "topology_change")} for d in tl.decisions))
    # truth (never loaded into the graph)
    _write(TR / "incidents_truth.jsonl", tl.incidents_truth)
    _write(TR / "extraction_gold.jsonl", tl.extraction_gold)
    _write(TR / "fact_cards.jsonl", ([{"id": m["id"], "intent": m["intent"], "facts": m["facts"]} for m in tl.messages] +
                                     [{"id": x["id"], "facts": x["facts"]} for x in tl.tickets + tl.docs + tl.decisions]))
    from .timeline import staleness_truth
    _dump(TR / "staleness_truth.json", staleness_truth(w, tl))
    tq = w.cfg.test_start
    _dump(TR / "expertise_truth.json", {e: sim.experts(e, tq, 5) for e in w.entities()})
    _dump(TR / "latent.json", {"fragility": w.fragility, "review_rigor": w.review_rigor, "expertise": w.expertise,
                               "special_people": w.special, "motifs": list(tl.motifs.values()),
                               "injection_artifact_ids": getattr(tl, "injection_ids", [])})
    for s in scen.get("test", []):
        _dump(SC / "test" / f"{s['id']}.json", s)
    for s in scen.get("train", []):
        _dump(SC / "train" / f"{s['id']}.json", s)
    for s in scen.get("learning", []):
        _dump(SC / "learning" / f"{s['id']}.json", s)
    for s in scen.get("hero", []):
        _dump(SC / "hero" / f"{s['id']}.json", s)
    if scen.get("probes"):
        _dump(SC / "probes" / "knowledge_update.json", scen["probes"]["knowledge_update"])
        _dump(SC / "probes" / "brain_qa.json", scen["probes"]["brain_qa"])
    node_est = (len(w.services) + len(w.datastores) + len(w.regions) + len(w.endpoints) + len(w.flags) + len(w.teams) +
                len(w.people) + len(w.oncall) + len(tl.changes) + len(tl.alerts) + len(tl.incidents) + len(tl.tickets) +
                len(tl.threads) + len(tl.messages) + len(tl.docs) + len(tl.decisions) + len(tl.runbooks) +
                sum(len(r["steps"]) for r in tl.runbooks))
    checks = {}
    for p in sorted(out.rglob("*.jsonl")):
        checks[str(p.relative_to(out))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    manifest = {"tenant": w.cfg.tenant, "company": w.cfg.company, "seed": w.cfg.seed, "render_mode": render_mode,
                "history_window": [iso(w.cfg.history_start), iso(w.cfg.history_end)],
                "test_window": [iso(w.cfg.test_start), iso(w.cfg.test_end)],
                "counts": counts, "runbook_steps": sum(len(r["steps"]) for r in tl.runbooks),
                "estimated_graph_nodes_before_extraction": node_est,
                "change_kinds": Counter(c["kind"] for c in tl.changes), "incident_causes": Counter(t["cause_type"] for t in tl.incidents_truth),
                "severity": Counter(f"SEV{i['sev']}" for i in tl.incidents),
                "silent_root_share": round(sum(t["silent_root"] for t in tl.incidents_truth) / max(1, len(tl.incidents)), 3),
                "scenarios": {k: len(v) for k, v in scen.items() if isinstance(v, list)},
                "validation": report, "checksums": checks}
    _dump(out / "manifest.json", manifest)
    return manifest
