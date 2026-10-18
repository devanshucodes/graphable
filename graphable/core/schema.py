"""Schema migration: range indexes, unique constraints, full-text indexes (ARCHITECTURE §5.4).

Service and Datastore nodes also carry the shared label :Component, so traversals can use a
single label (FalkorDB has no label alternation in patterns)."""
from __future__ import annotations

import time

from .db import TenantGraph

ID_LABELS = ["Component", "Service", "Datastore", "Region", "Endpoint", "FeatureFlag", "Team", "Person", "OnCallShift",
             "Change", "Alert", "Incident", "Thread", "Message", "Ticket", "Doc", "Runbook", "Step", "Decision",
             "Agent", "AgentRun", "AgentStep", "Task", "Handoff", "IncidentBrief", "Evidence", "Hypothesis", "Action",
             "Lesson", "Reflection", "Claim", "ExtractionRun", "Unresolved", "Session", "Turn", "Answer"]
EXTRA_RANGE = [("Component", "name"), ("Service", "name"), ("Datastore", "name"), ("Change", "ts"), ("Alert", "ts"),
               ("Incident", "started_at"), ("Incident", "key"), ("Message", "ts"), ("Person", "handle"),
               ("Task", "status"), ("Runbook", "key"), ("Decision", "key"), ("Ticket", "key"), ("Action", "idem_key")]
UNIQUE = ["Component", "Person", "Team", "Change", "Incident", "Message", "Thread", "Ticket", "Doc", "Runbook",
          "Decision", "Evidence", "Action"]
FULLTEXT = [("Runbook", ["title", "text"]), ("Doc", ["title", "text"]), ("Ticket", ["title", "text"]),
            ("Message", ["text"]), ("Component", ["name", "aliases_text"]), ("Person", ["name", "handle"]),
            ("Incident", ["title"])]


def _try(tg: TenantGraph, q: str):
    try:
        tg.write(q)
    except Exception as e:  # index already exists, etc.
        if "already" not in str(e).lower():
            raise


def migrate(tg: TenantGraph, constraints: bool = True):
    for lbl in ID_LABELS:
        _try(tg, f"CREATE INDEX FOR (n:{lbl}) ON (n.id)")
    for lbl, prop in EXTRA_RANGE:
        _try(tg, f"CREATE INDEX FOR (n:{lbl}) ON (n.{prop})")
    _try(tg, "CREATE INDEX FOR ()-[r:DEPENDS_ON]-() ON (r.valid_to)")
    _try(tg, "CREATE INDEX FOR ()-[r:OWNS]-() ON (r.valid_to)")
    for lbl, props in FULLTEXT:
        _try(tg, f"CREATE FULLTEXT INDEX FOR (n:{lbl}) ON ({', '.join('n.' + p for p in props)})")
    if constraints:
        for lbl in UNIQUE:
            try:
                tg.g.execute_command("GRAPH.CONSTRAINT", "CREATE", tg.name, "UNIQUE", "NODE", lbl, "PROPERTIES", "1", "id")
            except Exception as e:
                if "already" not in str(e).lower():
                    raise
        # constraints become OPERATIONAL asynchronously
        for _ in range(50):
            rows = tg.read("CALL db.constraints() YIELD status RETURN collect(status) AS s")
            if all(s == "OPERATIONAL" for s in rows[0]["s"]):
                break
            time.sleep(0.1)


def ensure_vector_indexes(tg: TenantGraph, dim: int):
    """Created only when embeddings are enabled (vectors must be written with vecf32())."""
    for lbl in ["Incident", "Thread", "Doc", "Ticket", "Runbook"]:
        _try(tg, f"CREATE VECTOR INDEX FOR (n:{lbl}) ON (n.embedding) OPTIONS {{dimension:{dim}, similarityFunction:'cosine'}}")
