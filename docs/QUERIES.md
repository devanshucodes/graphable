# Cypher queries and graph algorithms Graphable depends on

These are the actual templates from `graphable/core/tools.py`, `graphable/sim/player.py`, `graphable/ingest/pipeline.py` and `graphable/agents/*`.

**Rules for every query:**
- Parameters are always passed as parameters.
- Hop bounds and relationship types come from code-side allowlists and are inlined only after validation, because FalkorDB can't parameterize them.
- Reads run through `GRAPH.RO_QUERY` with a timeout.
- Every grouping key is `coalesce()`d to avoid FalkorDB 6.0.x issue #3150.

`VALID(r)` below stands for the bi-temporal predicate
`r.valid_from <= $at AND (r.valid_to IS NULL OR r.valid_to > $at)`.

---

## 1. Blast radius: who is impacted if X fails (walk against `DEPENDS_ON`)
```cypher
MATCH p=(up:Component)-[:DEPENDS_ON*1..{h}]->(s:Component {id:$id})
WHERE all(r IN relationships(p) WHERE VALID(r))
WITH up, p ORDER BY length(p) WITH up, collect(p)[0] AS p
OPTIONAL MATCH (t:Team)-[o:OWNS]->(up) WHERE VALID(o)
RETURN up.id AS id, up.name AS name, up.tier AS tier, coalesce(up.entry_point, false) AS entry_point,
       coalesce(up.customer_facing, false) AS customer_facing, length(p) AS hops, coalesce(t.name, '') AS owner,
       [n IN nodes(p) | n.id] AS path ORDER BY hops, tier
```

## 2. Root-cause ranking (parsimonious cover)
This runs as five queries; scoring happens in Python.

**Candidates:** every component downstream of the alerting set, with the alerting services that reach it and one shortest path, which becomes the evidence.
```cypher
MATCH p=(a:Component)-[:DEPENDS_ON*0..{H}]->(c:Component)
WHERE a.id IN $A AND all(r IN relationships(p) WHERE VALID(r))
WITH c, a, p ORDER BY length(p)
WITH c, collect(DISTINCT a.id) AS srcs, collect(p)[0] AS sp
RETURN c.id AS c, c.name AS name, c.kind AS kind, length(sp) AS h, srcs, [n IN nodes(sp) | n.id] AS path
```
**Precision:** how much of each candidate's up-closure is alerting. Hubs only win when nearly everything alerts.
```cypher
MATCH p=(u:Component)-[:DEPENDS_ON*1..3]->(c:Component)
WHERE c.id IN $C AND all(r IN relationships(p) WHERE VALID(r))
WITH c, collect(DISTINCT u.id) AS ups
RETURN c.id AS c, size(ups) AS n_up, size([x IN ups WHERE x IN $A]) AS n_up_alerting
```
**Minimality:** which candidates lie downstream of which, used for the "minimal explanation" term.
```cypher
MATCH p=(c:Component)-[:DEPENDS_ON*1..{H}]->(d:Component)
WHERE c.id IN $C AND d.id IN $C AND all(r IN relationships(p) WHERE VALID(r)) RETURN DISTINCT c.id AS c, d.id AS d
```
**Changes in the window** on candidate components:
```cypher
MATCH (ch:Change)-[:CHANGED]->(c:Component) WHERE c.id IN $C AND ch.ts >= $lo AND ch.ts <= $hi AND ch.rollback_of IS NULL
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(ch)
RETURN ch.id AS id, ch.kind AS kind, ch.ts AS ts, ch.summary AS summary, c.id AS c, coalesce(a.id, '') AS author
```
**Track record (learned memory):** rollbacks plus confirmed causes, per component × kind and per change signature.
```cypher
MATCH (ch:Change)-[:CHANGED]->(c:Component) WHERE c.id IN $C AND ch.ts < $lo AND ch.rollback_of IS NULL
RETURN c.id AS c, ch.kind AS kind, count(ch) AS n,
       sum(CASE WHEN coalesce(ch.rolled_back, false) OR size([(i:Incident)-[:CAUSED_BY]->(ch) | i]) > 0 THEN 1 ELSE 0 END) AS bad
```
```cypher
MATCH (i:Incident)-[:CAUSED_BY]->(ch:Change)-[:CHANGED]->(c:Component)
WHERE c.id IN $C AND i.started_at < $lo RETURN c.id AS c, ch.kind AS kind, count(i) AS n
```
**Scoring (Python):**
- `Cov = sqrt(E·Prec)` (Ochiai)
- `F` = no downstream candidate explains as much
- `R` = recency, with changes after detection discounted
- `K` = empirical-Bayes change-signature risk
- `H` = history
- `P = 2/(2+hops)`
- `S = Σ w·term`, using weights tuned on the train scenarios. The answer abstains to "not a change" below θ.

**Tie-break:** personalized PageRank on the incident subgraph (networkx, MicroRCA-style).

## 3. Similar past incidents (episodic memory)
```cypher
MATCH (i:Incident)-[:AFFECTED]->(e:Component) WHERE e.id IN $S AND i.id <> $id AND i.started_at < $t0
WITH DISTINCT i
RETURN i.id AS id, i.key AS key, i.title AS title, i.started_at AS t, i.sev AS sev,
       [(i)-[:AFFECTED]->(x) | x.id] AS affected, [(i)<-[:PART_OF]-(a:Alert) | a.name] AS alerts,
       [(i)-[:CAUSED_BY]->(c:Change)-[:CHANGED]->(r) | [c.id, c.kind, r.id]] AS causes,
       [(i)-[:CAUSED_BY]->(r:Component) | r.id] AS nc_roots,
       [(i)-[:RESOLVED_BY]->(p:Person) | [p.id, p.name, p.handle, p.left_at]] AS resolvers,
       [(i)-[:MITIGATED_WITH]->(rb:Runbook) | [rb.id, rb.key]] AS runbooks
```
Similarity (Python) = root proximity + change kind + Jaccard of symptoms and blast radius + text, plus a recency bonus.

## 4. Ownership as of a time, on-call, escalation (bi-temporal)
```cypher
MATCH (t:Team)-[o:OWNS]->(e:Component {id:$id}) WHERE VALID(o)
RETURN t.id AS team_id, t.name AS team, o.valid_from AS since, o.source_id AS source
```
```cypher
MATCH (p:Person)-[:HAS_SHIFT]->(s:OnCallShift)-[:COVERS]->(t:Team {id:$tid})
WHERE s.start <= $at AND s.end > $at
RETURN p.id AS id, p.name AS name, s.level AS level, (p.left_at IS NULL OR p.left_at > $at) AS active
```

## 5. Who actually knows X (expertise from evidence, with time decay)
This is several signal queries, combined in Python with weights and half-life decay. Examples:
```cypher
MATCH (p:Person)-[r:RESPONDED]->(i:Incident)-[:AFFECTED]->(:Component {id:$id}) WHERE i.started_at <= $at
RETURN p.id AS p, r.role AS role, i.started_at AS t, i.id AS src
```
```cypher
MATCH (t:Ticket)-[:ABOUT]->(:Component {id:$id}), (t)-[:RESOLVED_BY]->(p:Person) WHERE t.resolved_at <= $at
RETURN p.id AS p, t.resolved_at AS t, t.id AS src
```
```cypher
MATCH (p:Person)-[:SENT]->(m:Message)-[:IN]->(th:Thread)-[:MENTIONS]->(:Component {id:$id})
WHERE th.kind = 'help' AND m.ts <= $at RETURN DISTINCT p.id AS p, th.started_at AS t, th.id AS src
```
Bus factor = the number of active people whose score is ≥ 0.75 × the top active score. Departed experts are reported separately.

## 6. Staleness (DOCER-style rules)
```cypher
MATCH (:Component {id:$id})-[r:DEPENDS_ON]->(x:Component)
RETURN x.name AS n, sum(CASE WHEN r.valid_to IS NULL OR r.valid_to > $at THEN 1 ELSE 0 END) AS live
```
A runbook is stale if:
- it mentions a dependency with `live = 0`, or
- its `last_verified_at` is more than 90 days before the latest change, or
- its author has `left_at` set.

## 7. Decision trace (provenance)
```cypher
MATCH (d:Decision {id:$id})
RETURN d.key AS key, d.title AS title, d.status AS status,
       [(d)-[:DOCUMENTED_IN]->(doc:Doc) | {id: doc.id, title: doc.title}] AS docs,
       [(d)-[r:DISCUSSED_IN]->(t:Thread) | {id: t.id, channel: t.channel, source: r.source_id}] AS threads,
       [(d)-[:DECIDED_BY]->(p:Person) | {id: p.id, name: p.name}] AS decided_by,
       [(d)-[:SUPERSEDES]->(o:Decision) | {id: o.id, key: o.key}] AS supersedes,
       [(n:Decision)-[:SUPERSEDES]->(d) | {id: n.id, key: n.key}] AS superseded_by,
       [(d)-[:AFFECTS]->(c:Component) | {id: c.id, name: c.name}] AS affects
```

## 8. Graph-based alert correlation (intake)
Alerts correlate when their components are related, through a direct dependency or a 2-hop path whose middle node is **not a hub**, *and* the alerts are within 5 minutes of each other.
```cypher
MATCH p=(a:Component)-[:DEPENDS_ON*1..2]-(b:Component) WHERE a.id IN $E AND b.id IN $E AND a.id < b.id
  AND all(x IN relationships(p) WHERE x.valid_to IS NULL OR x.valid_to > $t)
  AND all(n IN nodes(p) WHERE n = a OR n = b OR size([(n)<-[:DEPENDS_ON]-(u) | u]) < 10)
RETURN DISTINCT a.id AS a, b.id AS b
```

## 9. Coordination: atomic compare-and-set task claim (one writer per graph)
```cypher
MATCH (t:Task {id:$id})
WHERE t.status = 'SUBMITTED' OR (t.status = 'WORKING' AND t.lease_until < $now)
SET t.status = 'WORKING', t.claimed_by = $agent, t.lease_until = $now + $lease, t.version = t.version + 1
RETURN t.version AS v          // empty => lost the claim
```

## 10. Idempotent, gated actions (no duplicate pages)
```cypher
MATCH (i:Incident {id:$inc})
MERGE (a:Action {idem_key:$idem})             // unique constraint on Action.id; idem = incident:kind:target
ON CREATE SET a.id = $id, a.kind = $kind, a.target = $target, a.tier = $tier, a.status = $status, a.created = true
ON MATCH SET a.created = false
MERGE (a)-[:FOR]->(i)
```

## 11. Functional-relation update from chat (close-and-open in one statement)
```cypher
MATCH (c:Component {id:$c})
OPTIONAL MATCH (:Team)-[o:OWNS]->(c) WHERE o.valid_from <= $vf AND (o.valid_to IS NULL OR o.valid_to > $vf)
SET o.valid_to = $vf, o.expired_at = $now, o.invalidated_by = $src
WITH DISTINCT c MATCH (t:Team {id:$t})
CREATE (t)-[:OWNS {valid_from:$vf, recorded_at:$now, source_id:$src, span:$span, extracted_by:$run, confidence:$conf}]->(c)
```

## 12. Extracted knowledge with provenance (batched, templated)
```cypher
UNWIND $rows AS r MATCH (a:{L1} {id:r.src}), (b:{L2} {id:r.dst})
MERGE (a)-[e:{REL}]->(b)
ON CREATE SET e.source_id = r.source, e.span = r.span, e.confidence = r.conf, e.extracted_by = $run, e.recorded_at = $now
ON MATCH SET e.corroborations = coalesce(e.corroborations, 0) + 1
```
`(L1, REL, L2)` is restricted to a schema allowlist, e.g. `(Incident, CAUSED_BY, Change)` or `(Decision, SUPERSEDES, Decision)`.

## 13. Learning from feedback (Scribe)
```cypher
MATCH (i:Incident {id:$i})-[s:SUSPECTED_CAUSE]->(x) SET s.confirmed = true
MERGE (i)-[c:CAUSED_BY]->(x) ON CREATE SET c.source_id = $by, c.confidence = 1.0, c.recorded_at = $now
```
When a diagnosis was wrong, the human-supplied cause becomes a `CAUSED_BY` edge plus a `(:Reflection)-[:ABOUT]->(:Component)` node. The next incident's ranking reads both through queries 2 and 3.

## 14. Built-in FalkorDB algorithms used
```cypher
CALL algo.pageRank('Component', 'DEPENDS_ON') YIELD node, score            // importance / blast weight (never cause)
CALL algo.betweenness({nodeLabels:['Component'], relationshipTypes:['DEPENDS_ON'], samplingSize:64, samplingSeed:42})
     YIELD node, score                                                       // chokepoints (fixed seed => reproducible)
MATCH (a {id:$a}), (b {id:$b}) WITH a, b
MATCH p=allShortestPaths((a)-[:DEPENDS_ON|OWNS|CHANGED|…*..{L}]-(b)) RETURN p   // explain a connection
CALL db.idx.fulltext.queryNodes('Component', $q) YIELD node, score         // entity resolution (aliases)
CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score               // knowledge search
```

## 15. Schema (migration)
- **Range indexes:** `id` on every label; also `Component.name`, `Change.ts`, `Alert.ts`, `Incident.started_at`, `Message.ts`, `Person.handle`, `Action.idem_key`, `DEPENDS_ON.valid_to`, `OWNS.valid_to`.
- **Full-text indexes** (DDL form): `Runbook(title,text)`, `Doc(title,text)`, `Ticket(title,text)`, `Message(text)`, `Component(name,aliases_text)`, `Person(name,handle)`, `Incident(title)`.
- **Unique constraints:** `GRAPH.CONSTRAINT CREATE … UNIQUE NODE <Label> PROPERTIES 1 id`, on Component, Person, Team, Change, Incident, Message, Thread, Ticket, Doc, Runbook, Decision, Evidence and Action.
- **Vector indexes** (768-d cosine) on Incident, Thread, Doc, Ticket and Runbook are created when Gemini embeddings are enabled. Vectors are always written with `vecf32()`.
