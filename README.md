# PRIMROSE Target Development Workbench

An evidence-first analyst workbench built around the supplied Python knowledge-graph application. The original workflow is the product: PDF/text ingestion, ontology scoping, extraction review, graph exploration, deterministic queries, GraphRAG, target development, and import/export remain available in one interface. PRIMROSE queues, diagnostics, method explanations, and model-backed target narratives are additive.

> **Scope:** ANALYST DECISION SUPPORT — REVIEW REQUIRED — NOT AUTHORITY TO ACT. “Act” is a workflow queue for analyst review; it is not an engagement recommendation, legal determination, or means-selection function.

## What is implemented

- `backend/web/` is the authoritative frontend. The Python server serves it locally, and every Site build copies the same three assets to `public/workbench/`; there is no second demo UI.
- Original overview, review, graph, Ask Graph, Sources, and Schema workflows, including PDF/text upload and JSON graph import/export.
- Manual ontology down-selection plus deterministic topic matching that fills an unsaved draft for explicit analyst review.
- PRIMROSE Act, Collect, Challenge, Hypotheses, and Monitor queues over the live graph snapshot.
- A 111-method catalogue and five versioned weighting profiles. Every method exposes its definition, calculation, assumptions, limitations, formula/raw LaTeX, inputs, units, scoring role, and selected-object trace.
- Deterministic target-development dossiers and optional asynchronous model-generated section narratives. Exact stable IDs and fail-closed screening are required.
- Separate runtime status for extraction, GraphRAG, Gemma explanation synthesis, target-development generation, ontology, and optional analytic adapters.
- An owner deployment container with bearer-token authentication and persistent graph, review, extraction, ontology-selection, and target-job data.
- A same-origin Sites proxy that forwards binary uploads and API responses, adds the backend credential server-side, and returns `503` when no live Python backend is configured. No fixture or synthetic fallback is served.

## Run the complete application locally

The Python server is the simplest and authoritative local path. It serves both the UI and `/api/*` from one process:

```bash
cd backend
.venv/bin/python server.py --no-browser --port 8090
```

Open `http://127.0.0.1:8090`. Deterministic review, graph, schema, import/export, and supported graph-query workflows do not require an LLM. PDF schema/heuristic extraction, GraphRAG, explanation synthesis, and generated target narratives additionally require their optional Python dependencies and a reachable OpenAI-compatible chat-completions endpoint.

Start the server with the virtualenv interpreter, not a bare `python3`. The
optional toolchain is installed into `backend/.venv`, and an interpreter without
it fails every model availability check, so extraction, GraphRAG and
target-development generation silently fall back to their deterministic paths.
To create it from scratch:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install langchain-openai langextract pymupdf networkx
```

The model defaults (`google/gemma-4-e4b` at `http://127.0.0.1:1234/v1`) are compiled
into the code, so a stock LM Studio needs no environment variables. Override
them with `KG_MODEL_ID` and `KG_LM_STUDIO_URL` if your setup differs.

To exercise the hosted Vinext/Cloudflare layer locally instead, `npm run dev`
starts the Python backend and the Site preview together:

```bash
npm run dev
```

Set `PRIMROSE_LOCAL_BACKEND_PORT` to change the local Python port, or set
`PRIMROSE_SKIP_LOCAL_BACKEND=1` and `PRIMROSE_BACKEND_URL` when connecting the
preview to a backend that you already run.

If `PRIMROSE_BACKEND_URL` or a private backend binding is absent, the Site UI still loads but API requests intentionally fail closed with `503`.

## Model roles

All model integrations use an OpenAI-compatible endpoint. They may share one loaded model, but their configuration and authority remain separate:

| Role | Required configuration | Authority |
|---|---|---|
| Extraction | `KG_MODEL_ID`, `KG_LM_STUDIO_URL`, `KG_LM_STUDIO_API_KEY` | Produces candidate graph records for review |
| GraphRAG | `KG_REASONING_MODEL_ID` (falls back to `KG_MODEL_ID`) and the same `KG_LM_STUDIO_*` endpoint | Synthesises over a bounded retrieved subgraph; zero score weight |
| PRIMROSE explanation | `PRIMROSE_GEMMA4_MODEL_ID`, `PRIMROSE_GEMMA4_BASE_URL`, optional API key | Explains supplied evidence and deterministic traces; cannot change graph, routes, reviews, or scores |
| Target-development narrative | `TARGET_DEVELOPMENT_MODEL_ID`, `TARGET_DEVELOPMENT_BASE_URL`, optional API key | Writes section narratives in a persisted asynchronous job; cannot override screening |

Target-development variables fall back to the PRIMROSE explanation variables and then the `KG_*` variables. Set exact model identifiers; the application does not relabel one model family as another.

## Owner deployment

```bash
cp deploy/backend.env.example deploy/backend.env
# Set a random PRIMROSE_BACKEND_TOKEN of at least 32 characters and the model values.
docker compose -f compose.owner.yml up --build -d
```

The container binds `127.0.0.1:8080` and persists `/data` in a named volume. Publish it only through an authenticated HTTPS tunnel, then configure the Site with either:

- a registered private HTTP binding exposed as `CUSTOMER_HTTP_PRIMROSE_BACKEND`, plus the matching `PRIMROSE_BACKEND_TOKEN` required by the supplied container; or
- `PRIMROSE_BACKEND_URL=https://...` plus the matching secret `PRIMROSE_BACKEND_TOKEN`.

The hosted frontend cannot run the Python process or a local Gemma service inside the Site worker. A reachable backend—and, for LLM functions, a reachable model endpoint—is an external runtime requirement, not an omitted frontend feature. See [the deployment guide](deploy/README.md).

## Verification

```bash
cd backend
python3 -m compileall -q kg_backend primrose server.py target_development.py
python3 -m unittest discover -s unit_test_kg -p 'test_*.py' -v

cd ..
npm run lint
npm test
```

## Documentation

- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [API contract](docs/API.md)
- [Methods and weighting profiles](docs/METHODS_AND_WEIGHTS.md)
- [Baseline, tests, and assurance findings](docs/BASELINE_AND_ASSURANCE.md)
- [Decisions and limitations](docs/DECISIONS_AND_LIMITATIONS.md)
- [Change summary](docs/CHANGE_SUMMARY.md)
- [Reusable implementation prompt](docs/IMPLEMENTATION_PROMPT.md)
# PRIMROSE_Target_Development_Workbench--1-
