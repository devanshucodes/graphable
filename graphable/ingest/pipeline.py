"""Ingestion pipeline: extract -> resolve -> validate -> contradiction check -> templated write.

  python -m graphable.ingest.pipeline --tenant acme            # backfill all prose already in the graph
Live ingest (one new message/doc) goes through ingest_message()/ingest_doc(), used by the API.
"""
from __future__ import annotations

import argparse
import time
import uuid
from collections import defaultdict

from ..core.db import TenantGraph
from ..settings import settings
from .extract import ALLOWED, Extraction, Gazetteer, GeminiExtractor, Rel, RulesExtractor

PREFIX = {"inc_": "Incident", "chg_": "Change", "svc_": "Component", "ds_": "Component", "rb_": "Runbook", "dec_": "Decision",
          "thr_": "Thread", "doc_": "Doc", "tkt_": "Ticket", "team_": "Team", "per_": "Person", "H3_msg": "Message", "msg_": "Message"}


def lbl(i: str) -> str | None:
    for p, l in PREFIX.items():
        if i.startswith(p):
            return l
    return None


def make_extractor(tg: TenantGraph, gz: Gazetteer):
    import os
    if settings.llm_enabled and os.getenv("EXTRACTOR", "gemini") == "gemini":
        return GeminiExtractor(tg, gz, settings.gemini_api_key, settings.gemini_model_flash)
    return RulesExtractor(tg, gz)


def validate(ex: Extraction) -> tuple[list[Rel], list[str]]:
    ok, rejected = [], []
    for r in ex.rels:
        key = (lbl(r.src), r.rel, lbl(r.dst))
        (ok if key in ALLOWED else rejected).append(r if key in ALLOWED else f"{r.src}-{r.rel}->{r.dst}")
    return ok, rejected


class Writer:
    def __init__(self, tg: TenantGraph, run_id: str):
        self.tg, self.run_id = tg, run_id
        self.buf = defaultdict(list)
        self.stats = defaultdict(int)

    def add(self, source_id: str, r: Rel):
        if r.rel == "OWNS":
            self.owns(source_id, r)
            return
        self.buf[(lbl(r.src), r.rel, lbl(r.dst))].append(
            {"src": r.src, "dst": r.dst, "source": source_id, "span": r.span, "conf": r.confidence, "props": str(r.props or "")})

    def owns(self, source_id: str, r: Rel):
        """Functional relation: exactly one active owner. Deterministic close-and-open in ONE statement (P6).
        If another source already recorded the same move within 3 days, reconcile onto that edge (earliest
        valid_from wins, both sources kept as provenance) instead of creating a duplicate."""
        source_id = r.props.get("message_id", source_id)
        vf = r.valid_from or int(time.time() * 1000)
        near = self.tg.read("""MATCH (t:Team {id:$t})-[o:OWNS]->(c:Component {id:$c})
            WHERE o.valid_from >= $vf - 259200000 AND o.valid_from <= $vf + 259200000 RETURN o.valid_from AS vf""",
                            {"t": r.src, "c": r.dst, "vf": vf})
        if near:
            other = near[0]["vf"]
            new_vf = min(vf, other)
            self.tg.write("""MATCH (c:Component {id:$c})<-[prev:OWNS]-(:Team) WHERE prev.valid_to = $other
                SET prev.valid_to = $nvf
                WITH DISTINCT c MATCH (t:Team {id:$t})-[o:OWNS]->(c) WHERE o.valid_from = $other
                SET o.valid_from = $nvf, o.corroborated_by = coalesce(o.corroborated_by, []) + [$src]""",
                          {"c": r.dst, "t": r.src, "other": other, "nvf": new_vf, "src": source_id})
            self.stats["OWNS reconciled"] += 1
            return
        cur = self.tg.read("""MATCH (t:Team)-[o:OWNS]->(c:Component {id:$c}) WHERE o.valid_from <= $vf AND (o.valid_to IS NULL OR o.valid_to > $vf)
            RETURN t.id AS team""", {"c": r.dst, "vf": vf})
        if cur and cur[0]["team"] == r.src:
            self.tg.write("""MATCH (t:Team {id:$t})-[o:OWNS]->(c:Component {id:$c}) WHERE o.valid_from <= $vf AND (o.valid_to IS NULL OR o.valid_to > $vf)
                SET o.corroborated_by = coalesce(o.corroborated_by, []) + [$src]""", {"t": r.src, "c": r.dst, "vf": vf, "src": source_id})
            self.stats["OWNS corroborated"] += 1
            return
        self.tg.write("""MATCH (c:Component {id:$c})
            OPTIONAL MATCH (:Team)-[o:OWNS]->(c) WHERE o.valid_from <= $vf AND (o.valid_to IS NULL OR o.valid_to > $vf)
            SET o.valid_to = $vf, o.expired_at = $now, o.invalidated_by = $src
            WITH DISTINCT c MATCH (t:Team {id:$t})
            CREATE (t)-[:OWNS {valid_from:$vf, recorded_at:$now, source_id:$src, span:$span, extracted_by:$run, confidence:$conf}]->(c)""",
                      {"c": r.dst, "t": r.src, "vf": vf, "now": int(time.time() * 1000), "src": source_id, "span": r.span,
                       "run": self.run_id, "conf": r.confidence})
        self.stats["OWNS changed"] += 1

    def flush(self):
        now = int(time.time() * 1000)
        for (l1, rel, l2), rows in self.buf.items():
            for i in range(0, len(rows), 2000):
                self.tg.write(f"""UNWIND $rows AS r MATCH (a:{l1} {{id:r.src}}), (b:{l2} {{id:r.dst}})
                    MERGE (a)-[e:{rel}]->(b)
                    ON CREATE SET e.source_id = r.source, e.span = r.span, e.confidence = r.conf, e.extracted_by = $run, e.recorded_at = $now
                    ON MATCH SET e.corroborations = coalesce(e.corroborations, 0) + 1""",
                              {"rows": rows[i:i + 2000], "run": self.run_id, "now": now}, timeout=120_000)
            self.stats[f"{l1}-{rel}->{l2}"] += len(rows)
        self.buf.clear()


def _threads(tg: TenantGraph, ids: list[str] | None = None) -> list[tuple[dict, list[dict]]]:
    # FalkorDB caps result sets (RESULTSET_SIZE, 10k by default): page by thread ids.
    ids = ids or [r["id"] for r in tg.read("MATCH (t:Thread) RETURN t.id AS id")]
    rows = []
    for i in range(0, len(ids), 400):
        rows += tg.read("""MATCH (m:Message)-[:IN]->(t:Thread) WHERE t.id IN $ids MATCH (p:Person)-[:SENT]->(m)
            OPTIONAL MATCH (t)-[:ABOUT]->(i:Incident)
            RETURN t.id AS tid, t.kind AS kind, t.channel AS channel, i.id AS inc, i.key AS inc_key,
                   m.id AS id, m.ts AS ts, m.text AS text, p.id AS author_id, p.handle AS author_handle ORDER BY m.ts""",
                        {"ids": ids[i:i + 400]}, timeout=120_000)
    rows.sort(key=lambda r: r["ts"])
    by = {}
    for r in rows:
        th = by.setdefault(r["tid"], ({"id": r["tid"], "kind": r["kind"], "channel": r["channel"], "incident_id": r["inc"],
                                        "incident_key": r["inc_key"]}, []))
        th[1].append(r)
    return list(by.values())


def run(tenant: str, limit: int | None = None) -> dict:
    tg = TenantGraph(tenant)
    t0 = time.time()
    gz = Gazetteer(tg)
    ex = make_extractor(tg, gz)
    run_id = "xr_" + uuid.uuid4().hex[:8]
    tg.write("CREATE (:ExtractionRun {id:$id, model:$m, prompt_version:$v, at:timestamp()})", {"id": run_id, "m": ex.name, "v": "v1"})
    w = Writer(tg, run_id)
    rejected, unresolved, n_src = 0, [], 0
    from concurrent.futures import ThreadPoolExecutor
    workers = 20 if isinstance(ex, GeminiExtractor) else 1
    threads = _threads(tg)[:limit]
    docs = tg.read("""MATCH (d:Doc) OPTIONAL MATCH (d)-[:POSTMORTEM_OF]->(i:Incident)
        RETURN d.id AS id, d.kind AS kind, d.text AS text, d.created_at AS created_at, i.id AS incident_id""")[:limit]
    tickets = tg.read("MATCH (t:Ticket) RETURN t.id AS id, t.title AS title, t.text AS text")[:limit]
    rules = RulesExtractor(tg, gz)

    def safe(fn, fallback, *args):  # an LLM failure on one source falls back to rules for that source
        try:
            return fn(*args)
        except Exception:
            return fallback(*args)
    with ThreadPoolExecutor(workers) as pool:  # LLM extraction is I/O bound; rules extraction stays single-threaded
        srcs = list(pool.map(lambda x: safe(ex.thread, rules.thread, *x), threads)) + \
            list(pool.map(lambda d: safe(ex.doc, rules.doc, d), docs))
    srcs += [ex.ticket(t) for t in tickets]
    for e in srcs:
        n_src += 1
        ok, bad = validate(e)
        rejected += len(bad)
        unresolved += e.unresolved
        for r in ok:
            w.add(e.source_id, r)
    w.flush()
    for u in set(unresolved):
        tg.write("MERGE (u:Unresolved {id:$id}) ON CREATE SET u.text = $t, u.first_seen = timestamp()", {"id": "unr_" + u.lower().replace(" ", "_"), "t": u})
    tg.write("MATCH (x:ExtractionRun {id:$id}) SET x.n_sources = $n, x.rejected = $r, x.seconds = $s",
             {"id": run_id, "n": n_src, "r": rejected, "s": round(time.time() - t0, 1)})
    out = {"run_id": run_id, "extractor": ex.name, "sources": n_src, "rejected": rejected, "unresolved": len(set(unresolved)),
           "written": dict(w.stats), "seconds": round(time.time() - t0, 1)}
    print(out)
    return out


# ------------------------------------------------------------------ live ingest (API)
def ingest_message(tenant: str, msg: dict, channel: str = "#announcements") -> dict:
    """msg: {id, author_id, ts, text, thread_id?}. Writes the Message, then extracts from its thread."""
    tg = TenantGraph(tenant)
    tid = msg.get("thread_id") or ("thr_live_" + msg["id"])
    tg.write("""MERGE (t:Thread {id:$tid}) ON CREATE SET t.channel = $ch, t.started_at = $ts, t.kind = 'live', t.title = left($text, 80)
        WITH t MATCH (p:Person {id:$a})
        MERGE (m:Message {id:$id}) ON CREATE SET m.ts = $ts, m.text = $text
        MERGE (m)-[:IN]->(t) MERGE (p)-[:SENT]->(m)""",
             {"tid": tid, "ch": channel, "ts": msg["ts"], "text": msg["text"], "a": msg["author_id"], "id": msg["id"]})
    gz = Gazetteer(tg)
    ex = make_extractor(tg, gz)
    run_id = "xr_" + uuid.uuid4().hex[:8]
    th, msgs = _threads(tg, [tid])[0]
    e = ex.thread(th, msgs)
    ok, bad = validate(e)
    w = Writer(tg, run_id)
    for r in ok:
        w.add(msg["id"] if r.rel == "OWNS" else e.source_id, r)
    w.flush()
    return {"thread_id": tid, "facts": [{"src": r.src, "rel": r.rel, "dst": r.dst, "valid_from": r.valid_from, "span": r.span} for r in ok],
            "rejected": bad, "written": dict(w.stats), "unresolved": e.unresolved}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="acme")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(a.tenant, a.limit)


if __name__ == "__main__":
    main()
