# Research Study: Graph-Native Multi-Agent Incident Response on FalkorDB

> Design document. Where it differs from the code, README.md and the code are authoritative.

**Project:** an AI on-call team that learns from every incident (Graph Hacks, all 3 tracks)
**Method:** 5 parallel research streams: Track 1 (graph-reasoning agents), Track 2 (memory and coordination), Track 3 (company brain), the domain (AIOps / root-cause analysis), and FalkorDB capabilities. FalkorDB claims were tested against a live local instance. Each paper was checked against its arXiv/venue page; anything not fully confirmed is flagged in §13.

---

## 0. Executive summary: the 12 decisions the evidence supports

| # | Decision | Main evidence |
|---|---|---|
| 1 | **Agents call fixed, curated graph tools rather than writing free-form Cypher.** Free-form Cypher is only a guarded read-only fallback. | CypherBench (ACL 2025): the best LLMs get only ~60% execution accuracy; the most common error is reversed edge direction. Pangu (ACL 2023): "don't generate, discriminate". |
| 2 | **Answers are faithful by construction.** The LLM may cite only evidence IDs that tools actually returned, and code verifies every citation. | GCR (ICML 2025): only 48.1% of paths an unconstrained LLM produced existed in the graph, vs 100% when constrained. ALCE (EMNLP 2023): ~50% of answers lack full citation support. |
| 3 | **The algorithm proposes and the LLM refines.** Deterministic Cypher/Python scoring ranks root-cause candidates; the LLM picks, explains and acts. | OpenRCA (ICLR 2025): best agent 11.3%. AIOpsLab: 36–45%. Flow-of-Action (WWW'25): 64% vs 35.5% for ReAct. GALA, PRAXIS: graph-bounded traversal beats free ReAct. |
| 4 | **Root-cause scoring combines proximity, recency, coverage, frontier, change-kind prior and history.** | ChangeRCA (FSE 2024; formula read from its code), Groot (ASE 2021), TraceRCA, MicroHECL, Gandalf (NSDI 2020). Google SRE: ~70% of outages come from changes. |
| 5 | **Memory follows the CoALA taxonomy: semantic, episodic and procedural, stored explicitly in the graph.** | CoALA (TMLR); Generative Agents (UIST'23); Zep (2025); ExpeL (AAAI'24); Voyager (TMLR'24). |
| 6 | **Never delete; invalidate.** Mutable facts carry bi-temporal properties, so "who owned it then?" stays answerable. | Zep/Graphiti bi-temporal edges; temporal database glossary (Jensen et al. 1998); Mem0g invalidates rather than deletes. |
| 7 | **Procedural memory (runbooks, lessons) is promoted only after verification or human approval.** | Voyager ablation: removing self-verification cut results by 73%. CoALA: procedural writes are "significantly riskier". AgentPoison and MINJA show memory poisoning. |
| 8 | **Agents coordinate on a graph "blackboard" (Task and Handoff nodes) under a deterministic state machine. They do not chat freely.** | STRATUS (NeurIPS 2025), the closest prior art: detection, diagnosis and mitigation agents on a state machine. MAST (NeurIPS 2025): most multi-agent failures are design or misalignment failures. MetaGPT. |
| 9 | **Owner ≠ expert.** `OWNS` is declared; `KNOWS` is computed from evidence (deploys, fixes, postmortems) with time decay. | Balog et al. (SIGIR 2006); Mockus & Herbsleb (ICSE 2002); Bird et al. (FSE 2011); transactive memory (Wegner). |
| 10 | **Load structured data deterministically; use an LLM only to extract from prose** (postmortems, chats, decision records), with provenance on every fact. | Zhu et al. 2023: GPT-4 is better at reasoning than precise extraction. W3C PROV-O; KGGen (NeurIPS 2025); EDC (EMNLP 2024). |
| 11 | **Actions are gated by a deterministic evidence score, never by the LLM's stated confidence.** Tiered: notes are automatic, tickets need a score threshold, paging needs approval, rollback is propose-only. | Xiong et al. (ICLR 2024): verbalized confidence is overconfident. ToolEmu (ICLR 2024). The MCP spec asks for a human in the loop. |
| 12 | **We show our own scoreboard on synthetic scenarios with deliberate traps, not public memory benchmarks.** | Published LoCoMo scores for the same system vary by 20+ points depending on who ran them. "When to use Graphs in RAG" (ICLR 2026): the demo needs truly multi-hop questions. |

**Novelty:** none of the 5 streams found a published system that combines a shared temporal knowledge graph, three-tier agent memory, multi-agent handoffs and verified path-grounded explanations for incident response. The closest are STRATUS (multi-agent, state machine, no graph memory), Groot (event graph + PageRank, no agents or memory), and RCACopilot / Zhang et al. (historical-incident retrieval, no graph).

---

## 1. FalkorDB: what's actually true (tested live)

The installed version is **FalkorDB v6.0.2**, the new Rust engine released Sep 29, 2026. The C engine is still maintained as **v4.22.0**.

### 1.1 Critical risk
- **Open bug #3150:** an aggregation with a *null grouping key* crashes the whole server. A research probe triggered it by accident; the container was restarted and the data was intact.
  - **Rule if we stay on 6.x:** wrap every grouping key in `coalesce()`.
  - #3226 (an index scan with LIMIT followed by SET or `CALL {}` can abort the server) is reported on main and untested.
- **Decision (§12):** stay on 6.0.2 (pinned) with strict query hygiene. The alternative was `falkordb/falkordb:v4.22.0` (more mature, and it also has A*, Leiden/Louvain and CCH).

### 1.2 Can / can't / workaround

| Need | Status | Notes / workaround |
|---|---|---|
| Variable-length traversal with filters | ✅ live | `-[:DEPENDS_ON*1..4]->`, `all(r IN relationships(p) WHERE …)`. **Hop bounds can't be parameters**: validate them, then inline them. |
| Shortest path | ✅ live | `shortestPath()` works **only in WITH/RETURN**, not in MATCH. `allShortestPaths` works in MATCH. `algo.SPpaths` for weighted paths. |
| PageRank | ✅ live | `algo.pageRank(label, relType)`. **No personalization, weights or damping.** Do personalized PageRank in networkx on a subgraph of ~50 nodes, or use an incident-scoped label trick. |
| Betweenness, WCC, label propagation, harmonic centrality, BFS, MaxFlow, MSF | ✅ live | Betweenness is sampled; **fix `samplingSeed` for the demo**. `algo.BFS(node, depth, relType)`. |
| A*, Leiden/Louvain | ❌ on 6.x | Only in v4.22.0. |
| Vector index (similar incidents) | ✅ live | `CREATE VECTOR INDEX … OPTIONS {dimension, similarityFunction:'cosine'}`. **Always wrap with `vecf32()`**: a plain list is stored but *silently not indexed*. `score` is a distance (lower is closer). No pre-filtering, so over-fetch k. |
| Full-text index | ✅ live | Use the DDL `CREATE FULLTEXT INDEX FOR (r:Runbook) ON (r.title, r.body)`. **The procedure form is broken on 6.x.** A hyphen is the NOT operator, so normalize names. |
| Uniqueness constraints | ✅ live | `GRAPH.CONSTRAINT CREATE …`, which requires an existing range index. Not available in Cypher. |
| `EXISTS {}` / `COUNT {}` subqueries, `(n:A\|B)` labels, APOC, `LOAD CSV` (in docker) | ❌ | Use pattern predicates, `size([...])`, `WHERE n:A OR n:B`, and `UNWIND $rows`. |
| Timezone datetimes | ❌ | **Store epoch-ms integers** (`valid_from`, `valid_to`, where null means current). Fractional seconds are dropped; Python datetime parameters fail. |
| Multi-statement transactions | ❌ | Every logical write must be **one atomic Cypher statement**. MULTI/EXEC blocks the whole server, so avoid it. |
| Concurrency | ✅ live | **One writer per graph** (FIFO); reads run in parallel on a snapshot. A single-query conditional `SET` therefore works as compare-and-set (to be proven by a test). |
| Multi-tenancy | ✅ live | One graph per tenant (one Redis key each). `GRAPH.COPY` to reset a demo tenant. `EXPIRE` works on graphs. No cross-graph queries. |
| Defaults that will bite | ⚠️ live | **`TIMEOUT 1000` ms** on reads; `RESULTSET_SIZE 10000`. Set `FALKORDB_ARGS="TIMEOUT_DEFAULT 10000 TIMEOUT_MAX 15000 RESULTSET_SIZE 100000"` and enable AOF. |
| MERGE semantics | ⚠️ live | A full-pattern MERGE can duplicate an endpoint. MATCH or MERGE the nodes first, then MERGE the edge. `LIMIT` doesn't limit eager writes. |
| Parameters in `LIMIT $k` | ✅ live | Works on 6.0.2, contrary to the MCP server's tool description. |

### 1.3 Ecosystem verdicts

| Piece | Verdict | Why |
|---|---|---|
| `falkordb` Python client 1.7.1 | **Use** | Needs **Python ≥ 3.10**; the Mac has 3.9.6, so install 3.11+. Has an async client. |
| Official FalkorDB MCP server | **Dev aid only** | Generic raw-Cypher tools. Track 1 asks for *domain* tools selected through MCP, so we build our own Python MCP server (FastMCP). |
| Graphiti | **Skip as a dependency; copy its ideas** | The released 0.30.2 breaks on FalkorDB 6.x (issue #1947, fix not released) and has open group_id bugs. (The Track 3 stream rated it easy; the FalkorDB stream's live/issue check overrides that.) |
| GraphRAG-SDK | **Optional** | Its full-text index uses the broken procedure form, so it fails silently on 6.x. Use only if bulk prose ingestion becomes core. |
| QueryWeaver | Not relevant | It's Text2**SQL**. |
| LangChain / LlamaIndex / Cognee | Skip | Not needed for our design. |
| FalkorDB Browser / `@falkordb/canvas` | **Browser: use for exploring the graph** | Graph canvas, query history. The Browser runs in the Docker setup (port 3000). The web app draws its own SVG graph (`apps/web/components/NetGraph.tsx`) rather than embedding the canvas. |
| FalkorDB Cloud free tier | **Risky for "live deployment"** | 100 MB, no TLS or backups, **stops after 1 idle day, deleted after 7 days**. Reseed before use, or run Docker on a small VM. |

---

## 2. Track 1: agents that act on connected data

### 2.1 Literature map

| Paper | Venue | Technique | What we adopt |
|---|---|---|---|
| Think-on-Graph (Sun et al.) [2307.07697](https://arxiv.org/abs/2307.07697) | ICLR 2024 | LLM beam search over the KG (N=3, depth 3) with a "sufficient?" check | Beam-limited exploration; paths as traceable evidence. Costs ~11.6 LLM calls per question, so use sparingly. |
| ToG-2 [2407.10805](https://arxiv.org/abs/2407.10805) | (ICLR 2025, unverified) | Alternates KG and document retrieval | "Graph to find, text to explain": once a path reaches a service, pull its runbooks, decision records and postmortems. |
| Reasoning on Graphs (Luo et al.) [2310.01061](https://arxiv.org/abs/2310.01061) | ICLR 2024 | Plan relation paths → constrained BFS → reason | The LLM selects a relation-path *template* from a small catalog; the server executes it. |
| StructGPT [2305.09645](https://arxiv.org/abs/2305.09645) | EMNLP 2023 | invoke → linearize → generate; two-stage neighbor tool | `graph_neighbors` returns relation types first, then triples. |
| KG-Agent [2402.11163](https://arxiv.org/abs/2402.11163) | ACL 2025 | Toolbox of extraction, logic and semantic tools, plus memory | Tool taxonomy; our memory lives in the graph. |
| Graph-CoT [2404.07103](https://arxiv.org/abs/2404.07103) | Findings ACL 2024 | Reason–interact–execute; `NodeDegree` check | Check degree before expanding; include 2–3 worked trajectories in the prompt. A 2-hop subgraph dump does **worse** than 1-hop. |
| GCR (Luo et al.) [2410.13080](https://arxiv.org/abs/2410.13080) | ICML 2025 | KG-Trie constrained decoding → 100% faithful paths | **The LLM may only select from tool-returned paths** (no training needed). |
| Paths-over-Graph [2410.14211](https://arxiv.org/abs/2410.14211) | WWW 2025 | 3-step pruning: structure → LLM → SBERT | Cheap structural pruning first, the LLM last. |
| Plan-on-Graph [2410.23875](https://arxiv.org/abs/2410.23875) | NeurIPS 2024 | Guidance, memory, reflection, backtracking | A "ruled_out → next hypothesis" reflection step. |
| GNN-RAG [2405.20139](https://arxiv.org/abs/2405.20139) | Findings ACL 2025 | Verbalized shortest paths as context | Shortest paths as the explanation format (skip the GNN). |
| Search-on-Graph [2510.08825](https://arxiv.org/abs/2510.08825) | KDD 2026 | A single `search` tool with a hub rule | **Hub rule:** degree > ~40–50 with no filter → return relation-type counts only. |
| GraphRAG (Edge et al.) [2404.16130](https://arxiv.org/abs/2404.16130) | arXiv 2024 | Communities + map-reduce summaries | **Not for us.** Our graph is already structured and our questions are local. |
| When to use Graphs in RAG [2506.05690](https://arxiv.org/abs/2506.05690) | ICLR 2026 | GraphRAG often loses to vanilla RAG except on complex reasoning | Make the demo questions *truly* multi-hop. |

### 2.2 Text-to-Cypher vs. curated tools
- **CypherBench** (ACL 2025) [2412.18702](https://arxiv.org/abs/2412.18702): Claude 3.5 Sonnet scores 61.6% and GPT-4o 60.2%. Errors: reversed direction, entity linking, pattern mismatch.
- **Text2Cypher** (COLING-W 2025) [2412.10064](https://arxiv.org/abs/2412.10064): baseline models struggle on multi-hop queries.
- **Spider 2.0** (ICLR 2025): realistic enterprise text-to-SQL is 21% vs 91% on Spider 1.0.
- **Pangu** (ACL 2023) [2212.09736](https://arxiv.org/abs/2212.09736): enumerate valid plans and let the LM score them. This is the principled case for "LLM chooses, executor derives".
- **Interactive-KBQA** (ACL 2024): curated tools plus an escape hatch.
- **Safety:** Prompt-to-SQL injection (ICSE 2025). Labels, relationship types and hop bounds can't be parameterized, so **allowlist them in code**. Use `ro_query` and set `TIMEOUT_MAX`.

### 2.3 Tool selection and MCP
- Accuracy falls as tool count grows: LongFuncEval (7–85% drop); RAG-MCP (13.6% → 43.1% selection accuracy with tool retrieval).
- **Give each agent 6–9 tools.**
- Anthropic's tool-design guidance: workflow-shaped tools, namespacing, human-readable outputs, actionable errors, a concise/detailed `response_format`.
- **MCP spec (2026-07-28):**
  - `inputSchema`/`outputSchema`/`structuredContent`
  - errors returned as `isError:true` so the model can self-correct
  - annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) are hints, not enforcement
  - a human SHOULD be able to deny tool calls
- **Graph algorithms must be tools.** NLGraph (NeurIPS 2023) and GraphArena (ICLR 2025) show LLMs hallucinate graph computation.

### 2.4 Faithful explanations
- Post-hoc chain-of-thought rationalizes: Turpin et al. (NeurIPS 2023), and Lanham et al. 2023 (larger models are *less* faithful).
- **Faithful CoT** (Lyu et al. 2023): the LLM writes a symbolic chain and a deterministic solver computes the answer.
- **Our principle:** *the explanation is the executed tool calls and the returned paths.* The narrative may only restate claims that cite verified evidence IDs.

### 2.5 Multi-hop failure modes → mitigations

| Failure | Mitigation |
|---|---|
| Hallucinated edges or paths | Evidence registry + code verifier re-matching edges by ID |
| Edge-direction confusion | Name tools by meaning (`upstream_dependents`), spell out direction in English in `graph_schema` |
| Path explosion | Hop caps ≤ 4, relationship-type allowlists, LIMIT, beam N=3 |
| Hub nodes (auth, DB, queue) | Degree check; hub flag from PageRank; relation-type counts above the threshold |
| Over-pruning | Structural pruning first, LLM last; keep the top 2 alive |
| Long tool outputs ignored ("Lost in the Middle", TACL 2024) | Compact, ranked outputs with the most important item first |

---

## 3. Track 2: agent memory and coordination

### 3.1 Taxonomy (CoALA, Sumers et al., TMLR, [2309.02427](https://arxiv.org/abs/2309.02427))

| Memory type | CoALA definition | Our graph realization |
|---|---|---|
| Working | Information active in the current decision cycle | The **IncidentBrief** node, size-capped, included in every agent prompt (like MemGPT's core block) |
| Episodic | Experience from earlier cycles | `Incident`, `Observation`, `AgentStep` chain, `Hypothesis`, `Session`/`Message`. **Append-only.** |
| Semantic | Knowledge about the world | `Service`, `Team`, `Person`, `DEPENDS_ON`, `OWNS`, `ON_CALL_FOR`, `Decision`, `Doc`, with **bi-temporal edges** |
| Procedural | How to act | Versioned `Runbook` → `Step` DAG, plus `Lesson` nodes with vote counts. **Gated writes.** |

CoALA actions map to tool name prefixes: `retrieve_*`, `learn_*`, `ground_*` (external actions).

### 3.2 Systems we borrow from

| System | Venue | Mechanism | Adopt |
|---|---|---|---|
| Generative Agents [2304.03442](https://arxiv.org/abs/2304.03442) | UIST 2023 | Retrieval = recency + importance + relevance (min-max normalized); reflections cite evidence | Retrieval scoring for similar incidents (importance from severity, no LLM call); Reflection/Lesson nodes with `DERIVED_FROM` |
| MemGPT / Letta [2310.08560](https://arxiv.org/abs/2310.08560) | arXiv 2023 | Tiered memory, shared core blocks, background "sleep-time" agents | IncidentBrief; a background consolidator on incident close |
| **Zep / Graphiti** [2501.13956](https://arxiv.org/abs/2501.13956) | arXiv 2025 | Bi-temporal edges (`t_valid`/`t_invalid` + `created`/`expired`); invalidate on contradiction; **template-only Cypher writes**; hybrid retrieval (cosine + BM25 + BFS) | All of it, implemented ourselves |
| Mem0 / Mem0g [2504.19413](https://arxiv.org/abs/2504.19413) | arXiv 2025 | ADD/UPDATE/DELETE/NOOP via tool call; the graph variant invalidates | The 4-operation JSON contract for semantic writes (with DELETE turned into invalidate) |
| A-MEM [2502.12110](https://arxiv.org/abs/2502.12110) | NeurIPS 2025 | Zettelkasten notes, auto-linking, "evolution" | Auto-link postmortems with `SIMILAR_TO {score, basis}`. **Don't** rewrite old notes. |
| HippoRAG 1/2 [2405.14831](https://arxiv.org/abs/2405.14831), [2502.14802](https://arxiv.org/abs/2502.14802) | NeurIPS 2024 / ICML 2025 | Personalized PageRank from seed nodes | Considered, not used: FalkorDB's `algo.pageRank` has no personalization, and root-cause ranking uses coverage scoring (§7.1) |
| MIRIX [2507.07957](https://arxiv.org/abs/2507.07957) | arXiv 2025 | 6 typed stores, sensitivity flag | Field lists for episodic/procedural nodes; a `sensitivity` flag for pasted secrets |
| AriGraph [2407.04363](https://arxiv.org/abs/2407.04363) | IJCAI 2025 | Episodic vertices linked to the triples they produced | The `(Episode)-[:ASSERTED]->(fact)` provenance pattern |
| Hindsight [2512.12818](https://arxiv.org/abs/2512.12818) | arXiv 2025 | Separates facts, experiences and beliefs | Keep `Evidence` and `Hypothesis` separate (proposed/confirmed/refuted) |
| MemoryBank [2305.10250](https://arxiv.org/abs/2305.10250) | (AAAI 2024, unverified) | Ebbinghaus decay | Decay applied **at ranking time only**; never delete |

### 3.3 Procedural memory: learning from experience

| Paper | Venue | Mechanism | Adopt |
|---|---|---|---|
| Reflexion [2303.11366](https://arxiv.org/abs/2303.11366) | NeurIPS 2023 | Verbal reflections on failure | A `Reflection` node when a mitigation fails |
| **Voyager** [2305.16291](https://arxiv.org/abs/2305.16291) | TMLR 2024 | A skill is added **only after self-verification** (ablation −73%) | A runbook is promoted only on verified mitigation or human approval |
| **ExpeL** [2308.10144](https://arxiv.org/abs/2308.10144) | AAAI 2024 | Insights with ADD/EDIT/UPVOTE/DOWNVOTE; removed at 0 | **`Lesson {text, votes}`**: the simplest credible demo of procedural learning |
| Agent Workflow Memory [2409.07429](https://arxiv.org/abs/2409.07429) | ICML 2025 | Induce reusable workflows from trajectories | Induce a `proposed` runbook from the Responder's action sequence |
| Memp [2508.06433](https://arxiv.org/abs/2508.06433) | (ACL 2026 Findings) | Step and script levels; deprecate instead of delete | Runbook (script) → Step; `SUPERSEDES` versions |
| LEGOMem [2510.04851](https://arxiv.org/abs/2510.04851) | arXiv 2025 | Orchestrator memory vs task-agent memory | Role-filtered retrieval: Triage gets plan-level memories, Responder gets step-level |
| ReasoningBank [2509.25140](https://arxiv.org/abs/2509.25140) | ICLR 2026 | Learn from failures too | Keep anti-pattern lessons |
| G-Memory [2506.07398](https://arxiv.org/abs/2506.07398) | NeurIPS 2025 | Insight / query / interaction graph tiers | Incident → AgentStep chain → Lesson (maps one-to-one) |
| StepFly [2510.10074](https://arxiv.org/abs/2510.10074) | FSE 2026 | Troubleshooting guides become execution DAGs (~94% success) | Runbook steps with `NEXT`/`DEPENDS_ON` |
| OpsHarness [2608.25661](https://arxiv.org/abs/2608.25661) | arXiv 2026 | Contrast success and failure → update proposals behind a 2-stage verification gate | The nearest work to "learning runbooks from outcomes" |

### 3.4 Consistency rules
1. **Single-valued relations** (owning team, current on-call, running version): close the old edge's `valid_to` and open the new one **in one deterministic Cypher statement**. No LLM.
2. **Free-text facts** (from prose): an LLM contradiction check only against edges between the **same entity pair** (Zep), then invalidate.
3. **Hypotheses are not facts.** They move proposed → confirmed or refuted through evidence edges.
4. **Order:** run the contradiction check *before* deduplication. MemClaw found that deduplication swallowed contradictions.
5. **Don't summarize summaries.** It causes semantic drift (SSGM, Lam et al. 2026). Keep raw episodes and link summaries to their sources.
6. Keep temporal logic **in Cypher, not in prompts**. Zep reports weak models struggle with time.

### 3.5 Multi-agent coordination
- **Blackboard architecture** (Nii 1986; Hayes-Roth 1985; Hearsay-II): independent knowledge sources, a shared board and a separate control component. Our board is the Incident subgraph.
- **LLM blackboard papers:** Han & Zhang [2507.01701](https://arxiv.org/abs/2507.01701) (a control unit cut tokens 3×); Salemi et al. [2510.01285](https://arxiv.org/abs/2510.01285) (agents volunteer for posted requests, +13–57%).
- **MetaGPT** (ICLR 2024): structured documents instead of chat; role-based publish/subscribe.
- **STRATUS** (NeurIPS 2025): detection, diagnosis and mitigation agents with a state machine and a "Transactional No-Regression" property; ≥1.5× over prior SRE agents. **Our closest prior art.**
- **MAST** (NeurIPS 2025 D&B) [2503.13657](https://arxiv.org/abs/2503.13657): 14 failure modes, mostly design and misalignment. Handoffs need explicit `open_questions`, `verification_status` and a termination rule.
- **A2A v1.0:** reuse the *state names* (SUBMITTED, WORKING, INPUT_REQUIRED, COMPLETED, FAILED, …) and `contextId` = incident id. Don't implement the HTTP protocol.
- **AutoGen and CAMEL:** the shared state is the chat transcript. Cite them as the contrast to our design.

### 3.6 Concurrency patterns (FalkorDB: one writer per graph, atomic queries)
- **Task claim as single-query compare-and-set:**
  ```cypher
  MATCH (t:Task {id:$id})
  WHERE t.status='SUBMITTED' OR (t.status='WORKING' AND t.lease_until < $now)
  SET t.status='WORKING', t.claimed_by=$agent, t.lease_until=$now+60000, t.version=t.version+1
  RETURN t.version          // empty result = lost the claim
  ```
- **Idempotent side effects:** `MERGE (a:Action {idem_key:$k})` under a unique constraint, so a retry never pages twice.
- Append-only episodic writes, UNWIND batching, `WHERE n.version=$expected` optimistic checks.
- **Tenant id comes from server-side context, never from the LLM.** Enforce scope in *every* template, including get-by-id (the MemClaw sub-tenant bypass).

### 3.7 Benchmarks: why we won't chase them
LoCoMo (ACL 2024), LongMemEval (ICLR 2025), DMR and MemoryAgentBench are chat-assistant benchmarks, and published numbers disagree. Zep on LoCoMo is reported as 66.0, 75.1, 79.1 and 49.1 depending on who ran it. We borrow LongMemEval's **categories** (knowledge update, temporal reasoning, abstention) for our own tests in §9.

---

## 4. Track 3: company brain

### 4.1 Framing
- **Organizational memory** (Walsh & Ungson, AMR 1991): memory is scattered across retention facilities (people, structures, transformations). The brain indexes them all.
- **Answer Garden** (Ackerman, TOIS 1998): when the knowledge base can't answer, route the question to a human expert and capture the reply back into the store. **Our loop:** low confidence → page the top expert → the reply becomes a Source with provenance.
- **Transactive memory** (Wegner 1986/87; Lewis 2003: specialization, credibility, coordination): the "who knows what" directory. This frames the expertise features.
- **Lesson from Microsoft Viva Topics** (retired Feb 2025): topic mining with no workflow attached didn't survive as a product. Tie the brain to a concrete workflow (incident response). ✔

### 4.2 Industry precedents
- **LinkedIn KG:** canonical IDs plus alias lists.
- **LinkedIn RAG+KG for customer service** (SIGIR 2024, [2404.17723](https://arxiv.org/abs/2404.17723)): tickets stored as structured graphs; **MRR +77.6%, median resolution time −28.6% in production**. The closest analog to our similar-incident recall.
- **Salesforce ICA** (ICSE-SEIP 2022, [2204.11598](https://arxiv.org/abs/2204.11598)): a causal KG built from ~2,000 postmortems.
- **Glean:** Content + People + Activity graph; expertise *computed* from activity.
- **Atlassian Teamwork Graph:** cross-tool links (self-reported 44% more accurate answers).
- **Backstage:** exactly one owner (a team) per component.

### 4.3 Expertise ("who knows what")
- **Balog Model 2** (SIGIR 2006): document-centric, `p(q|ca) = Σ_d p(q|d)·p(d|ca)`, and it consistently wins. Implement as a vector search over sources followed by aggregation over linked people.
- **Experience atoms** (Mockus & Herbsleb, ICSE 2002): every deploy, fix, review and postmortem is a timestamped evidence edge.
- **Degree-of-Authorship / truck factor** (Avelino et al., ICPC 2016, [1604.06766](https://arxiv.org/abs/1604.06766), formula confirmed): `DOA = 3.293 + 1.098·FA + 0.164·DL − 0.321·ln(1+AC)`. Gives a **bus factor per service** and a "bus factor = 1" warning (used in the vault scenario).
- **Temporal expertise profiling** (ECIR 2014): use recency decay, plus a "was the expert, moved on" badge.
- **Who should fix this bug** (ICSE 2006) and ticket routing (KDD 2008): record the *resolver* separately from the *assignee*, and page the resolver.
- **Ownership and quality** (Bird et al., FSE 2011): a deploy by a low-expertise contributor to a high-tier service is a risk feature for the Investigator.
- **Knowledge-at-Risk on turnover** (Rigby et al., ICSE 2016): when `left_at` is set, rank successors by overlap and flag orphaned runbooks and docs.

### 4.4 Building the KG from prose
- **Zhu et al.** [2305.13168](https://arxiv.org/abs/2305.13168): GPT-4 is "more suited as an inference assistant than a few-shot information extractor". So **load systems of record directly** and use the LLM only on prose.
- **Microsoft GraphRAG extraction:** small chunks (600 tokens give ~2× the entity references of 2,400), a "glean" pass, claim extraction. Use the ideas, skip the indexer: it took 281 minutes on the podcast dataset.
- **EDC** (EMNLP 2024): extract, then define, then canonicalize to the schema. A `RELATED_TO {label}` escape hatch gets canonicalized later.
- **KGGen** (NeurIPS 2025) [2502.09956](https://arxiv.org/abs/2502.09956): candidate retrieval (BM25 + embeddings) plus LLM-confirmed deduplication of aliases.
- **iText2KG** [2409.03284](https://arxiv.org/abs/2409.03284): a distill step turns a long doc into schema-shaped JSON before extraction.
- **Entity matching with LLMs** (EDBT 2025): confirm only candidate pairs with the LLM.
- **Conversation disentanglement** (ACL 2019): generate chat that is **already threaded** and extract per thread.

### 4.5 Provenance and decisions
- **W3C PROV-O:** Entity (sources, answers), Activity (ExtractionRun, AgentRun), Agent (people, the 3 agents). `wasDerivedFrom`, `wasInformedBy` (a handoff!), `invalidatedAtTime`.
- **Nanopublications:** important claims ("root cause of INC-42 = deploy D-17") become `Claim` nodes with `SUPPORTED_BY` edges.
- **Architecture Decision Records** (Nygard 2011; Tyree & Akerman 2005; Jansen & Bosch 2005): a `Decision {status: proposed|accepted|superseded|deprecated}` with `SUPERSEDES`, `DISCUSSED_IN`, `DECIDED_BY` and `AFFECTS` edges. Never delete a decision.
- **IBIS / QOC** (stretch goal): capture rejected options, to answer "why didn't we just use X?"
- **Citation verification:** ALCE citation recall and precision; AttrScore labels (attributable / extrapolatory / contradictory). A *contradictory* label doubles as a **staleness signal**.

### 4.6 Keeping knowledge current
- **Bi-temporal model:** valid time vs transaction time on every mutable edge.
- **Staleness rules** (from DOCER, EMSE 2024). Flag a runbook when:
  - it mentions a service or endpoint that no longer exists;
  - its `last_verified_at` is older than the latest deploy to its service;
  - its author has left;
  - it cites a superseded decision.
- **Demo:** post "checkout moved to Team Orbit" in chat. Show the `OWNS` edge invalidated, the paging target changing, and "who owned checkout during INC-42?" still answered correctly.

### 4.7 Synthetic company data (rule 6: synthetic only)
- **CRMArena** (NAACL 2025) [2411.02305](https://arxiv.org/abs/2411.02305) recipe:
  - generate objects in schema-dependency order;
  - **latent variables** (true expertise, service fragility, true root cause) drive behavior but aren't loaded;
  - mini-batches with deduplication;
  - a format verifier and a content verifier.
- **Personas** ([2406.20094](https://arxiv.org/abs/2406.20094)): each employee gets a writing style. Names come from Faker under a fictional domain.
- **Don't use** Enron (real people's mail; thousands of SSNs found), TREC W3C/CERC or IRC logs. They break rule 6.
- **HERB** (Salesforce) is synthetic but CC-BY-NC and Salesforce-branded. Use only its structure ideas.

---

## 5. Domain: AIOps and root-cause analysis

### 5.1 Graph-based RCA: what's feasible here

| Method | Venue | Core idea | Feasible? |
|---|---|---|---|
| MonitorRank | SIGMETRICS 2013 | Random walk with restart on the call graph | Medium; use as tie-breaker |
| MicroRCA | NOMS 2020 | Anomalous subgraph + **personalized PageRank** (α=0.85, read from its code) | **High** (networkx) |
| MicroHECL | ICSE-SEIP 2021 | Follow anomalous dependency chains downstream; prune healthy edges; 68% top-3 at Alibaba | **High**: one Cypher path query |
| **Groot** (eBay) | ASE 2021 | Event causality graph (alerts, deploys, logs) + rules + PageRank biased to dangling nodes; **78% top-1 on 952 incidents** | **High**: the closest published design to ours |
| TraceRCA | IWQoS 2021 | More abnormal and fewer normal traces through a service → more suspicious | **High** (coverage term) |
| CIRCA | KDD 2022 | Root cause = intervention: anomalous while its parents are healthy | Concept only ("frontier" rule) |
| CloudRanger, Microscope, MicroCause, RCD, Sage, Eadro, Nezha, BARO | 2018–2024 | Causal discovery, GNNs, VAEs, time series | **Low: skip** |
| "How far are we?" (Pham et al.) | ASE 2024 | 9 causal-discovery + 21 RCA methods; none dominates; synthetic results don't transfer | **Main evidence against causal discovery** |

### 5.2 Change-induced incidents
- **Google SRE book:** "roughly 70% of outages are due to changes in a live system". Playbooks give ~3× better MTTR.
- **Gandalf** (NSDI 2020, Azure): correlates failures with rollouts in time *and* space; ~92–95% precision. Treat partial or canary rollouts as strong evidence.
- **ChangeRCA** (FSE 2024), formula read from `ranker.py`:
  - `score = time_score + difference_score + depth_score`
  - `depth_score = 2/(hops+2)`
  - recency in doubling buckets
  - the change is declared the culprit if canary instances show a >0.7 anomaly fraction
  - **85% Hit@1.** This is almost exactly our toy test, plus the canary term.
- **Kapel et al.** (ICSE-SEIP 2024): linking changes to incidents gives more false positives in practice than on historical data, so **always show alternative hypotheses**.
- **Ghosh et al.** (SoCC 2022, Best Paper): 7 root-cause types in Microsoft Teams incidents. Include non-change causes such as certificate expiry and capacity.

### 5.3 LLM agents for incidents: reported reality

| Work | Venue | Result | Lesson |
|---|---|---|---|
| Ahmed et al. | ICSE 2023 | 40k incidents; fine-tuned GPT-3.x helps | — |
| **Zhang et al.** [2401.13810](https://arxiv.org/abs/2401.13810) | FSE 2024 Ind. | **In-context examples from retrieved historical incidents: +24.8% over fine-tuning, +49.7% over zero-shot** | Recalling similar incidents is *the* strongest lever, and it's what a graph does well |
| RCACopilot [2305.15778](https://arxiv.org/abs/2305.15778) | EuroSys 2024 | Handler per alert type; category F1 up to 0.766 | Handlers become per-alert-kind Cypher standard procedures |
| Roy et al. [2403.04123](https://arxiv.org/abs/2403.04123) | FSE 2024 Ind. | ReAct 35% correct but only ~4% hallucination; failures from insufficient evidence and the iteration cap | Cap tool calls; dedupe retrieval results |
| Flow-of-Action [2502.08224](https://arxiv.org/abs/2502.08224) | WWW 2025 | Standard procedures as code: **64% vs 35.5% for ReAct** | The Investigator follows fixed procedure sequences, not free ReAct |
| OpenRCA | ICLR 2025 | Best agent 11.34% | Its data lacks deploy and config context, **which is exactly what our graph provides** |
| AIOpsLab [2501.06706](https://arxiv.org/abs/2501.06706) | MLSys 2025 | RCA 36–45%; false alarms on no-fault cases | **Always allow a "no fault / not a change" answer** |
| ITBench | ICML 2025 | 11.4% of SRE scenarios resolved | Frame our results as a demo, not state of the art |
| GALA [2508.12472](https://arxiv.org/abs/2508.12472) | arXiv 2025 | Causal ranking first, then LLM iterative re-ranking | "Algorithm proposes, LLM refines" (see §13 on its citation) |
| TopoEvo [2605.15611](https://arxiv.org/abs/2605.15611) | arXiv 2026 | "Symptom-amplification bias": agents blame the visible victims | Topology-constrained hypothesis → evidence → test |
| Kim et al. [2602.09937](https://arxiv.org/abs/2602.09937) | arXiv 2026 | 1,675 OpenRCA runs, 12 failure types; prompting doesn't fix them; better inter-agent protocol −15 pp | Typed handoffs |

### 5.4 Incident similarity
- **DiLink** [2403.18639](https://arxiv.org/abs/2403.18639): text embeddings + service-dependency-graph embeddings → **F1 0.96**. This directly supports combining structural and text similarity.
- LiDAR (FSE 2020), iPACK (ICSE 2023), LinkCM (13.1× faster triage).
- **DeepTriage** (KDD 2020): misrouting increases time-to-mitigate tenfold, so **page the owner of the *suspect* service, not the alerting one.**
- Pure text similarity misses incidents where one root cause shows up in different services. Structural overlap fills the gap.

### 5.5 Blast radius and criticality
- Blast radius = reverse reachability over `DEPENDS_ON`, weighted by customer-facing entry points.
- PageRank, transitive dependents and betweenness measure **importance and severity, not root-cause probability**. Global PageRank always blames hubs.

### 5.6 What agents should output (industry practice)
- **Google SRE postmortem:** Summary, Impact, Root Causes, Trigger, Resolution, Detection, Action Items, Lessons, Timeline.
- **PagerDuty:** SEV-1…5. Step 2 is "check for an obvious cause such as a recent deployment". Experts act only when the Incident Commander directs.
- **DORA:** "failed deployment recovery time" and change failure rate, both computable per service or team from our graph.

---

## 6. Synthesized architecture

### 6.1 Layers
```
 ┌───────────────────────────── UI (demo) ─────────────────────────────┐
 │ alert feed · agent timeline · graph canvas (path highlight) · scoreboard │
 └──────────────────────────────────┬──────────────────────────────────┘
          Orchestrator: deterministic state machine (STRATUS/MetaGPT-style)
   SUBMITTED → TRIAGE → INVESTIGATE → VERIFY(code) → RESPOND → GATE → CLOSE → CONSOLIDATE
          │ Triage agent      │ Investigator agent     │ Responder agent
          └──────── MCP client (tool selection through MCP) ───────┘
                               │
              Custom Python MCP server (FastMCP): domain tools
     read tools (ro_query, allowlists, hop caps) · memory writes · gated actions
     evidence registry · scoring (Python) · tenant scope from context
                               │
                 FalkorDB: one graph per tenant (e.g. t_acme)
   semantic (bi-temporal) · episodic (append-only) · procedural (gated)
   coordination blackboard · provenance (PROV) · vector + full-text indexes
```
**The "remove FalkorDB" test:** without the graph there is no topology, no blast radius, no root-cause candidates, no memory, no handoff state and no evidence paths. The product cannot run. ✔

### 6.2 Schema (draft)

**Time format:** all timestamps are epoch-ms integers. Mutable edges carry `valid_from`, `valid_to` (null means current), `recorded_at`, `expired_at`, `source_id`.

| Layer | Nodes | Edges |
|---|---|---|
| Org (semantic) | `Person{id,name,handle,active,joined_at,left_at}`, `Team{id,name}` | `MEMBER_OF`†, `OWNS`† (exactly 1 active per service), `ON_CALL_FOR`† |
| Systems (semantic) | `Service{id,name,aliases[],tier,entry_point}`, `Change{id,kind:code\|config\|flag,ts,canary,summary}` | `DEPENDS_ON{kind:sync\|async}`, `CHANGED` (Change→Service), `AUTHORED`, `REVIEWED` |
| Knowledge (semantic) | `Doc{kind:postmortem\|design\|wiki}`, `Decision{status,…}`, `Ticket`, `Thread`, `Message` | `MENTIONS`, `DOCUMENTED_IN`, `DISCUSSED_IN`, `DECIDED_BY`, `AFFECTS`, `SUPERSEDES`, `IN`, `REPLY_TO`, `SENT`, `WROTE` |
| Episodic | `Incident{id,title,sev,started_at,resolved_at,status,summary,embedding}`, `Alert/Observation`, `AgentStep{agent,action_type,tool,ts}`, `Hypothesis{statement,status,score}`, `Session`, `Message` | `AFFECTED`, `OBSERVED_ON`, `NEXT` (step chain), `SUPPORTED_BY`/`REFUTED_BY`, `ROOT_CAUSE`, `RESOLVED_BY`, `RESPONDED{role}`, `MITIGATED_WITH{outcome,verified}`, `SIMILAR_TO{score,basis}` |
| Procedural | `Runbook{version,status:proposed\|active\|deprecated,last_verified_at}`, `Step{success_count,failure_count}`, `Lesson{text,votes}` | `HAS_STEP`, `NEXT`, `SUPERSEDES`, `MITIGATES`, `DERIVED_FROM` |
| Coordination | `Task{type,status(A2A names),claimed_by,lease_until,version}`, `Handoff{from,to,summary,open_questions,verification_status}`, `Agent`, `IncidentBrief`, `Action{idem_key,tier,status,approver}` | `FOR`, `CARRIES`, `CREATES`, `WROTE` |
| Provenance | `Evidence{id,tool,params,node_ids,edge_ids,ts}`, `Claim`, `Answer`, `AgentRun`, `ExtractionRun` | `CITES{verdict}`, `GENERATED_BY`, `INFORMED_BY` (handoff = PROV `wasInformedBy`) |
| Derived | — | `KNOWS{score,evidence_count,last_evidence_at}`, computed **on demand** (~30 people × ~40 services is cheap) |

† bi-temporal edge

### 6.3 MCP tool catalogue (namespaced; ≤ 9 per agent)
**Read tools** (`readOnlyHint`; each returns `evidence_id`s):
1. `graph_resolve(text, kinds, k)`: entity linking (exact or alias, then full-text, then vector)
2. `graph_schema()`: labels and edge types with **direction written out in English**
3. `graph_neighbors(node_id, rel_types, direction, limit)`: applies the hub rule
4. `incident_blast_radius(service_id, max_hops≤4)`: upstream dependents, hops, entry points, owners
5. `incident_rank_root_causes(incident_id, window_min, max_hops)`: candidates with score breakdowns (§7.1)
6. `graph_explain_connection(from_id, to_id, max_len≤4, k)`: `algo.SPpaths` / `allShortestPaths`
7. `incident_similar(incident_id, k, phase)`: hybrid similarity (§7.2) with fixer, runbook and linking paths
8. `org_ownership(service_id, at=None)`: owner team, on-call, escalation, **as of a given time**
9. `org_experts(service_id | query)`: KNOWS ranking plus top-3 evidence edges; bus factor
10. `knowledge_for_entity(node_id, kinds)`: runbooks, decisions, postmortems ("text to explain")
11. `graph_hubs(metric)`: precomputed PageRank / betweenness with a fixed seed
12. `graph_cypher_readonly(cypher, params, purpose)`: guarded escape hatch (ro_query, EXPLAIN pre-check, bounded `*`, ≤50 rows, shown in the UI)

**Memory writes** (automatic, templated Cypher only):
13. `memory_note(kind: hypothesis|finding|ruled_out, claim, evidence_ids, …)`: rejects unknown evidence IDs
14. `memory_handoff(to_role, summary, open_questions, evidence_ids)`

**Gated actions** (idempotency key = incident + action + target):
15. `action_create_ticket`
16. `action_page_oncall`
17. `action_propose_remediation` (always pending)

**Per agent:**
- **Triage:** 1, 4, 7, 8, 11, 13, 14
- **Investigator:** 1, 3, 5, 6, 7, 10, (12), 13, 14
- **Responder:** 8, 9, 10, 13, 15, 16, 17

### 6.4 The agent loop
1. An alert creates `Incident` + `Task(triage)`. The orchestrator writes the `IncidentBrief`.
2. **Triage** follows a fixed procedure: blast radius → ownership → similar incidents (phase=triage, no root known) → sets severity SEV-1..5 → note + handoff.
3. **Investigator** follows a procedure, then a bounded ReAct (≤ 8–10 tool calls):
   - `rank_root_causes` first (structural pruning);
   - test the top 2 with `explain_connection`, `neighbors` and `similar`;
   - mark `ruled_out` and move on when evidence contradicts (Plan-on-Graph);
   - output strict JSON with claims and evidence IDs, **or "no change-induced cause found"**.
4. **Verifier (code, no LLM):**
   - evidence IDs exist in this incident's registry;
   - the cited edges still exist;
   - entities named in each claim lie on the cited paths;
   - unsupported claims are dropped or flagged ⚠.
5. **Confidence** = the deterministic score from step 3's ranking, optionally × agreement across 3 Investigator samples (self-consistency).
6. **Responder:** page the owner of the *suspect* service, attach the runbook used by the most similar resolved incident, draft the ticket and status text, pre-fill a postmortem skeleton.
7. **Gate:**

   | Tier | Action | Rule |
   |---|---|---|
   | T0 | Notes, handoffs | Automatic |
   | T1 | Ticket | Automatic if score ≥ 0.6 and ≥ 1 verified path; else draft |
   | T2 | Page | Needs approval, unless SEV-1 and score ≥ 0.8 and a verified on-call path |
   | T3 | Rollback / restart | Proposal only |

8. **Close → Consolidate** (background, "sleep-time"):
   - incident summary + embedding;
   - `SIMILAR_TO` links;
   - KNOWS evidence;
   - lesson votes (ExpeL);
   - step statistics;
   - a *proposed* runbook induced from the Responder's trace (AWM);
   - promotion only on verified mitigation or human click (Voyager).

---

## 7. Algorithms

### 7.1 Root-cause score (per candidate change *x* on service *c*)
**Candidates:** services reachable via `(a)-[:DEPENDS_ON*0..5]->(c)` from alerting set A. Changes with `t0−2h ≤ ts ≤ t0+5min` (allowing for clock skew).

`S(x) = 0.30·P + 0.25·R + 0.15·Cov + 0.15·F + 0.10·K + 0.05·H`

| Term | Definition | Source |
|---|---|---|
| P, proximity | `2/(2+h)`, h = minimum hops from A to c | ChangeRCA |
| R, recency | `exp(−Δt/30min)`, or ChangeRCA's doubling buckets; 0 if the change landed after onset + skew | ChangeRCA |
| Cov, coverage | Ochiai: `e_f/√(|A|·(e_f+e_p))`; e_f = alerting services whose downstream closure contains c; e_p = healthy entry points that reach c | TraceRCA / spectrum fault localization |
| F, frontier | 1 if c is alerting and none of its dependencies are; 0.5 if c is silent but all chains converge on it | Groot, CIRCA |
| K, kind prior | `(fails+1)/(changes+2)` for the change kind and service, computed in Cypher; + canary bonus | Gandalf |
| H, history | Maximum similarity to past incidents caused by the same (service, kind) | Zhang et al. 2024 |

- **Ties:** no separate tie-break step in the implementation; candidates are sorted by score (a stable sort). Personalized PageRank (MicroRCA) was considered but is not implemented.
- **Abstain:** if max S < θ, return a non-change hypothesis (frontier or coverage service plus a guessed fault type).
- **Weights:** grid-search on *training* seeds only.

### 7.2 Incident similarity (two phases)
`sim(i,j) = 0.35·s_root + 0.20·s_kind + 0.15·J(symptoms) + 0.15·J(blast radius) + 0.15·cos(text)`
- `s_root = 1/(1 + hops between root services)`
- **Triage phase:** the root is unknown, so use only the symptom, blast-radius and text terms.
- **After investigation:** recompute as an expectation over the top-3 hypotheses.
- **Retrieval:** vector top-20 → graph rerank. Normalize with min-max, plus recency `0.99^days` and a verified-outcome bonus (Generative Agents style).
- Exclude the incident itself and any future incidents (no leakage).

### 7.3 Expertise
```
KNOWS(p,s) = active(p) · Σ_{e∈evidence(p,s)} w(e.type) · 2^(−Δt_e/H)  +  0.3 · Σ_{s'∈DEPENDS_ON±1(s)} KNOWS₀(p,s')
```
- **Weights `w`:** fixer 5, postmortem/decision-record author 4, runbook author 3, ticket resolved 3, deploy 2, review 1.5, helper 1.5, chat answer 1 (capped at 1 per thread).
- **Half-life H:** 180 days for activity, 365 for designs.
- **Bus factor:** the number of active people with normalized KNOWS ≥ 0.75.
- **Free-text "who knows X":** Balog Model 2 over vector hits.
- **Who to page:** `Σ_j sim(i,j)·[p resolved j]·e^(−age/180d)` + an on-call bonus for the suspect service's owner team.

---

## 8. Synthetic world and scenario generator

**World:**
- 30–60 services in a layered DAG: 1–3 entry points → 3–4 business tiers → a leaf infrastructure tier (DB, cache, queue, auth/vault).
- Depth 3–6, out-degree 1–4, preferential attachment so 2–4 hubs have in-degree ≥ 10. Shaped after Online Boutique (11 services), DeathStarBench (36–41) and TrainTicket (41); Alibaba/Meta statistics only, **no redistribution of their traces**.
- 6–10 teams, ~30 people, one reorg, one departure.

**Fault mix:**
- 55–65% change-induced (deploy > config > flag).
- 35–45% not change-induced (capacity, certificate expiry, dependency outage).
- Symptoms propagate upstream with probability 0.6–0.9 per hop.

**Difficulty knobs (without them the benchmark is circular):**
- 3–15 distractor changes inside the window, some closer in time or upstream;
- ~30% silent roots;
- 5–10% false alerts;
- ~10% incidents with two roots.

**Prose:** postmortems, decision records, threaded chats and tickets, LLM-generated *from the ground-truth JSON* (CRMArena). Deliberately inject:
- aliases;
- one stale runbook;
- one superseded decision record;
- one ownership-change chat message.

Verify each artifact against the ground truth.

**Splits:** tuning seeds vs test seeds, plus one held-out topology.

---

## 9. Evaluation shown in the app

| Metric | Track | Target / what it shows |
|---|---|---|
| Root-cause AC@1 / AC@3 / MRR vs baselines | 1 | Graph scoring beats "most recent change" and "LLM without graph" |
| **AC@1 vs number of concurrent changes** (one line per method) | 1 | **The headline plot** |
| Evidence-path validity rate (cited edges that exist) | 1 | ~100% by construction (GCR's "faithful ratio") |
| Abstention precision/recall on non-change incidents | 1 | It says "not a change" when that's true |
| Similar-incident recall@3 (planted twins): no memory vs vector vs vector+graph | 2 | Memory helps; the graph helps more |
| **Learning curve:** steps/tokens to mitigation over 5–10 recurring incidents | 2 | Procedural memory works |
| Knowledge-update test: stale answers after an ownership change | 2/3 | 0 stale; "as-of" queries correct |
| Concurrency: N agents × M incidents → duplicate claims and pages | 2 | 0 duplicates |
| Tenant leakage (same service names in 2 tenants, including get-by-id) | 2 | 0 cross-tenant hits |
| Expertise P@1 vs the generator's latent truth | 3 | "Who should we page" is right |
| Citation precision/recall (ALCE) on ~20 questions | 3 | Answers are sourced |
| Extraction edge precision/recall vs ground truth | 3 | The prose → graph pipeline is sound |

**Baselines:**
1. Random change in the window.
2. Most recent change anywhere.
3. Our toy method (hops, then recency).
4. The weighted score.
5. The weighted score + LLM re-rank with graph tools.
6. An LLM given only the change list and alert text, no graph.

**Honesty:** small n and synthetic data; frame it as a demonstration, not state of the art (OpenRCA and ITBench are at ~11%).

---

## 10. Scope and priorities

| Priority | Item |
|---|---|
| **Must** | Schema + deterministic world/scenario generator · tools 1–8, 13–16 · custom MCP server · 3 agents + state machine · code verifier · evidence registry · consolidation (SIMILAR_TO, lessons) · UI with path highlight · demo script · README (data model + Cypher) · AI-use disclosure |
| **Should** | Mini scoreboard (AC@1 vs baselines, path validity, recall@3) · ownership-change live demo + as-of query · KNOWS + bus factor · action gate UI (approve button) · per-tenant graphs with GRAPH.COPY reset |
| **Could** | LLM extraction from a handful of postmortems/chats with provenance (Track 3 "docs, tickets, chats") · vector similarity · networkx personalized PageRank · runbook induction + promotion · citation verifier on free-form Q&A · learning-curve plot |
| **Won't** | Causal discovery, GNNs, fine-tuning, Microsoft GraphRAG indexer, Graphiti dependency, HTTP A2A, real telemetry time series |

---

## 11. What NOT to do (consolidated)
1. A single generic `query_graph` tool as the main interface.
2. Letting the LLM write Cypher for writes or actions, or string-concatenating values into Cypher.
3. Unbounded `*` traversals, missing `TIMEOUT_MAX`, null grouping keys on 6.x.
4. Global PageRank or betweenness as root-cause evidence (they measure importance, not cause).
5. LLM narrative used as the explanation; trusting LLM citations or verbalized confidence.
6. Deleting facts on contradiction; summaries of summaries; auto-promoting runbooks.
7. Free-text agent chat as the handoff; no termination rule; unlimited tool loops.
8. LLM extraction of facts we already hold as structured data; open, schema-free extraction.
9. Confusing owner with expert; scoring expertise by message count.
10. Easy synthetic data (one-hop solvable), time or label leakage, no "no fault" cases.
11. Real or personal datasets (Enron, TREC mail, IRC); redistributing Alibaba or Meta traces.
12. Letting the LLM supply `tenant_id`/`user_id`; ignoring get-by-id scope.
13. Auto-page or auto-rollback without a gate.
14. Claiming state of the art on LoCoMo / LongMemEval / OpenRCA.

---

## 12. Design decisions
Each item lists the options this study considered and what the implementation chose.
1. **FalkorDB version:** v4.22.0 (mature; A*, Leiden) or 6.0.2 (latest Rust engine, crash bug #3150). **Chosen:** 6.0.2, pinned, with `coalesce()` on nullable grouping keys.
2. **LLM and embeddings:** an LLM provider plus a separate embeddings provider, or no vectors at all (structural + full-text similarity only). **Chosen:** Gemini for both (Pro for the Investigator's review, Flash for extraction and Brain answers, Flash-Lite for dataset prose, 768-d Gemini embeddings). Everything also runs without a key, in a deterministic offline mode.
3. **Agent runtime:** a plain Python state machine + MCP client, or LangGraph. **Chosen:** LangGraph (a StateGraph with an interrupt for human approval).
4. **Real MCP loop vs direct function calling:** Track 1 lists "tool selection through MCP". **Chosen:** a real MCP client loop (`langchain-mcp-adapters` over streamable HTTP), with an in-process fallback for fast evals.
5. **UI:** Streamlit (fastest) or a small web app with `@falkordb/canvas`. **Chosen:** a Next.js web app with its own SVG graph view.
6. **Deployment:** FalkorDB Cloud free tier (deleted after 7 idle days) or Docker on a small VM. **Chosen:** local Docker Compose is the supported setup; a hosted deployment is optional.
7. **Scale of the synthetic world:** ~40 services / 30 people / 15 incidents, or smaller. **Chosen:** larger: 40 services, 15 datastores, 11 teams, 131 people and 397 historical incidents (`datagen/out/acme/manifest.json`).
8. **How much Track 3 prose extraction to build**, since Track 3 explicitly names "docs, tickets, and chats". **Chosen:** rules extraction for all prose, plus Gemini extraction for incident, decision and announcement threads and for postmortems and design docs when a key is set.

---

## 13. Verification flags and conflicts between streams
- **GALA:** two streams cite arXiv **2508.12472** (Tian et al., 2025); one cited 2608.08968 / ASE 2026. Treat **2508.12472** as the reference and recheck before citing a venue.
- **Graphiti on FalkorDB:** the Track 3 stream said "easy"; the FalkorDB stream found the released version breaks on 6.x (issue #1947). The latter is tested against issues, so it wins.
- **`LIMIT $k` parameters:** the MCP server's tool description says no; the live test on 6.0.2 says yes.
- **Venues unconfirmed:** ToG-2, τ-bench, MemoryBank (AAAI), MetaGPT oral status, AutoGen (COLM), RCAEval track, DiLink, MCP-Bench, Memp.
- **Authors unconfirmed:** MicroCause, TraceRCA, MonitorRank's author list (DOI confirmed).
- **Statistics unconfirmed:** the Ant Group change-incident percentages, the Sock Shop service count, FalkorDB's "10k+ tenants" and its benchmark claims (vendor-run).
- The FalkorDB facts in §1 are **VERIFIED-LIVE on 6.0.2** unless marked otherwise; recheck if we switch to v4.22.0.
- All 2026 arXiv items (IDs 26xx) are recent preprints; cite them as preprints.

## 14. Prerequisites (setup)
- Install Python 3.11+ (falkordb-py 1.7.1 requires ≥ 3.10).
- Pull `falkordb/falkordb:v4.22.0` alongside latest, for the version decision.
- Get an LLM API key ready (and an embeddings key, if we use vectors).
- Make the decisions in §12.
