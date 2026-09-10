# API contract

The Python application and hosted frontend use the same `/api/*` paths. The Site proxy does not maintain a parallel `/api/primrose/*` schema and never substitutes fixture data.

## System and original workflow routes

| Method and route | Purpose |
|---|---|
| `GET /api/health` | Graph, extraction, schema, GraphRAG, Gemma explanation, target-generation, and optional-adapter status |
| `GET /api/summary` | Current graph and review counts plus source-document summary |
| `GET /api/documents` | Source documents attributed by the current graph |
| `GET /api/review` | Filtered/paginated review queue |
| `POST /api/review` | Persist a node/edge decision and audit entry |
| `GET /api/search` | Search live graph items |
| `GET /api/graph` | Review-filtered graph slice |
| `GET /api/items/{kind}/{id}` | One live node or edge with review and provenance context |
| `GET /api/audit` | Persisted review/import audit events |
| `GET /api/jobs` and `GET /api/jobs/{id}` | Extraction job list and polling |
| `GET /api/export/graph` | Download all or selected-status graph records |
| `GET /api/export/reviews` | Download review/audit sidecar |
| `POST /api/query` | Deterministic, GraphRAG, or auto-routed graph question |
| `POST /api/graph/import` | Replace or merge a validated JSON graph |
| `POST /api/extractions` | Submit raw PDF/text bytes for background extraction |

`POST /api/extractions?mode=schema|heuristic` accepts the document as the request body, `Content-Type` for the media type, and `X-Filename` for the original filename. It returns `202`; poll the returned job ID. The request body is limited to 25 MiB.

`POST /api/query` accepts:

```json
{
  "question": "Which records connect the named entities?",
  "mode": "deterministic"
}
```

`mode` is `deterministic`, `graphrag`, or `auto`. Explicit `graphrag` requires a ready provider. `auto` falls back to the deterministic engine when the optional model path is unavailable and records that fallback in the trace.

## Ontology routes

| Method and route | Purpose |
|---|---|
| `GET /api/schema` | Full schema catalogue, defaults, and saved request |
| `GET /api/schema/selection` | Resolve the current selection and show prompt costs/warnings |
| `POST /api/schema/selection` | Validate and persist an explicit selection |
| `POST /api/schema/selection/preview` | Resolve a draft without saving it |
| `POST /api/schema/selection/reset` | Remove the saved selection and restore schema defaults |
| `GET /api/schema/prompt?pass=ENTITY|RELATIONSHIP` | Inspect one compiled extraction prompt |
| `POST /api/schema/suggest` | Deterministically suggest an unsaved schema draft from topic text |

Topic suggestion request:

```json
{
  "topic": "ports, logistics and air-defence systems",
  "max_entity_types": 24
}
```

The response includes ranked matched terms, a candidate `module_ids`/`entity_type_ids` request, and the normal prompt preview. Matching is lexical, does not call a model, never saves automatically, and is not an ontology determination.

## PRIMROSE routes

| Method and route | Purpose |
|---|---|
| `GET /api/workbench/bootstrap` | Live context, queues, selected object, graph slice, adapters, methods count, and target metadata |
| `GET /api/workbench/queues?limit=50` | Deterministic Act/Collect/Challenge/Hypotheses/Monitor routes and counts |
| `GET /api/workbench/methods` | All 111 versioned method definitions |
| `GET /api/workbench/methods/{id}` | One method definition |
| `GET /api/workbench/profiles` | Five versioned score profiles and weights |
| `GET /api/workbench/adapters` | Optional provider status and exact unavailability reasons |
| `GET /api/workbench/graph?focus={id}&depth=1&limit=60` | Review-filtered diagnostic projection |
| `GET /api/workbench/objects/node/{id}` | Metrics, gates, traces, evidence, and blocked/available scores |
| `POST /api/workbench/explanations` | Deterministic explanation or configured Gemma synthesis |

Explanation request:

```json
{
  "id": "exact-stable-id",
  "question": "Why is this object in Collect?"
}
```

When Gemma is not explicitly configured, the route returns the deterministic trace. When configured, output must pass the fixed structured schema and may cite only evidence IDs supplied to the provider. Neither path can mutate graph, review state, route, or score.

## Target-development routes

| Method and route | Purpose |
|---|---|
| `GET /api/target-development/meta` | Phases, handling constraints, screening boundary, and model-job availability |
| `GET /api/target-development/candidates` | Exact-ID candidate list and screening results |
| `POST /api/target-development/preview` | Synchronous deterministic dossier/facts/gaps preview |
| `GET /api/target-development/jobs` | Recent persisted model-generation jobs |
| `POST /api/target-development/jobs` | Queue one asynchronous model-backed record |
| `GET /api/target-development/jobs/{id}` | Poll one job |

Preview and job submission use the same bounded request:

```json
{
  "target_id": "exact-stable-id",
  "phase": "basic",
  "depth": 2,
  "limit": 90
}
```

Both paths require an exact stable entity ID, allow-listed phase, depth 1–3, and limit 10–150. Rejected, personnel, area, protected, or otherwise restricted objects fail closed. The preview does not call a model. The job route requires a reachable OpenAI-compatible provider, runs through a single worker, persists state, removes prompts and the duplicated raw evidence block from normal responses, and reports `queued`, `running`, `completed`, `completed_with_errors`, or `failed`.

## Authentication and hosted proxy

Local loopback development may omit `PRIMROSE_BACKEND_TOKEN`. The owner container refuses to start without a token of at least 32 characters and requires it for every route except `/healthz`.

The hosted proxy:

- calls a private `CUSTOMER_HTTP_PRIMROSE_BACKEND` binding when present, otherwise `PRIMROSE_BACKEND_URL`; the supplied owner container still requires the matching Site-side `PRIMROSE_BACKEND_TOKEN` in either topology;
- accepts public backend URLs over HTTPS only (loopback HTTP is allowed for local development);
- adds the bearer token server-side;
- forwards only allow-listed request/response headers and raw body bytes;
- sanitises filenames, limits extraction uploads to 25 MiB, and preserves the 30 MiB graph-import/API allowance;
- rejects cross-origin browser API calls;
- returns `503` instead of demo data if the connection is absent or fails.

## Error semantics

- Unknown route/resource: `404`.
- Invalid payload, selection, mode, or bounds: `400`.
- Missing/invalid service authorisation: `401` in the container wrapper or `403` at the base API boundary.
- Cross-origin state change: `403`.
- Oversized request: `413`.
- Target screening stop: `422`.
- Optional model/dependency unavailable or backend connection failure: `503`.
- An unavailable metric is a normal `200` observation with `raw_value: null` and a reason; it is not an HTTP error.
