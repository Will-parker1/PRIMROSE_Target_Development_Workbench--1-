# AI Enabled Knowledge Graph

A local proof-of-concept application for turning extracted entities and
relationships into a human-reviewed, queryable knowledge graph. It combines a
clean browser interface with the supplied Python extraction and reasoning code.

The application deliberately separates candidate extraction from accepted
graph content. Analysts can accept, reject or defer each entity and
relationship; decisions and reasons are written to an audit sidecar rather than
silently changing the source graph.

## Start the application

The core review, explore and query application requires Python 3.11 or later
and no third-party packages.

```bash
cd kg_application
python server.py
```

Open <http://127.0.0.1:8080>. The server opens the page automatically unless
started with `--no-browser`.

Useful options:

```bash
python server.py --no-browser
python server.py --port 8090
python server.py --graph /absolute/path/to/graph.json      # or .graphml
python server.py --state /absolute/path/to/review_state.json
```

Keep the default loopback host for normal use. There is no multi-user identity
or authorisation layer in this local POC. A separately hosted same-origin web
proxy must protect the Python service with a shared backend credential:

```bash
export PRIMROSE_BACKEND_TOKEN='a-long-random-secret'
python server.py --host 0.0.0.0 --no-browser
```

When that variable is set, every `/api` request requires
`Authorization: Bearer <the same secret>`. Keep the secret in the server and
trusted proxy configuration; never place it in browser JavaScript or local
storage. The legacy page served directly by `server.py` does not inject this
credential and is intended for the default loopback configuration. This shared
service token authenticates the proxy, not individual analysts, and does not
replace user authorisation, workspace isolation, TLS or rate limiting.

## Workflow

1. **Sources** — load NetworkX node-link JSON or optionally extract a document.
2. **Schema** — choose which schema modules and entity types the extraction may
   use. Predicates follow from that choice; see below.
3. **Extract** — create candidate entities and relationships with the local
   LangExtract/Gemma pipeline.
4. **Review** — accept or reject candidates; a reason is mandatory for a
   rejection. Keyboard shortcuts are A, R and S.
5. **Explore** — inspect a deliberately bounded neighbourhood. Rejected nodes
   and relationships are excluded.
6. **Ask graph** — run deterministic graph questions, or GraphRAG answers from a
   local model, with the consulted nodes, relationships and method shown
   alongside the answer.

The detail drawer contains source context, review state and technical metadata
without crowding the main workflow.

## Schema-guided extraction

`UK_NATO_Targeting_LangExtract_Gemma_Context_IESv5_v1.0.json` holds 166 entity
types, 210 relationship types and 15 controlled value sets across ten modules,
with IES 5.0.3 pattern mappings. It is a reference context, not a prompt: its
own loading rule forbids serialising the whole file into a chunk prompt. The
application selects a slice, compiles that slice into the prompts, and validates
the model's output back against it.

**Extraction mode** is chosen on **Sources**:

- *Schema-guided* — two passes against the selected slice, emitting canonical
  schema ids. This is the default when the schema document loads.
- *Heuristic* — the original per-corpus prompts in `building_kg/pdf_to_kg.py`,
  which pick their profile from the document's own content.

### Down-selecting what gets extracted

**Schema** is where an analyst says what they are interested in. Ticking modules
sets the pool; ticking entity types narrows it further. Predicates are not
chosen directly — a predicate stays in scope only when a selected entity type
satisfies both its declared domain and its declared range, so no extraction can
produce an edge pointing at something you asked not to extract. Predicates the
schema marks `HOST_DERIVED_ONLY` are never requested from the model at all.

The panel shows the surviving predicates, every predicate the closure dropped
and why, and the compiled size of both prompts. That last number matters: all
ten modules compile to roughly 47,000 characters of predicate vocabulary, which
no 4B model will hold apart. A working selection — say CORE plus TP3 with six
entity types — compiles to about 13,000, and both prompts keep their full
definitions and surface forms. **Entity prompt** and **Relationship prompt**
show exactly what the model will be sent.

The selection persists to `runtime/extraction_selection.json` and is frozen into
each job at submission, so changing it mid-run cannot change what a queued
document is extracted against.

The topic helper can produce an initial reviewable slice from plain words such
as “system components and dependencies”:

```http
POST /api/schema/suggest
Content-Type: application/json

{"topic":"system components and dependencies","max_entity_types":24}
```

This is a deterministic lexical match over module, entity and predicate
metadata. It makes no model call, does not save the selection, never falls back
to loading the full schema when nothing matches, and returns its matched terms
plus the normal closure and prompt-size preview. The analyst must review the
suggestion and explicitly save it through `/api/schema/selection` before it can
affect extraction.

### What the two passes produce

Per the schema's runtime contract, ENTITY and RELATIONSHIP are separate
`lx.extract()` calls with separately filtered few-shot examples. Every record is
checked before it reaches the graph:

- the recorded span must slice back to exactly the extraction text;
- `type_id` and `predicate_id` must be in the selected registry;
- subject and object types must satisfy the predicate's domain and range through
  the local parent hierarchy;
- polarity, certainty, modality, source direction and review action must be
  permitted values, and the claimed review action must be one the predicate's
  `model_action` allows;
- every quoted time, authority, evidence and value string must occur verbatim in
  the source, so a missing qualifier cannot be invented to complete a record.

A canonical id with the right local name but the wrong namespace prefix is
normalised when it resolves unambiguously inside the selection — a small model
reliably writes `tgt_rel:providesInputTo` for `core_rel:providesInputTo`. That
is identifier repair, not interpretation: controlled values are never repaired,
because changing one would change the claim.

Projection follows the schema's graph projection contract. The entity pass
creates grounded mention records and does not merge them, since resolution is a
separate reviewed decision. The relationship pass creates a reified source
assertion — or a prediction, when modality is `MODEL_PREDICTED` — carrying the
evidence span, polarity, modality, certainty and time wording, linked to its
subject and object mentions. Nothing is promoted to accepted graph truth; every
record enters the review queue as a candidate.

Each run's selection, prompt sizes, per-pass counts, rejection reasons and
identifier repairs are written into `runtime/extractions/<job>.json` and
summarised on the **Sources** job list.

### From the command line

```bash
python building_kg/schema_extractor.py document.pdf -o out.json \
  --module tgt:TP3 \
  --entity-type core:TargetSystem --entity-type core:TargetSystemComponent \
  --entity-type core:Function

# inspect the compiled prompt without calling a model
python building_kg/schema_extractor.py document.pdf --module tgt:TP3 \
  --print-prompt RELATIONSHIP
```

### Scope

The schema prohibits selecting, recommending or prioritising real targets,
capability or means selection, weaponeering, collateral-damage estimation,
automated legal determination, and any automatic promotion of model output into
accepted graph truth. Those prohibitions are compiled into both prompts. The
supplied data is synthetic, and this remains a local proof of concept, not an
operational authority or decision system.

## Data currently supplied

`graph.graphml` is the graph loaded by default: the corporate and
critical-infrastructure network for the `Data/corpus` documents, 360 entities
and 936 relationships. Entities are typed as Person, Organisation, Facility,
Location, Jurisdiction or Equipment, and carry aliases, sector, criticality,
capacity and commissioning attributes. Relationships include BOARD_MEMBER_OF,
MAINTAINS, SUPPLIES, OPERATES, OWNS and DEPENDS_ON, with stakes, commodities
and validity dates where the source records them.

GraphML is read with the standard library, so the core application still has no
third-party runtime dependency. **A GraphML source is never written to**: merges
from **Sources** and completed extraction jobs are written to a JSON sidecar
beside it (`graph.graphml` → `graph.json`), which then becomes the live graph on
the next start. Delete the sidecar to return to the pristine GraphML.

`osint_knowledge_graph.json` is the PRIMROSE Ukraine OSINT knowledge graph
extracted from 48 synthetic source packets, typed as State/Polity, Military
force/org, System/equipment, Location/infrastructure or Concept/capability, with
confidence, time scope and illustrative PHIA per claim. It loads with
`python server.py --graph osint_knowledge_graph.json`.

`knowledge_graph.json` is the original sample movie graph from the uploaded
backend. It is retained for interface testing and loads with
`python server.py --graph knowledge_graph.json`.

All of these are synthetic. This is not a targeting dataset and is not an
IES-conformant interchange document. Replace the graph through **Sources**, or
pass another with `--graph`, before domain evaluation. Review decisions are keyed
to the loaded graph, so keep a separate `--state` file per graph if you review
more than one.

The accepted-only export is the projection gate for downstream use. The full
candidate export preserves every record and annotates it with review status.

## Node-link input contract

The importer accepts a JSON object with `nodes` and `links` arrays:

```json
{
  "directed": true,
  "multigraph": true,
  "graph": {"name": "Example graph"},
  "nodes": [
    {"id": "entity-1", "label": "Example entity", "type": "Organisation"}
  ],
  "links": [
    {
      "assertion_id": "assertion-1",
      "source": "entity-1",
      "target": "entity-2",
      "relation": "DEPENDS_ON",
      "source_document": "example.pdf",
      "source_text": "Exact supporting text",
      "confidence": 0.83
    }
  ]
}
```

Each node requires a unique `id` (or a usable `name`/`label`). Every link must
reference known endpoints. Supply a stable `assertion_id` wherever possible;
otherwise the application derives one from the assertion and its provenance.
Parallel assertions with different evidence are retained.

The node-link format is an application projection, not a claim of IES v5/RDF
conformance. A production implementation should validate and map application
classes, identifiers, temporal qualification, provenance and security markings
at its ingestion/export boundary.

## Optional GraphRAG with the local LM Studio model

**Ask graph** answers with GraphRAG by default: it retrieves a bounded subgraph
and passes it to the same local OpenAI-compatible endpoint the PDF extraction
pipeline uses, through LangChain's `ChatOpenAI` client. The deterministic engine
remains available in the **Ask graph** sidebar for exact counts, rankings and
paths, and needs nothing beyond the standard library.

GraphRAG needs LangChain installed and a local model served. Create the virtual
environment described under PDF extraction below (or install just
`langchain-openai`), start LM Studio, then run the server from that environment:

```bash
python -m venv .venv
.venv/bin/pip install langchain-openai      # or: pip install -r requirements.txt
.venv/bin/python server.py
```

Running `server.py` with an interpreter that has no LangChain leaves **Ask
graph** on deterministic answers and shows "Install the optional LangChain
toolchain" in the sidebar.

Defaults can be changed per run:

```bash
python server.py --query-mode deterministic
export KG_QUERY_MODE=auto              # the same setting via the environment
export KG_REASONING_MODEL_ID=google/gemma-4-e4b   # defaults to KG_MODEL_ID
export KG_LLM_TIMEOUT=120              # seconds allowed for one answer
```

| Mode | Behaviour |
|---|---|
| `deterministic` | Graph queries only. No model call, no optional dependency. |
| `graphrag` | **Default.** Always uses the local model. Returns 503 when it is unavailable, rather than quietly answering another way. |
| `auto` | Uses the local model when it is reachable, otherwise falls back to the deterministic engine and says so in the trace. |

Retrieval is deterministic and runs entirely over the review-filtered graph:

1. The question is resolved to node ids in reading order, so "the path between
   A and B" is retrieved from A to B. Entity and relationship-type mentions
   are matched exactly, then by substring, then (when `rapidfuzz` is
   installed) by fuzzy string match, so a misspelled name such as "Sara
   Conner" still resolves to "Sarah Connor" instead of returning nothing.
   `rapidfuzz` is optional: without it, matching still works on exact and
   substring mentions.
2. A bounded neighbourhood is collected, capped at 120 relationships and
   roughly 12,000 characters of context. Depth adapts to how many entities the
   question names: two hops for a single entity, one hop when several resolve,
   because several two-hop windows over a small graph retrieve so much of it
   that the answer loses focus. When two entities resolve, the shortest
   retained path is retrieved as well.
3. When one entity resolves and the question also names a relationship type
   that is not on that entity's own edges — "who directed X" — the graph is
   searched outward one hop at a time until the nearest matching relationship
   is found, rather than assuming a fixed hop count. The deterministic engine
   uses the same search as a fallback whenever a two-entity path question
   ("How is X connected to Y?") does not resolve to two entities.
4. Rejected entities and relationships never enter the prompt. When nothing
   resolves, a high-degree region is retrieved instead.
5. Every record placed in the prompt is returned with the answer, so the
   citation chips and detail drawer show exactly what the model was given.

The retrieved rows are wrapped in a `<graph-data>` block and marked as
untrusted data rather than instructions. The model is told not to invent
records, to flag unreviewed candidates, and never to present connectivity as
criticality. `GET /api/health` reports whether LangChain is installed and
whether the endpoint answered a TCP probe, so a stopped LM Studio fails fast
instead of hanging the request.

Model answers remain candidate analysis. Check them against the returned
evidence before relying on them.

## Optional PDF extraction with Gemma

Install the complete optional toolchain:

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

That file also carries the optional reasoning experiments, which pull PyTorch,
transformers and spaCy — several gigabytes. For GraphRAG answers and PDF
extraction only, install the shipped features alone:

```bash
pip install langchain-openai langextract pymupdf networkx
```

Requires Python 3.11 or later. `spacy` must be 3.8 or newer: the older 3.7 pin
has no wheels for recent Python and fails while building `thinc`/`blis` from
source.

Run an OpenAI-compatible local endpoint such as LM Studio, then configure it if
the defaults differ:

```bash
export KG_MODEL_ID=google/gemma-4-e4b
export KG_LM_STUDIO_URL=http://127.0.0.1:1234/v1
export KG_LM_STUDIO_API_KEY=lm-studio
```

Uploads accept PDF and UTF-8 text documents, are limited to 25 MiB and are
processed one at a time. The document kind is detected from the uploaded bytes
rather than the filename. Extraction jobs are local and in-memory for this POC.
Completed candidates are merged into the review queue. The extraction prompt in
`building_kg/pdf_to_kg.py` should be replaced or extended with the approved
application profile for the operational domain.

In the graph explorer, node fill carries the entity type and node stroke carries
review status, so the two signals stay independent. The detail drawer shows
extraction provenance — claim status, source document, grade, office held,
disclosed holding — as fields, and flags analytic-rule records that describe how
to model the data rather than facts about the subject.

### Extraction profiles

`building_kg/pdf_to_kg.py` ingests both `.pdf` and `.txt` documents. The two
corpora are unrelated domains, so each has its own prompt, closed predicate
vocabulary and entity types, selected per document:

| Profile | Corpus | Detected by |
|---|---|---|
| `packet` | Russia-Ukraine source packets (`runtime/uploads/*.pdf`) | `SYNTHETIC SOURCE PACKET` |
| `corpus` | Corporate/infrastructure documents (`Data/corpus/*.txt`) | `[SYNTHETIC CORPUS` |

```bash
# whole corpus, profile detected per document, resumable
python building_kg/pdf_to_kg.py Data/corpus -o osint_knowledge_graph.json \
    --checkpoint-dir runtime/checkpoints --batch-size 10

# resume after an interruption; finished documents are skipped
python building_kg/pdf_to_kg.py Data/corpus -o osint_knowledge_graph.json \
    --checkpoint-dir runtime/checkpoints --resume

# restrict to one file type or force a profile
python building_kg/pdf_to_kg.py Data --pattern "*.txt" --recursive --profile corpus
```

For `Data/corpus` documents the `DOCUMENT ID`, `TYPE`, `DATE` and `ORIGIN`
header fields are read with a regex rather than by the model, and become edge
provenance (`claim_id`, `doc_type`, `time_scope`). A failed document is logged
and skipped rather than ending the run.

## Target development: preview and asynchronous generation

Target development retains the existing deterministic dossier, screening and
section prompts. The browser-facing boundary has two deliberately different
modes:

- `POST /api/target-development/preview` assembles graph facts, required
  elements and gaps without calling a model.
- `POST /api/target-development/jobs` queues model-backed section generation
  and returns immediately with HTTP 202. Poll the job-specific GET route until
  it reaches `completed`, `completed_with_errors` or `failed`.

```http
POST /api/target-development/jobs
Content-Type: application/json

{"target_id":"FAC-0009","phase":"basic","depth":2,"limit":90}
```

Generation uses one worker because a record makes several sequential model
calls. Job sidecars are written to `runtime/target-development/<job-id>.json`;
completed results survive restart, while interrupted queued/running jobs are
marked failed on restart. The normal API result omits raw prompts and the
duplicated evidence block. Per-section errors remain explicit and never remove
the other sections.

The provider must expose OpenAI-compatible chat completions. Configure it only
on the backend:

```bash
export TARGET_DEVELOPMENT_MODEL_ID='your-exact-model-id'
export TARGET_DEVELOPMENT_BASE_URL='https://model.example/v1'
export TARGET_DEVELOPMENT_API_KEY='server-side-secret'
```

Those variables fall back, in order, to the corresponding
`PRIMROSE_GEMMA4_*` values and then the original `KG_*` model settings. The
metadata route reports the effective model, endpoint and dependency/readiness
state but never returns the API key. Rejected, protected, restricted, area and
personnel records fail closed before a job is accepted; model generation cannot
override screening or review state.

## Architecture

| Component | Purpose |
|---|---|
| `web/` | Responsive six-view browser interface |
| `server.py` | Standard-library static server and JSON API |
| `kg_backend/store.py` | Validation, indexing, review state, audit and exports |
| `kg_backend/query.py` | Deterministic evidence-returning graph questions |
| `kg_backend/graphrag.py` | Bounded subgraph retrieval and LangChain GraphRAG answers |
| `kg_backend/jobs.py` | Optional background document extraction adapter |
| `kg_backend/schema.py` | Targeting schema registry, module selection and record validation |
| `kg_backend/schema_prompt.py` | Compiles a selected slice into the two pass prompts |
| `kg_backend/schema_service.py` | Serves the catalogue and persists the analyst's selection |
| `kg_backend/graphml.py` | Standard-library GraphML reader |
| `primrose/target_jobs.py` | Persistent asynchronous target-development model jobs |
| `building_kg/schema_extractor.py` | Schema-guided two-pass extraction and graph projection |
| `building_kg/pdf_to_kg.py` | Heuristic LangExtract/Gemma PDF- and text-to-MultiDiGraph pipeline |
| `reasoning_kg/` | Optional bounded reasoning experiments; not used by default |
| `visualisations_kg/` | Optional standalone PyVis export |
| `graph_to_html.py` | Self-contained interactive HTML export of a graph |
| `target_development.py` | CJCSI-structured target development over a selected entity |

Runtime decisions, uploads, extraction outputs and graph backups are stored
under `runtime/` unless `--state` points elsewhere. Source graph imports are
validated before any file is replaced, and a timestamped backup is created.

## Interactive graph export

`graph_to_html.py` writes the loaded graph to one self-contained HTML file. The
data is embedded and nothing is fetched at runtime — no CDN, no external
stylesheet — so the file can be opened from disk or passed on and it will render.

```bash
python graph_to_html.py                                   # graph.graphml -> graph.html
python graph_to_html.py --focus "Bereznyi Hydroelectric Plant" --depth 2
python graph_to_html.py --graph osint_knowledge_graph.json -o osint.html
```

The page colours entities by type using the same palette as the web interface,
filters by entity type and relationship type, searches, highlights a selected
entity's neighbourhood, and lists every attribute the source graph holds for a
selection. Drag to reposition, scroll to zoom, double-click to isolate a
neighbourhood. Node size and label priority follow connectivity, which is a
legibility device and not a claim about importance.

## API summary

If `PRIMROSE_BACKEND_TOKEN` is set, every route below requires the matching
`Authorization: Bearer ...` header. Requests without it fail with HTTP 403.

| Method and route | Purpose |
|---|---|
| `GET /api/health` | Application, extraction and reasoning dependency status |
| `GET /api/summary` | Counts, review progress and composition |
| `GET /api/review` | Paginated review queue |
| `POST /api/review` | Record an analyst decision |
| `GET /api/graph` | Bounded graph neighbourhood |
| `GET /api/search` | Entity and relationship search |
| `POST /api/query` | Graph question; `mode` is `deterministic`, `graphrag` or `auto` |
| `POST /api/graph/import?mode=merge` | Validate and import node-link JSON |
| `POST /api/extractions?mode=schema` | Queue a document for extraction; `mode` is `schema` or `heuristic` |
| `GET /api/schema` | Full schema catalogue: modules, types, predicates and value sets |
| `GET /api/schema/selection` | The saved selection, resolved, with prompt sizes |
| `POST /api/schema/selection` | Save a selection after validating it resolves |
| `POST /api/schema/selection/preview` | Resolve a candidate selection without saving |
| `POST /api/schema/suggest` | Deterministically suggest an unsaved, reviewable schema slice from topic words |
| `POST /api/schema/selection/reset` | Return to the schema's own runtime defaults |
| `GET /api/schema/prompt?pass=ENTITY` | The compiled prompt text for a pass |
| `GET /api/target-development/meta` | Screening phases and model-generation readiness |
| `GET /api/target-development/candidates` | Paginated entities with their screening results |
| `POST /api/target-development/preview` | Build a deterministic, model-free dossier preview |
| `POST /api/target-development/jobs` | Queue asynchronous model-backed generation |
| `GET /api/target-development/jobs` | List persisted target-development jobs |
| `GET /api/target-development/jobs/{id}` | Poll one target-development job and result |
| `GET /api/audit` | Read recent review and integrity audit events |
| `GET /api/export/graph?status=accepted` | Export only accepted content |
| `GET /api/export/graph` | Export all candidates with review state |
| `GET /api/export/reviews` | Export decisions and audit history |

## Tests

Run the standard-library suite from the project root:

```bash
python -m unittest discover -s unit_test_kg -p "test_*.py" -v
```

The tests cover validation, parallel assertions, review persistence, rejected
record exclusion, merge/replace behaviour, import trust boundaries,
deterministic questions, GraphRAG retrieval and mode routing, schema topic
suggestion, asynchronous target-job contracts, backend bearer authentication,
API routes, security headers and static assets.
GraphRAG retrieval is tested without any optional dependency; the generation
tests use a stub chat model and are skipped when LangChain is not installed.
No test contacts a live model endpoint.

## POC boundaries

- The default web path is single-user and local. The optional backend bearer
  token authenticates a trusted proxy, not an analyst. Add end-user identity,
  authorisation, a durable database and concurrency controls before shared use.
- Connectivity is not criticality. The interface and query engine state this
  explicitly and do not infer targeting judgements.
- LLM-generated free-form Cypher is disabled. The optional Neo4j helper permits
  only a bounded parameterised neighbourhood query and expects a read-only
  database account.
- GraphRAG never lets the model choose what to retrieve. Retrieval is a fixed,
  bounded traversal of the reviewed graph, and the model only ever sees the rows
  that are also returned to the analyst.
- Model output remains candidate information until reviewed. Validate provenance,
  classifications, handling caveats and ontology constraints at every external
  boundary.
