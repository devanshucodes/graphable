# Cypher queries and graph algorithms Graphable depends on

These are the actual templates from `graphable/core/tools.py`, `graphable/core/schema.py`, `graphable/core/embed.py`, `graphable/sim/player.py`, `graphable/ingest/pipeline.py` and `graphable/agents/*`.

**Rules for every query:**
- Values are always passed as parameters.
- Hop bounds, labels and relationship types come from code-side allowlists and are inlined only after validation, because FalkorDB can't parameterize them.
- Reads run through `GRAPH.RO_QUERY` with a timeout.
- Nullable grouping keys are `coalesce()`d (FalkorDB 6.0.x issue #3150: a null grouping key can crash the server).

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
This runs as several queries; scoring happens in Python.

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
MATCH (ch:Change)-[:CHANGED]->(c:Component) WHERE c.id IN $C AND ch.ts >= $lo AND ch.ts <= $hi
  AND ch.rollback_of IS NULL
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(ch)
RETURN ch.id AS id, ch.kind AS kind, ch.ts AS ts, ch.summary AS summary, coalesce(ch.canary, false) AS canary,
       c.id AS c, coalesce(a.id, '') AS author
```
**Track record (learned memory):** rollbacks plus confirmed causes, per component × kind.
```cypher
MATCH (ch:Change)-[:CHANGED]->(c:Component) WHERE c.id IN $C AND ch.ts < $lo AND ch.rollback_of IS NULL
RETURN c.id AS c, ch.kind AS kind, count(ch) AS n,
       sum(CASE WHEN coalesce(ch.rolled_back, false) OR size([(i:Incident)-[:CAUSED_BY]->(ch) | i]) > 0 THEN 1 ELSE 0 END) AS bad
```
**Change-signature track record** (e.g. the same config key), over all earlier changes, paged by `ts` because FalkorDB caps result sets (`RESULTSET_SIZE`):
```cypher
MATCH (ch:Change) WHERE ch.ts < $t AND ch.ts > $last AND ch.rollback_of IS NULL
RETURN ch.ts AS ts, ch.kind AS kind, ch.summary AS summary,
       (coalesce(ch.rolled_back, false) OR size([(i:Incident)-[:CAUSED_BY]->(ch) | i]) > 0) AS bad
ORDER BY ch.ts LIMIT 5000
```
**History:** past incidents caused by the same kind of change on a candidate, and past non-change causes.
```cypher
MATCH (i:Incident)-[:CAUSED_BY]->(ch:Change)-[:CHANGED]->(c:Component)
WHERE c.id IN $C AND i.started_at < $lo RETURN c.id AS c, ch.kind AS kind, count(i) AS n
```
```cypher
MATCH (i:Incident)-[:CAUSED_BY]->(c:Component) WHERE c.id IN $C AND i.started_at < $lo
RETURN c.id AS c, count(i) AS n
```
**Scoring (Python):**
- `Cov = sqrt(E·Prec)` (Ochiai), where `E` is the share of alerting services that reach the candidate
- `F` = 1 if no downstream candidate explains as much, else 0
- `R` = recency, `exp(−Δt/30 min)`; changes that land after detection are heavily discounted
- `K` = change-kind risk: a blend of the component × kind and change-signature track records, smoothed toward the base rate of that kind (empirical Bayes)
- `H` = history, `min(1, n/2)` for past incidents caused by the same kind of change on the component
- `P = 2/(2+hops)`
- `S = Σ w·term`, using the weights `W` tuned on the train scenarios only. The answer abstains to "not a change" when the best change scores below θ (`ABSTAIN_THETA`).
- Non-change candidates are scored separately from coverage, minimality, whether the component itself alerts, and past non-change causes (`W_NC`).

**Ties:** there is no separate tie-break step. Candidates are sorted by score with Python's stable sort, so equal scores keep the order in which the changes query returned them.

## 3. Similar past incidents (episodic memory)
**Structural candidates:** past incidents that affected the same components.
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
**Semantic neighbours** (only when the incident has an embedding, see §15): HNSW vector search, over-fetched and then filtered by time. `score` is a cosine distance, so similarity is `1 - score`.
```cypher
MATCH (q:Incident {id:$id}) CALL db.idx.vector.queryNodes('Incident', 'embedding', 40, q.embedding)
YIELD node, score WHERE node.id <> $id AND node.started_at < $t0 RETURN node.id AS id, 1 - score AS sim
```
Exact cosine for structural candidates that are outside the HNSW top 40:
```cypher
MATCH (q:Incident {id:$id}), (i:Incident) WHERE i.id IN $ids AND i.embedding IS NOT NULL
RETURN i.id AS id, 1 - vec.cosineDistance(i.embedding, q.embedding) AS sim
```
Up to 15 vector-only matches are then fetched with the same projection as the structural query.

**Similarity (Python):**
- After investigation (root known): `0.35·root + 0.20·change kind + 0.15·J(symptoms) + 0.15·J(blast radius) + 0.15·text`.
- At triage (root unknown): only the symptom, blast-radius and text terms, renormalized.
- `text` is the vector similarity when the incident is embedded, otherwise the token overlap of the titles.
- Final score: `0.9·sim + 0.05·0.99^days + 0.05` if the past incident has a runbook.

## 4. Ownership as of a time, on-call, escalation (bi-temporal)
```cypher
MATCH (t:Team)-[o:OWNS]->(e:Component {id:$id}) WHERE VALID(o)
RETURN t.id AS team_id, t.name AS team, o.valid_from AS since, o.source_id AS source
```
```cypher
MATCH (p:Person)-[:HAS_SHIFT]->(s:OnCallShift)-[:COVERS]->(t:Team {id:$tid})
WHERE s.start <= $at AND s.end > $at
RETURN p.id AS id, p.name AS name, p.handle AS handle, s.level AS level, s.id AS shift,
       (p.left_at IS NULL OR p.left_at > $at) AS active
```
```cypher
MATCH (p:Person {role:'em'})-[m:MEMBER_OF]->(t:Team {id:$tid}) WHERE VALID(m)
RETURN p.id AS id, p.name AS name, p.handle AS handle LIMIT 1
```

## 5. Who actually knows X (expertise from evidence, with time decay)
This is several signal queries (incident responses, authored and reviewed changes, resolved tickets, written docs and runbooks, help-thread answers), combined in Python with weights and a half-life decay (180 days; 365 for design docs). Examples:
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
- it is active and its `last_verified_at` is more than 90 days before the latest change, or
- its author has `left_at` set.

## 7. Decision trace (provenance)
```cypher
MATCH (d:Decision {id:$id})
RETURN d.id AS id, d.key AS key, d.title AS title, d.status AS status, d.decided_at AS decided_at,
       [(d)-[:DOCUMENTED_IN]->(doc:Doc) | {id: doc.id, title: doc.title, snippet: substring(doc.text, 0, 600)}] AS docs,
       [(d)-[r:DISCUSSED_IN]->(t:Thread) | {id: t.id, channel: t.channel, source: r.source_id}] AS threads,
       [(d)-[:DECIDED_BY]->(p:Person) | {id: p.id, name: p.name}] AS decided_by,
       [(d)-[:SUPERSEDES]->(o:Decision) | {id: o.id, key: o.key, title: o.title}] AS supersedes,
       [(n:Decision)-[:SUPERSEDES]->(d) | {id: n.id, key: n.key, title: n.title}] AS superseded_by,
       [(d)-[:AFFECTS]->(c:Component) | {id: c.id, name: c.name}] AS affects
```
A decision key (e.g. `ADR-042`) is matched exactly; free text is matched to a decision through the `Doc` full-text index (§16).

## 8. Graph-based alert correlation (intake)
Alerts correlate when their components are related, through a direct dependency or a 2-hop path whose middle node is **not a hub**, *and* the alerts are within 5 minutes of each other.
```cypher
MATCH p=(a:Component)-[:DEPENDS_ON*1..2]-(b:Component) WHERE a.id IN $E AND b.id IN $E AND a.id < b.id
  AND all(x IN relationships(p) WHERE x.valid_to IS NULL OR x.valid_to > $t)
  AND all(n IN nodes(p) WHERE n = a OR n = b OR size([(n)<-[:DEPENDS_ON]-(u) | u]) < 10)
RETURN DISTINCT a.id AS a, b.id AS b
```

## 9. Coordination: atomic compare-and-set task claim (one writer per graph)
The triage, investigate and respond phases each claim their `Task` before they start. If the claim returns no row, another worker holds the task and the phase stops. The worker id is the agent role plus the crew run (`investigator:3f9a…`), so a second crew on the same incident loses the claim even with the same role; the claim is re-entrant for its current holder while the task is `WORKING`.
```cypher
MATCH (t:Task {id:$id})
WHERE t.status = 'SUBMITTED' OR (t.status = 'WORKING' AND (t.lease_until < $now OR t.claimed_by = $agent))
SET t.status = 'WORKING', t.claimed_by = $agent, t.lease_until = $now + $lease, t.version = t.version + 1
RETURN t.version AS v          // empty => lost the claim
```

## 10. Coordination: handoffs through the graph
The sending agent writes a `Handoff` that carries its evidence (`memory_handoff`):
```cypher
MATCH (i:Incident {id:$inc}) MERGE (fa:Agent {id:$from}) ON CREATE SET fa.name = $fromn
MERGE (ta:Agent {id:$to}) ON CREATE SET ta.name = $ton
WITH i, fa, ta
CREATE (h:Handoff {id:$id, from_agent:$fromn, to_agent:$ton, summary:$s, open_questions:$oq, verification_status:'unverified',
        ts:$ts, recorded_at:timestamp(), root_type:$rt, root_entity_id:$re, root_change_id:$rc, confidence:$cf, mem:'episodic'})
CREATE (h)-[:FOR]->(i), (h)-[:FROM]->(fa), (h)-[:TO]->(ta)
WITH h OPTIONAL MATCH (e:Evidence) WHERE e.id IN $ev
FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | CREATE (h)-[:CARRIES]->(e))
RETURN DISTINCT h.id AS id
```
The Investigator's handoff carries its conclusion as properties (`root_type`, `root_entity_id`, `root_change_id`, `confidence`); the Responder takes the root cause from it. The receiving agent reads its latest handoff from the graph (`memory_inbox`), anchored on the incident, then marks it accepted:
```cypher
MATCH (:Incident {id:$inc})<-[:FOR]-(h:Handoff) WHERE h.to_agent = $role
OPTIONAL MATCH (h)-[:CARRIES]->(e:Evidence)
WITH h, collect(e.id) AS ev ORDER BY coalesce(h.recorded_at, 0) DESC, h.ts DESC LIMIT 1
RETURN h.id AS id, h.from_agent AS from_agent, h.summary AS summary, h.open_questions AS open_questions, ev,
       h.root_type AS root_type, h.root_entity_id AS root_entity_id, h.root_change_id AS root_change_id, h.confidence AS confidence
```
```cypher
MATCH (h:Handoff {id:$id}) SET h.verification_status = 'accepted', h.accepted_by = $agent, h.accepted_at = $now
```

## 11. Idempotent, gated actions (no duplicate pages)
```cypher
MATCH (i:Incident {id:$inc})
MERGE (a:Action {idem_key:$idem})             // idem = incident:kind:target
ON CREATE SET a.id = $id, a.kind = $kind, a.target = $target, a.tier = $tier, a.status = $status, a.payload = $payload,
              a.created_at = $ts, a.agent = $agent, a.mem = 'episodic', a.created = true
ON MATCH SET a.created = false
MERGE (a)-[:FOR]->(i)
WITH a UNWIND (CASE WHEN size($ev) = 0 THEN [null] ELSE $ev END) AS eid
OPTIONAL MATCH (e:Evidence {id:eid}) FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (a)-[:JUSTIFIED_BY]->(e))
RETURN DISTINCT a.id AS id, a.status AS status, a.created AS created
```
The single-statement `MERGE` runs under FalkorDB's one-writer-per-graph rule, so concurrent attempts create one `Action`.

## 12. Functional-relation update from chat (close-and-open in one statement)
```cypher
MATCH (c:Component {id:$c})
OPTIONAL MATCH (:Team)-[o:OWNS]->(c) WHERE o.valid_from <= $vf AND (o.valid_to IS NULL OR o.valid_to > $vf)
SET o.valid_to = $vf, o.expired_at = $now, o.invalidated_by = $src
WITH DISTINCT c MATCH (t:Team {id:$t})
CREATE (t)-[:OWNS {valid_from:$vf, recorded_at:$now, source_id:$src, span:$span, extracted_by:$run, confidence:$conf}]->(c)
```

## 13. Extracted knowledge with provenance (batched, templated)
```cypher
UNWIND $rows AS r MATCH (a:{L1} {id:r.src}), (b:{L2} {id:r.dst})
MERGE (a)-[e:{REL}]->(b)
ON CREATE SET e.source_id = r.source, e.span = r.span, e.confidence = r.conf, e.extracted_by = $run, e.recorded_at = $now
ON MATCH SET e.corroborations = coalesce(e.corroborations, 0) + 1
```
`(L1, REL, L2)` is restricted to a schema allowlist, e.g. `(Incident, CAUSED_BY, Change)` or `(Decision, SUPERSEDES, Decision)`.

## 14. Learning from feedback (Scribe)
On close, the Scribe records the agents' conclusion as a suspicion, never as fact:
```cypher
MATCH (i:Incident {id:$i}), (x:{L} {id:$x}) MERGE (i)-[s:SUSPECTED_CAUSE]->(x)
ON CREATE SET s.by = 'investigator', s.score = $score, s.ts = $now, s.verified = $v
```
`{L}` is `Change` for a change-induced root and `Component` otherwise.
When a human confirms it, it becomes a `CAUSED_BY` edge:
```cypher
MATCH (i:Incident {id:$i})-[s:SUSPECTED_CAUSE]->(x) SET s.confirmed = true
MERGE (i)-[c:CAUSED_BY]->(x) ON CREATE SET c.source_id = $by, c.confidence = 1.0, c.recorded_at = $now
```
When the diagnosis was wrong, the human-supplied cause becomes a `CAUSED_BY` edge plus a `(:Reflection)-[:ABOUT]->(:Component)` node. The next incident's ranking and similarity read the `CAUSED_BY` edge (queries 2 and 3), and `memory_recall` returns the `Reflection`.

## 15. Vector search (Gemini embeddings, HNSW, cosine)
Embeddings are written with `vecf32()` (a plain list is stored but not indexed):
```cypher
UNWIND $rows AS r MATCH (n:{Label} {id:r.id}) SET n.embedding = vecf32(r.v)
```
`knowledge_search` runs full-text search (§16) and, when a Gemini key is set, a vector search per label (`Doc`, `Ticket`, `Runbook`, `Thread`) with the embedded question:
```cypher
CALL db.idx.vector.queryNodes('{Label}', 'embedding', 6, vecf32($v)) YIELD node, score
RETURN node.id AS id, '{Label}' AS kind, coalesce(node.title, node.key, '') AS title,
       substring(coalesce(node.text, ''), 0, 300) AS snippet, 1 - score AS score
```
The full-text and vector rankings are merged in Python with reciprocal rank fusion (k = 60). Incident similarity uses the vector queries in §3.

## 16. Built-in FalkorDB algorithms and indexes used
```cypher
CALL algo.pageRank('Component', 'DEPENDS_ON') YIELD node, score
RETURN node.id AS id, node.name AS name, score ORDER BY score DESC, id                 // importance / blast weight (never cause)
CALL algo.betweenness({nodeLabels:['Component'], relationshipTypes:['DEPENDS_ON'], samplingSize:64, samplingSeed:42})
YIELD node, score RETURN node.id AS id, node.name AS name, score ORDER BY score DESC, id // chokepoints (fixed seed => reproducible)
```
`graph_hubs` returns the top k, plus the rank of specific components when asked. Triage uses it to report where the alerting components rank by PageRank. The `algo.*` procedures see every `DEPENDS_ON` edge, including edges closed with `valid_to`.
```cypher
MATCH (a:{L1} {id:$a}), (b:{L2} {id:$b}) WITH a, b
MATCH p=allShortestPaths((a)-[:DEPENDS_ON|OWNS|CHANGED|…*..{L}]-(b))           // explain a connection
RETURN [n IN nodes(p) | n.id] AS ids, [n IN nodes(p) | coalesce(n.name, n.key, n.title, n.summary, n.id)] AS labels,
       [r IN relationships(p) | [startNode(r).id, type(r), endNode(r).id]] AS rels LIMIT {k}
```
When only `DEPENDS_ON` is requested, the path is directional: `(a)-[:DEPENDS_ON*..{L}]->(b)`, then the reverse direction if that finds nothing.
```cypher
CALL db.idx.fulltext.queryNodes('Component', $q) YIELD node, score   // entity resolution (aliases); also 'Person'
CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score         // knowledge search; also 'Ticket', 'Runbook', 'Message'
```
Entity resolution tries exact ids, keys, names and aliases first, then full-text, then fuzzy full-text (`%word%`, Levenshtein distance 1).

## 17. Company-brain conversation memory
Every Brain answer is stored with its citations:
```cypher
MATCH (r:AgentRun {id:$run}) CREATE (a:Answer {id:$id, question:$q, text:$t, intent:$intent, ts:$now, mem:'episodic'})-[:GENERATED_BY]->(r)
WITH a UNWIND (CASE WHEN size($c) = 0 THEN [null] ELSE $c END) AS eid
OPTIONAL MATCH (e:Evidence {id:eid}) FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | CREATE (a)-[:CITES {verdict:'attributable'}]->(e))
```
When the request has a `session_id`, the question becomes the next `Turn` of a `Session`:
```cypher
MERGE (s:Session {id:$sid}) ON CREATE SET s.created_at = $now, s.mem = 'episodic'
WITH s OPTIONAL MATCH (s)-[:HAS_TURN]->(p:Turn) WITH s, count(p) AS n
MATCH (a:Answer {id:$aid})
CREATE (s)-[:HAS_TURN]->(t:Turn {id:$tid, n:n + 1, question:$q, intent:$intent, entity_id:$eid, entity_name:$ename, ts:$now,
                                 mem:'episodic'})-[:ANSWERED_BY]->(a)
RETURN t.n AS n
```
A follow-up question that names no entity uses the entity of the latest turn:
```cypher
MATCH (:Session {id:$sid})-[:HAS_TURN]->(t:Turn) WHERE t.entity_id IS NOT NULL
RETURN t.entity_id AS id, t.entity_name AS name ORDER BY t.n DESC LIMIT 1
```

## 18. Schema (migration, `graphable/core/schema.py`)
- **Range indexes:**
  - `id` on `Component`, `Service`, `Datastore`, `Region`, `Endpoint`, `FeatureFlag`, `Team`, `Person`, `OnCallShift`, `Change`, `Alert`, `Incident`, `Thread`, `Message`, `Ticket`, `Doc`, `Runbook`, `Step`, `Decision`, `Agent`, `AgentRun`, `AgentStep`, `Task`, `Handoff`, `IncidentBrief`, `Evidence`, `Hypothesis`, `Action`, `Lesson`, `Reflection`, `Claim`, `ExtractionRun`, `Unresolved`, `Session`, `Turn`, `Answer`;
  - also `Component.name`, `Service.name`, `Datastore.name`, `Change.ts`, `Alert.ts`, `Incident.started_at`, `Incident.key`, `Message.ts`, `Person.handle`, `Task.status`, `Runbook.key`, `Decision.key`, `Ticket.key`, `Action.idem_key`;
  - on relationships: `DEPENDS_ON.valid_to`, `OWNS.valid_to`.
- **Full-text indexes** (DDL form): `Runbook(title,text)`, `Doc(title,text)`, `Ticket(title,text)`, `Message(text)`, `Component(name,aliases_text)`, `Person(name,handle)`, `Incident(title)`.
- **Unique constraints:** `GRAPH.CONSTRAINT CREATE … UNIQUE NODE <Label> PROPERTIES 1 id`, on Component, Person, Team, Change, Incident, Message, Thread, Ticket, Doc, Runbook, Decision, Evidence and Action.
- **Vector indexes** (768-d cosine) on `Incident`, `Thread`, `Doc`, `Ticket` and `Runbook` `.embedding` are created when Gemini embeddings are enabled. Vectors are always written with `vecf32()`.
- `Service` and `Datastore` nodes also carry the shared label `Component`, so traversals use one label (FalkorDB has no label alternation in patterns).
