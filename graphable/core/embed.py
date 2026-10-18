"""Gemini embeddings (768-d) stored as FalkorDB vectors (vecf32) with HNSW cosine indexes.

  python -m graphable.core.embed --tenant acme
Text per label: Incident = title + alerting components + alert names; Thread = title + first messages;
Doc/Ticket/Runbook = title + body (truncated). Messages are not embedded individually (memory budget).
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor

from ..settings import settings
from .db import TenantGraph
from .schema import ensure_vector_indexes

BATCH = 64


def _client():
    from google import genai
    return genai.Client(api_key=settings.gemini_api_key)


def embed_texts(texts: list[str], task: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    c = _client()
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = [t[:6000] or " " for t in texts[i:i + BATCH]]
        for attempt in range(5):
            try:
                r = c.models.embed_content(model=settings.embed_model, contents=chunk,
                                           config={"output_dimensionality": settings.embed_dim, "task_type": task})
                out += [e.values for e in r.embeddings]
                break
            except Exception:
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError("embedding failed after retries")
    return out


def embed_query(text: str) -> list[float]:
    return embed_texts([text], "RETRIEVAL_QUERY")[0]


SOURCES = {
    "Incident": """MATCH (n:Incident) WHERE n.embedding IS NULL
        RETURN n.id AS id, n.title + ' | ' + reduce(s='', c IN [(n)-[:AFFECTED]->(x) | x.name] | s + ' ' + c) + ' | ' +
               reduce(s='', a IN [(n)<-[:PART_OF]-(al:Alert) | al.name] | s + ' ' + a) AS text""",
    "Thread": """MATCH (n:Thread) WHERE n.embedding IS NULL
        RETURN n.id AS id, coalesce(n.title, '') + ' | ' + reduce(s='', t IN [(m:Message)-[:IN]->(n) | m.text][0..6] | s + ' ' + t) AS text""",
    "Doc": "MATCH (n:Doc) WHERE n.embedding IS NULL RETURN n.id AS id, coalesce(n.title, '') + ' | ' + substring(coalesce(n.text, ''), 0, 4000) AS text",
    "Ticket": "MATCH (n:Ticket) WHERE n.embedding IS NULL RETURN n.id AS id, coalesce(n.title, '') + ' | ' + substring(coalesce(n.text, ''), 0, 2000) AS text",
    "Runbook": "MATCH (n:Runbook) WHERE n.embedding IS NULL RETURN n.id AS id, coalesce(n.title, '') + ' | ' + coalesce(n.text, '') AS text",
}


def embed_tenant(tenant: str, labels: list[str] | None = None, workers: int = 6) -> dict:
    tg = TenantGraph(tenant)
    ensure_vector_indexes(tg, settings.embed_dim)
    stats = {}
    for lbl in labels or list(SOURCES):
        rows = []
        while True:  # page past RESULTSET_SIZE
            page = tg.read(SOURCES[lbl] + f" SKIP {len(rows)} LIMIT 5000", timeout=60_000)
            rows += page
            if len(page) < 5000:
                break
        t = time.time()
        chunks = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
        with ThreadPoolExecutor(workers) as ex:
            vecs = list(ex.map(lambda ch: embed_texts([r["text"] or "" for r in ch]), chunks))
        for ch, vs in zip(chunks, vecs):
            tg.write(f"UNWIND $rows AS r MATCH (n:{lbl} {{id:r.id}}) SET n.embedding = vecf32(r.v)",
                     {"rows": [{"id": r["id"], "v": v} for r, v in zip(ch, vs)]}, timeout=60_000)
        stats[lbl] = {"n": len(rows), "seconds": round(time.time() - t, 1)}
        print(f"  embedded {lbl:<9} {len(rows):>6}  ({stats[lbl]['seconds']}s)")
    return stats


def embed_incident(tg: TenantGraph, incident_id: str) -> bool:
    """Embed one live incident at intake (when LLM is enabled)."""
    if not settings.llm_enabled:
        return False
    r = tg.read(SOURCES["Incident"].replace("WHERE n.embedding IS NULL", "WHERE n.id = $id"), {"id": incident_id})
    if not r:
        return False
    v = embed_texts([r[0]["text"]])[0]
    tg.write("MATCH (n:Incident {id:$id}) SET n.embedding = vecf32($v)", {"id": incident_id, "v": v})
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="acme")
    a = ap.parse_args()
    print(embed_tenant(a.tenant))


if __name__ == "__main__":
    main()
