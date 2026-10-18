"""Tenant-scoped FalkorDB access (P1, P7, P8). The tenant is chosen here, from auth context,
never from LLM-supplied arguments. Reads go through GRAPH.RO_QUERY with a timeout."""
from __future__ import annotations

import threading
from functools import lru_cache

from falkordb import FalkorDB

from ..settings import settings


@lru_cache(maxsize=1)
def client() -> FalkorDB:
    return FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port,
                    username=settings.falkordb_username, password=settings.falkordb_password)


def graph_name(tenant: str) -> str:
    if not tenant.replace("_", "").isalnum():
        raise ValueError("invalid tenant")
    return f"t_{tenant}"


class TenantGraph:
    """All Cypher for one tenant goes through here."""

    _lock = threading.Lock()

    def __init__(self, tenant: str):
        self.tenant = tenant
        self.name = graph_name(tenant)
        self.g = client().select_graph(self.name)

    def read(self, q: str, params: dict | None = None, timeout: int | None = None) -> list[dict]:
        res = self.g.ro_query(q, params or {}, timeout=timeout or settings.query_timeout_ms)
        return _rows(res)

    def write(self, q: str, params: dict | None = None, timeout: int | None = None) -> list[dict]:
        res = self.g.query(q, params or {}, timeout=timeout or settings.query_timeout_ms)
        return _rows(res)

    def exists(self) -> bool:
        return self.name in client().list_graphs()


def _rows(res) -> list[dict]:
    if not res.header:
        return []
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    out = []
    for r in res.result_set:
        out.append({c: _plain(v) for c, v in zip(cols, r)})
    return out


def _plain(v):
    """Convert falkordb Node/Edge/Path objects to plain dicts."""
    cls = type(v).__name__
    if cls == "Node":
        return {"_labels": list(v.labels), **v.properties}
    if cls == "Edge":
        return {"_type": v.relation, **v.properties}
    if cls == "Path":
        return {"nodes": [_plain(n) for n in v.nodes()], "edges": [_plain(e) for e in v.edges()]}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v
