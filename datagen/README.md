# datagen: synthetic world generator

Builds the fictional company **Acme Commerce** (and a small second tenant, **Globex Retail**) described in [`../docs/DATA_SPEC.md`](../docs/DATA_SPEC.md). 100% synthetic: fictional people (Faker), the fictional domains `acme-fake.dev` and `globex-fake.dev`.

```bash
cd graphable
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python faker
.venv/bin/python -m datagen                    # both tenants, offline template prose (~1 s)
GEMINI_API_KEY=... .venv/bin/python -m datagen --render gemini   # richer LLM prose (cached in datagen/.cache)
```

The output is deterministic (`seed=20261015`), so the same code always produces byte-identical structured data.

Running the generator rewrites `datagen/out/`. The committed dataset was rendered with `--render gemini` (see `render_mode` in `out/acme/manifest.json`), so run the generator only if you want to replace it. `python -m graphable.bootstrap` does not regenerate when `out/acme/manifest.json` exists (unless you pass `--regenerate`).

| Module | Role |
|---|---|
| `catalog.py` | Hand-designed catalog: 40 services, 15 datastores, 11 teams, topology, failure mechanisms, personas |
| `world.py` | Org and its history (reorg, Orbit team, joiners, departures), bi-temporal ownership/membership/dependencies, on-call, endpoints, flags, hidden expertise and fragility |
| `timeline.py` | 12-month discrete-event simulation: changes, bad-change sampling, propagation, alerts, response, runbooks, decisions, help threads, chatter, wiki, plus fact cards for every prose artifact |
| `render.py` | Fact cards → text. Template mode is offline; Gemini mode uses the LLM with a fact-coverage gate |
| `scenarios.py` | Held-out sets: 40 test scenarios (with traps), 40 train scenarios (for tuning only), 8 learning-curve, 4 hero demo, knowledge-update probes, 30 Brain Q&A |
| `validate.py` | Gates: referential integrity, employment windows, one owner per entity, time windows, hubs/cycle, 3 injection traps, no PII |
| `export.py` | JSONL + `manifest.json` |

**Output:** `out/<tenant>/{world,events,artifacts,truth,scenarios}`. **`truth/` is never loaded into the graph**; only the eval harness reads it.
