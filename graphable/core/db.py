"""Tenant-scoped FalkorDB access (P1, P7, P8). The tenant is chosen here, from auth context,
never from LLM-supplied arguments. Reads go through GRAPH.RO_QUERY with a timeout."""
from __future__ import annotations

import threading
from functools import lru_cache

from falkordb import FalkorDB
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from redis.retry import Retry

from ..settings import settings


@lru_cache(maxsize=1)
def client() -> FalkorDB:
    # a remote database (FalkorDB Cloud) can drop idle pooled connections: check them before use, reconnect and retry
    return FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port,
                    username=settings.falkordb_username, password=settings.falkordb_password,
                    retry=Retry(ExponentialBackoff(cap=2, base=0.2), 3), retry_on_error=[RedisConnectionError, RedisTimeoutError],
                    health_check_interval=30, socket_keepalive=True)


@lru_cache(maxsize=1)
def timeout_max() -> int:
    """Server-side TIMEOUT_MAX (0 = unlimited). A query asking for more is rejected by FalkorDB, so we cap to it."""
    try:
        v = client().connection.execute_command("GRAPH.CONFIG", "GET", "TIMEOUT_MAX")
        return int(v[1] if isinstance(v, (list, tuple)) else v)
    except Exception:
        return 0


def _t(timeout: int | None) -> int:
    t = timeout or settings.query_timeout_ms
    m = timeout_max()
    return min(t, m) if m else t


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
        res = self.g.ro_query(q, params or {}, timeout=_t(timeout))
        return _rows(res)

    def write(self, q: str, params: dict | None = None, timeout: int | None = None) -> list[dict]:
        res = self.g.query(q, params or {}, timeout=_t(timeout))
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
