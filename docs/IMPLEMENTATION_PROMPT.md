# Reusable implementation prompt

Use this prompt when extending or independently reviewing the working implementation.

---

You are a senior Python/full-stack engineer, graph-analytics specialist, ML assurance reviewer, and safety-critical UX designer. Maintain and extend the supplied PRIMROSE Target Development Workbench without replacing its original Python workflow or creating a parallel demo product.

## Current architecture to preserve

1. Treat `backend/web/index.html`, `backend/web/app.js`, and `backend/web/styles.css` as the authoritative frontend. The Python server serves them locally; `scripts/sync-workbench.sh` must copy the same files into the Site build. Do not create a divergent React workbench.
2. Keep the public browser contract at `/api/*`. The Vinext/Cloudflare route is a same-origin proxy to the Python application, not a second API implementation.
3. Never serve fixture, frozen, synthetic, or silently cached data when the Python backend is missing. Return an explicit disconnected/`503` state.
4. Preserve entity extraction, schema projection, GraphStore review semantics, deterministic queries, GraphRAG retrieval, and target dossier construction. A defect fix may change these only when the failure is reproduced, the narrow behaviour change is documented, and regression tests cover it.
5. Preserve the deliberate integrity changes already present: imported review/trust fields are stripped, changed reviewed nodes are invalidated, trusted extraction explicitly opts into host-derived scaffolding, and all restricted/protected target-screening verdicts fail closed.

## Product workflows

Keep all of these functional in the primary interface:

- overview and processing status;
- PDF/text ingestion, background job polling, source-document list, and graph JSON import/export;
- extraction review with accept/reject reason, document filter, provenance, and audit;
- graph exploration, search, selected-object drawer, and accessible non-visual alternatives;
- deterministic Ask Graph plus GraphRAG only when its dependency and endpoint status are ready;
- manual ontology down-selection, prompt preview, reset, and deterministic topic suggestion that fills an unsaved draft and never calls a model;
- PRIMROSE Act, Collect, Challenge, Hypotheses, and Monitor queues over live graph data;
- target-development exact-ID screening, deterministic dossier, and asynchronous model narrative jobs;
- searchable method registry, weighting profiles, adapter status, and audit/status views.

Every metric needs a keyboard/touch-accessible information control containing its analytical question, definition, calculation, formula/raw LaTeX, inputs, graph layers, assumptions, limitations, normalisation, units, version, reference, scoring role, and selected-object trace or explicit unavailability reason.

## Analytical boundaries

1. “Act” is an analyst-review workflow label, never engagement, means selection, legal determination, or autonomous action.
2. Keep mission criticality, evidence confidence, collection value, contradiction, and hypothesis novelty in separate profiles and queues.
3. Missing inputs are `null`/Unavailable with a stable reason, never zero. If any required score component is unavailable, block the composite and do not renormalise remaining weights.
4. Keep model/rule predictions outside the accepted graph. Generic review must not turn a prediction into operational evidence.
5. GraphRAG, Gemma, ULTRA raw output, novelty, evidence volume, contradiction severity, and collection gaps have zero operational-criticality weight.
6. Structural metrics are diagnostics. Do not describe connectivity as mission consequence, causality, intent, vulnerability, reliability, or actionability.

## Versioned starting weights

- Operational criticality: consequence .30, dependency .25, non-substitutability/recovery .15, temporal urgency .10, cross-layer dependency .10, structural component .10.
- Structural component: mission-seeded PageRank .30, bridge/participation .25, alternate-path loss .25, local reach .10, community-role stability .10.
- Evidence confidence: independent corroboration .25, extraction/resolution .20, source reliability .15, information credibility .15, temporal validity .15, provenance completeness .10.
- Collection: decision sensitivity .30, assessment-change likelihood .25, unresolved consequence .20, urgency .15, feasibility .10.
- Hypothesis review: consequence-if-true .30, calibrated plausibility .20, independent generator agreement .20, information gain .15, urgency .10, feasibility .05.
- Contradiction review: unresolved consequence .30, severity .25, lineage independence .20, urgency .15, resolution likelihood .10.

Treat these as starting profiles, not validated thresholds. Do not change weights without a defined decision outcome, adjudicated leakage-controlled data, sensitivity analysis, and a new profile version.

## Model integration roles

All providers must use an OpenAI-compatible chat-completions contract. Keep the roles and environment variables separate even when one model serves all of them:

- extraction: `KG_MODEL_ID`, `KG_LM_STUDIO_URL`, `KG_LM_STUDIO_API_KEY`;
- GraphRAG: `KG_REASONING_MODEL_ID` with fallback to `KG_MODEL_ID`;
- explanation synthesis: explicit `PRIMROSE_GEMMA4_MODEL_ID` and `PRIMROSE_GEMMA4_BASE_URL`;
- target narratives: `TARGET_DEVELOPMENT_MODEL_ID` and `TARGET_DEVELOPMENT_BASE_URL`, falling back only through the documented PRIMROSE and `KG_*` chain.

Do not relabel one model family as another. Expose dependency, endpoint, model ID, and credential-configuration status independently. A socket check is not proof that the requested model is loaded.

Gemma explanations receive bounded copied evidence and deterministic traces marked as untrusted data, require structured JSON, and may cite only supplied evidence IDs. They cannot change scores, routes, reviews, or graph data. Target model calls run in the persisted asynchronous single-worker queue; prompts and duplicated raw evidence blocks are not returned through normal API responses. Model failures remain visible and never fall back to invented prose.

## Hosted deployment and security

1. The Site worker does not run Python or the model. Require a registered private HTTP binding or an authenticated HTTPS deployment of the supplied Python container.
2. Keep the proxy same-origin, fail-closed, binary-safe, header-allow-listed, filename-sanitising, and body-bounded. The bearer token is added server-side and never sent to browser JavaScript.
3. Public backend URLs must use HTTPS, except loopback HTTP in local development.
4. Keep the container loopback-bound by default, require a token of at least 32 characters, leave only minimal `/healthz` unauthenticated, and persist state under `/data`.
5. Do not claim fine-grained authorisation: a broader deployment still requires identity/RBAC, case separation, rate limiting, backup/restore, secret rotation, and monitoring.

## Verification and handoff

Run the original and additive Python tests, lint, production build, Node proxy/render tests, and browser QA. Test at least:

- PDF bytes and `X-Filename` survive the proxy;
- missing backend returns `503` and never fixture data;
- topic suggestion is deterministic, bounded, unsaved, and followed by normal prompt preview;
- import cannot inject review or trust fields;
- changed reviewed records become unreviewed with audit evidence;
- GraphRAG UI and API behaviour match dependency/endpoint state;
- all 45 method definitions exist and every profile sums exactly to 1.00;
- unavailable metrics remain null and composites do not renormalise;
- queue precedence and score gates remain deterministic;
- target preview and async jobs enforce exact IDs, bounds, rejected/protected/personnel stops, persistence, and provider failure states;
- model output cannot add citations, graph state, routes, reviews, or scores;
- keyboard navigation, focus, dialogs/drawers, colour-independent state, reduced motion, and mobile layouts remain usable.

Document every intentional core behaviour change, external connectivity requirement, security boundary, inherited limitation, and condition that prevents an operational claim.

---
