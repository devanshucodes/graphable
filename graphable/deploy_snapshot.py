"""Build the hosted demo snapshot: one trimmed copy of the demo graph that fits FalkorDB Cloud's free tier (100 MB).

  python -m graphable.deploy_snapshot --src-port 6379 --scratch-port 6395 --out deploy/demo_snapshot.dump

Starts from the golden graph (the state every demo run begins from), works on a scratch FalkorDB instance:
  1. removes run-time records (agent runs, evidence, answers, sessions, simulated incidents ...),
  2. drops the vector embeddings and indexes on Ticket and Thread (the largest; ~40 MB). Incident, Doc and Runbook keep
     theirs, so similar-incident recall and semantic search over docs/runbooks still work; tickets and chats stay
     full-text searchable,
  3. writes a DUMP of the result. The API restores it with RESTORE (DEMO_SNAPSHOT=...), so the hosted database only
     ever holds one copy, and a database that restarts empty is refilled automatically.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import redis

RUNTIME_LABELS = ["AgentStep", "AgentRun", "Evidence", "Answer", "Session", "Turn", "Hypothesis", "Handoff", "Action",
                  "Task", "Lesson", "Reflection", "IncidentBrief"]
TRIM_VECTORS = ["Ticket", "Thread"]


def build(src_port: int, scratch_port: int, out: Path, src_graph: str = "golden_acme", name: str = "t_demo_main") -> dict:
    src, dst = redis.Redis(port=src_port), redis.Redis(port=scratch_port)
    dst.delete(name)
    dst.restore(name, 0, src.dump(src_graph))
    q = lambda c: dst.execute_command("GRAPH.QUERY", name, c, "--compact")
    for lbl in RUNTIME_LABELS:
        q(f"MATCH (n:{lbl}) DETACH DELETE n")
    for prefix, lbl in (("inc_h", "Incident"), ("alt_h", "Alert"), ("chg_h", "Change"), ("msg_live", "Message"),
                        ("msg_h", "Message"), ("thr_live", "Thread")):
        q(f"MATCH (n:{lbl}) WHERE n.id STARTS WITH '{prefix}' DETACH DELETE n")
    for lbl in TRIM_VECTORS:
        try:
            q(f"DROP VECTOR INDEX FOR (n:{lbl}) ON (n.embedding)")
        except redis.ResponseError:
            pass
        q(f"MATCH (n:{lbl}) WHERE n.embedding IS NOT NULL SET n.embedding = NULL")
    payload = dst.dump(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    # measure what one fresh copy costs in memory
    dst.delete(name)
    time.sleep(1)
    base = dst.info("memory")["used_memory"]
    dst.restore(name, 0, payload)
    time.sleep(2)
    mem = dst.info("memory")["used_memory"] - base
    nodes = dst.execute_command("GRAPH.QUERY", name, "MATCH (n) RETURN count(n)")[1][0][0]
    edges = dst.execute_command("GRAPH.QUERY", name, "MATCH ()-[r]->() RETURN count(r)")[1][0][0]
    return {"snapshot_mb": round(len(payload) / 1e6, 1), "memory_mb": round(mem / 1e6, 1), "nodes": nodes, "edges": edges}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-port", type=int, default=6379)
    ap.add_argument("--scratch-port", type=int, required=True)
    ap.add_argument("--out", default="deploy/demo_snapshot.dump")
    a = ap.parse_args()
    print(build(a.src_port, a.scratch_port, Path(a.out)))


if __name__ == "__main__":
    main()
