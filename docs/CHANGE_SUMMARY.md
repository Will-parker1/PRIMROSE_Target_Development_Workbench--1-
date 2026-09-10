# Change summary

## Restored and retained

- The original `backend/web` interface is again the primary product surface, with Overview, Review, Explore, Ask Graph, Sources, and Schema intact.
- Entity extraction, schema-guided and heuristic extraction, GraphStore, review, import/export, search, deterministic query, GraphRAG retrieval, and target dossier construction remain Python-backed workflows.
- PDF/text upload and job polling use the live extraction API rather than frontend fixture data.
- The original visual language and navigation model were extended instead of replaced by the React demonstration.

## Added to the authoritative workflow

- PRIMROSE Act, Collect, Challenge, Hypotheses, and Monitor views over the live graph.
- Live object diagnostics, routing gates/traces, source context, audit/status, adapter state, and navigation into graph and target development.
- A searchable 111-method catalogue with accessible information controls and selected-object calculations.
- Five versioned, non-renormalising weighting profiles.
- Deterministic topic-assisted ontology narrowing. It fills an unsaved manual draft, exposes lexical matches and prompt cost, and requires explicit save.
- Deterministic target dossiers plus persisted asynchronous model narrative jobs through the original `develop()` function.
- Separate OpenAI-compatible configuration roles for extraction, GraphRAG, Gemma explanation synthesis, and target narrative generation.
- An authenticated owner container, loopback-only Compose exposure, persistent `/data` volume, minimal health endpoint, and deployment guide.
- A Sites build sync that publishes the exact `backend/web` assets and a same-origin `/api/*` proxy supporting binary uploads, route-specific timeouts, private binding or authenticated HTTPS, and fail-closed `503` errors.

## Removed

- The alternate React workbench and its duplicated frontend type/API layer.
- The 34,000-line bundled synthetic snapshot.
- The `/api/primrose/*` demo/fallback route.
- Claims that the hosted frontend is live when no Python backend is connected.

## Integrity remediations

- Client imports can no longer inject review decisions or `host_derived` trust state.
- The trusted extraction path explicitly opts into its own host-derived provenance records.
- Reviewed nodes are returned to unreviewed when a merge changes their payload, with an audit event.
- Target screening now fails closed for every restricted/protected verdict; the legacy personnel override no longer bypasses it.
- GraphRAG selection is disabled when its dependency or endpoint status is unavailable.
- Graph colours no longer rely on inline styles blocked by the server's Content Security Policy.
- Source/schema state is refreshed after changes so the UI does not present stale workflow data.

## Deliberately not added

- No fabricated ULTRA, rule, temporal, calibration, provenance, or model results.
- No model-generated graph mutations or accepted facts.
- No universal blended priority score.
- No automatic acceptance, engagement recommendation, means selection, legal determination, or production threshold.
- No embedded Python runtime, database, or model inside the Site worker.
- No public unauthenticated backend exposure.
