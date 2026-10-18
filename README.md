# Graphable: an AI on-call team with a graph brain (FalkorDB)

> When production breaks, teams lose time relearning what they already knew. Graphable is a crew of AI agents (Triage, Investigator, Responder) that respond to incidents using a **FalkorDB graph as their shared brain**. The graph holds the company's services, dependencies, changes, people, teams, decisions, runbooks and every past incident. The agents trace root causes through it, **remember** what happened before, hand work to each other through it, act behind a human approval gate, and **prove every claim with a graph path** that code re-verifies.

**Tracks entered:** Agents That Act on Connected Data · Agent Memory and Coordination · Company Brain.

| | |
|---|---|
| **Root cause, top-1** | **75%** on 40 held-out scenarios with planted traps. Baselines: random change in the window 18%, toy "hops then recency" 16%, most-recent change **0%** |
| **Claims backed by a real graph path** | **100%**. Code re-checks every cited edge |
| **Concurrency** | 20 agents racing for one task: **1 wins**. 20 simultaneous pages: **1 action created** |
| **Company brain Q&A** | 28/30 correct, **100% cited**, **0 stale answers** after org changes |
| **Learning** | A never-seen failure pattern: wrong 3 times, then **right 5 times out of 5** after on-call corrections are written to the graph |
| **Knowledge extraction (docs, tickets, chats)** | Gemini extractor: micro **P 0.86 / R 0.90** against ground truth. Rules baseline: 0.86 / 0.86 |
| **Speed** | Deterministic crew: ~18 MCP tool calls in **~1.5 s**. With Gemini review: ~23 calls, ~37 s |

The data is synthetic, so treat these as a demonstration, not a benchmark claim. Method and caveats are in [Evaluation](#evaluation).

---

## Why it needs a graph (the "remove FalkorDB" test)
Every core function is a graph traversal. Without FalkorDB there is:
- no topology;
- no blast radius;
- no root-cause candidates;
- no "who owned it then";
- no memory;
- no shared blackboard for the agents;
- no evidence to cite.

| Question | How the graph answers it |
|---|---|
| What breaks if `redis-cache` fails? | Walk *against* `DEPENDS_ON` (bounded, bi-temporal) |
| Which change caused this? | Walk *along* `DEPENDS_ON` from the alerting set; score each change by how many symptoms it explains (Ochiai coverage), minimality, change-type risk learned from history, and recency |
| Has this happened before, and who fixed it? | `(Incident)-[:AFFECTED]->(svc)<-[:AFFECTED]-(past)-[:RESOLVED_BY]->(person)`, `-[:MITIGATED_WITH]->(runbook)` |
| Who owned checkout *during* INC-0287? | Bi-temporal `OWNS {valid_from, valid_to, source_id}`. Nothing is ever deleted |
| Who actually knows vault? | Expertise computed from fixes, postmortems, runbooks, tickets, changes and help answers, with time decay. Includes bus factor and departed experts |
| Why did we decide ADR-042? | `(Decision)-[:DISCUSSED_IN]->(Thread)`, `-[:DECIDED_BY]->`, `-[:SUPERSEDES]->`, with provenance |

## Architecture
```
 Web app (Next.js + @falkordb/canvas)  ──REST/SSE──  FastAPI
                                                       │
                     LangGraph crew: triage → investigate → verify(code) → respond → gate(human) → close → Scribe
                                                       │  MCP client (streamable HTTP, bearer token = tenant)
                              Graphable MCP server: 20 domain tools (role-filtered ≤9 per agent)
                                                       │  templated, bounded, read-only Cypher + evidence registry
                              FalkorDB: one graph per tenant (semantic · episodic · procedural · coordination · provenance)
```

**Principles**
- **Deterministic core, probabilistic edge.** Code does traversal, scoring, verification and gating; the LLM selects, explains and drafts.
- **Faithful by construction.** Agents may only cite `evidence_id`s that tools returned, and a code verifier re-checks every edge before anything is shown or acted on.
- **Never delete; invalidate.** Mutable facts are bi-temporal, so history stays queryable.
- **Tenant comes from auth, never from the LLM.**
- **Safety gate.** Notes are automatic. A ticket is auto-filed only with a verified path and a high score. A page needs human approval unless SEV1 with a very high score. Rollback is only ever proposed.

**Agents**
- **Triage:** blast radius, severity, similar incidents → handoff.
- **Investigator:** ranks root causes, recalls memory, picks a runbook → hypothesis and handoff.
- **Responder:** owner, on-call, real experts, gated actions, status update.
- **Brain:** company Q&A with citations.
- **Scribe:** consolidation, lessons, learning from feedback.
- **Ingestor:** docs, tickets and chats → facts with provenance.

All of their memory and coordination lives in the graph: `AgentRun`, `AgentStep`, `Evidence`, `Hypothesis`, `Handoff`, `Task`, `Action`, `Lesson`, `Reflection`.

Full design and research grounding: [`ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`RESEARCH.md`](docs/RESEARCH.md), [`DATA_SPEC.md`](docs/DATA_SPEC.md).

## Graph data model
```
(Team)-[:OWNS {valid_from, valid_to, source_id}]->(Service|Datastore :Component)
(Person)-[:MEMBER_OF {valid_from, valid_to}]->(Team)          (Person)-[:HAS_SHIFT]->(OnCallShift)-[:COVERS]->(Team)
(Component)-[:DEPENDS_ON {kind, critical, valid_from, valid_to}]->(Component)
(Change {kind, ts, summary})-[:CHANGED]->(Component)           (Person)-[:AUTHORED|REVIEWED]->(Change)
(Alert)-[:FIRED_ON]->(Component)   (Alert)-[:PART_OF]->(Incident)-[:AFFECTED]->(Component)
(Incident)-[:CAUSED_BY {source_id, span}]->(Change|Component)  (Incident)-[:RESOLVED_BY]->(Person)  (Incident)-[:MITIGATED_WITH]->(Runbook)
(Runbook {status, last_verified_at})-[:HAS_STEP]->(Step)-[:NEXT]->(Step)   (Runbook)-[:MITIGATES]->(Component)
(Thread)<-[:IN]-(Message)<-[:SENT]-(Person)   (Thread|Doc|Ticket)-[:MENTIONS]->(Component)
(Decision)-[:DOCUMENTED_IN]->(Doc)   (Decision)-[:DISCUSSED_IN|DECIDED_BY|AFFECTS|SUPERSEDES]->(…)
Agent memory: (Evidence)-[:FOR]->(Incident)  (Incident)-[:HAS_HYPOTHESIS]->(Hypothesis)-[:SUPPORTED_BY]->(Evidence)
              (Handoff)-[:FROM|TO]->(Agent)  (AgentRun)-[:INFORMED_BY]->(AgentRun)  (AgentStep)-[:PRODUCED]->(Evidence)
              (Incident)-[:SUSPECTED_CAUSE]->(…)  (Lesson {votes})-[:ABOUT]->(Component)  (Reflection)-[:ABOUT]->(Component)
              (Action {idem_key, tier, status})-[:FOR]->(Incident)   (Answer)-[:CITES]->(Evidence)
```
Every Cypher query and graph algorithm the product depends on is in **[`docs/QUERIES.md`](docs/QUERIES.md)**.

## Data (100% synthetic, rule 6)
"Acme Commerce" is a fictional marketplace with Faker names on a fictional domain. It has:
- 40 services, 15 datastores, 131 people and 11 teams;
- **12 months of history:** 9.7k changes, 397 incidents, 7.1k alerts, 15k chat messages, 2.9k tickets, 440 docs, 170 runbooks, 120 decisions;
- **~40.7k nodes and ~107k edges after extraction** (27 MB in FalkorDB).

The history includes a reorg, a new team, 8 departures, a sale-week capacity spike and a year-end freeze. There are deliberate traps:
- decoy deploys and silent root causes;
- look-alike incidents;
- service aliases and typos;
- two people named Sam;
- stale runbooks and superseded decisions;
- prompt-injection strings in tickets;
- a second tenant (`globex`) with identical service names.

The generator is deterministic (`seed=20261015`). It writes ground truth for scoring to `truth/`, which is never loaded into the graph.

## Run it

**One command (Docker):**
```bash
cd graphable && docker compose up --build
# web http://localhost:3001 · API docs http://localhost:8000/docs · MCP http://localhost:8765/mcp · FalkorDB Browser http://localhost:3000
```
**Local development:**
```bash
docker run -d -p 6379:6379 -p 3000:3000 falkordb/falkordb:6.0.2
cd graphable && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r pyproject.toml
.venv/bin/python -m graphable.bootstrap                      # generate → load → extract → golden snapshots (~3 s)
.venv/bin/python -m graphable.mcpserver.server --port 8765 & # MCP server
.venv/bin/uvicorn graphable.api.app:app --port 8000 &        # API
cd apps/web && npm install && npm run build && npm start     # http://localhost:3001
```
**Gemini (optional):** put `GEMINI_API_KEY=…` in `graphable/.env` (git-ignored). This enables:
- **Gemini prose** for the dataset (`python -m datagen --render gemini`, Flash-Lite, with a fact-coverage gate that keeps every id, number and @handle);
- **Gemini extraction** (Flash, rules as a floor);
- **768-d embeddings** (`gemini-embedding-2`) in FalkorDB vector indexes, used for semantic similar-incident recall and hybrid knowledge search;
- **Gemini review** of the Investigator's ranking (Pro);
- **grounded Brain answers**, where every sentence must cite a retrieved source or it is dropped.

Without a key, deterministic offline policies run, and they are also the evaluation baseline.

**Use the MCP server from your own client**, e.g. Claude Desktop or Cursor: streamable HTTP at `http://localhost:8765/mcp` with header `Authorization: Bearer dev-acme`.

## Evaluation
```bash
.venv/bin/python -m graphable.evals.rca --set train            # features + baselines + weight/threshold tuning (train only)
.venv/bin/python -m graphable.evals.rca --set test --tuned     # held-out numbers
.venv/bin/python -m graphable.evals.system --bus mcp           # crew over MCP, concurrency, isolation, probes, Brain Q&A, learning curve
.venv/bin/python -m pytest -q tests                            # 9 integration tests
```

**Method**
- **Scenarios:** 40 *test* + 40 *train* + 8 *learning* + 4 *hero*.
- **Isolation:** each runs on a fresh `GRAPH.COPY` of the golden graph.
- **Intake:** the incident is created by graph-based alert correlation.
- **Tuning:** weights and the abstain threshold were chosen by random search on **train only** and then frozen.

**Results on test**

| Method | AC@1 | AC@3 | MRR |
|---|---|---|---|
| **Ours, tuned** | **0.75** | 0.88 | 0.88 |
| Ours, research-prior weights | 0.47 | | |
| Random change in window | 0.18 | | |
| Toy (hops → recency) | 0.16 | | |
| Most recent change | 0.00 | | |

- Look-alike traps: 100% resolved.
- Non-change incidents: correctly abstained 75% of the time, and named the right origin 100% of the time.

**What the LLM adds, measured** (40 test incidents over MCP, Gemini Pro reviewing the graph ranker)
- **Letting the LLM override the ranker hurt.** It fixed 1 incident, broke 3 (two were look-alike traps, where it was swayed by a similar past incident), and dropped accuracy from 0.75 to 0.70.
- **So dissent is treated as uncertainty, not authority.** The graph answer stands, the incident is marked *contested* with halved confidence, and a human sees both views. Result: **0.75 accuracy kept; 6 incidents contested, 2 of them genuinely wrong.**
- **With review on, 93% of all claims verify against the graph.** The rest are LLM statements naming entities its cited evidence does not contain, and they are shown as rejected, not as fact.
- **The LLM's tool budget is enforced in code, not in the prompt.** A prompt-only limit let 5 of 40 runs loop.
- **Conclusion:** the graph and the deterministic scoring carry the accuracy. The LLM adds explanation, extra checks and a second opinion, and the verifier keeps it honest.

**Honest limitations**
- **Synthetic data and small n**, so these are demonstrations.
- **The tuned weights lean on coverage and change-type risk.** Recency got little weight because the decoys are deliberately recent (a generator artifact we report rather than hide).
- **About 17% of auto-filed (T1) tickets pointed at a wrong root on test.** Pages and rollbacks stay human-gated for exactly this reason.
- **Real-world RCA is much harder**: OpenRCA and ITBench report ~11% for LLM agents.

## Repository layout
```
datagen/            synthetic world generator (catalog, world, timeline, render, scenarios, validate, export)
graphable/core      tenancy (db), schema, domain tools, evidence registry, verifier
graphable/ingest    loader (systems of record), extractor + pipeline (prose → facts with provenance)
graphable/mcpserver FastMCP server (20 tools, annotations, bearer-token tenancy)
graphable/agents    LangGraph crew, policies (offline + Gemini), Brain, Scribe, tool buses (MCP/direct)
graphable/api       FastAPI + SSE        graphable/sim  scenario player + graph-based intake
graphable/evals     RCA + system evaluation          apps/web  Next.js UI with FalkorDB canvas
docs/QUERIES.md     every Cypher query/algorithm      tests/  integration tests
```

## Use of AI coding assistants (rule 11)
This project was built with an AI coding assistant (Claude, by Anthropic), used for literature research synthesis, code generation, test generation and documentation. The problem choice, scope, technology choices (FalkorDB, Gemini, LangGraph, real MCP, custom UI, deployment) and architecture decisions were made by the team. Every component was run, tested and reviewed. The evaluation numbers above come from the included scripts.
