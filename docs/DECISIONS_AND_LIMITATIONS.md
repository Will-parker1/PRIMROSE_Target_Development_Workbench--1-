# Decisions and limitations

## What is defensible now

The application is viable as analyst decision support over a live, persisted graph. It supports ingestion, ontology scoping, review, exploration, deterministic queries, bounded GraphRAG, diagnostic queues, and screened target-development dossiers. It is not defensible as an autonomous priority engine or authority for consequential action.

The strongest analytical decision is to keep these questions separate:

- **Act:** Which analyst-review actions pass every configured hard gate?
- **Collect:** Which explicit evidence question is most decision-relevant?
- **Challenge:** Which material contradiction should be adjudicated first?
- **Hypotheses:** Which isolated model/rule candidate deserves validation first?
- **Monitor:** Which records satisfy none of the above routes?

Queue precedence remains safety/context failure → hypothesis → contradiction → Act gates → Collect → Monitor. “Act” is a software workflow label, not an engagement recommendation.

## What works without an LLM

With the Python backend available, graph import/export, review and audit, source lists, manual and topic-assisted schema narrowing, graph exploration, deterministic questions, PRIMROSE topology diagnostics, method definitions, routing traces, and deterministic target dossiers work without a model.

LLM-dependent features are independently unavailable unless their dependencies and endpoint are ready:

- schema/heuristic PDF or text extraction;
- GraphRAG synthesis;
- Gemma explanation synthesis;
- generated target-development section narratives.

The UI reports these states separately and disables model actions that are not ready. It does not imply that “backend connected” means “model connected”.

## Why operational scores remain unavailable

The loaded graph generally lacks a versioned decision horizon, accepted-operational epistemic state, assertion-level source lineage, independent corroboration, collection opportunities, report/observation/transaction times, historical snapshots, layer mapping, capacities/flows, validated cascade models, stability runs, and adjudicated labels. A numeric operational score built from those absent inputs would imply knowledge the graph does not contain.

The application therefore reports unavailable values with reasons and blocks a composite when any required contributor is missing. Structural metrics are diagnostics, not proof of consequence, intent, causality, vulnerability, or actionability.

## Integrity decisions

The original workflow remains authoritative, but several inherited fail-open or integrity defects were deliberately fixed rather than frozen:

- imported JSON cannot inject managed review decisions or the trusted `host_derived` flag;
- trusted extraction may preserve only its own deterministic provenance scaffolding;
- changing a reviewed node through merge invalidates the old decision and writes an audit event;
- protected/restricted objects and personnel cannot be forced through target development by a legacy flag;
- GraphRAG is selectable only when its dependencies and endpoint are ready;
- CSP-blocked inline graph colours were replaced by CSS classes.

These are intentional behavioural changes and must remain regression-tested.

## Tool-specific boundaries

- **GraphRAG:** bounded retrieval and corpus synthesis; zero numerical weight. An answer is not a causal or score trace.
- **Gemma explanation provider:** optional synthesis over copied traces and evidence. Structured output and citation allow-list checks reduce, but do not eliminate, model error.
- **Target narrative model:** writes prose into an asynchronous review record; it cannot override exact-ID screening or turn gaps into established facts.
- **ULTRA:** static, structure-only link-candidate ranking. Its raw score is not a calibrated probability, causal claim, or temporal forecast.
- **AnyBURL/AMIE:** symbolic candidates require exception/coverage reporting and review outside the accepted graph.
- **TLogic/TILP:** temporal candidates require real event-time history and a fixed forecast horizon.
- **PageRank/centrality:** useful only under explicit relation semantics, direction, layers, and missing-data sensitivity; otherwise they can rank collection artefacts.
- **Resilience/cascade:** current topology provides structural proxies only. Capacity-aware causal or reliability claims require domain models and data.

## Hosted deployment boundary

The Site is not a self-contained Python or model host. It contains the authoritative static frontend and a same-origin proxy. Fully live hosted workflows require either a registered private HTTP binding or an authenticated HTTPS deployment of the supplied Python container. LLM workflows additionally require that Python host to reach an OpenAI-compatible model endpoint.

The proxy deliberately returns `503` if no backend is attached. This is an honest connectivity requirement, not a demo mode. D1 and R2 are not used by the current design; persistent application state remains on the backend volume.

The container provides a service-level bearer-token boundary, not fine-grained user authorisation. The owner-only Site and proxy-supplied authenticated analyst identity reduce exposure, but a broader deployment still needs reviewed identity, role, case-level access, audit retention, secret rotation, rate limiting, network policy, and backup/restore procedures.

## Runtime limitations

- The backend uses Python's `ThreadingHTTPServer`; it is appropriate behind a controlled owner deployment boundary, not as an internet-facing high-scale application server.
- Target-development generation has one worker per process. Jobs persist as JSON, but queued/running work is marked failed after a restart; there is no distributed queue, cancellation, or cross-replica locking.
- Browser and proxy document uploads are capped at 25 MiB; graph imports and other API bodies retain the backend's 30 MiB allowance.
- Provider readiness uses a short network reachability probe; a reachable socket does not prove that the requested model is loaded or that a generation will succeed.
- The bearer token protects the backend service but is not row-level or case-level authorisation.
- Model endpoints must provide an OpenAI-compatible chat-completions interface; native provider APIs need an adapter.

## Conditions before operational evaluation

1. Define the decision, mission, scenario, time horizon, layers, relation directions, and permitted scope.
2. Implement assertion-level provenance and source-family lineage.
3. Add an epistemic state machine separating extracted, resolved, hypothesis, endorsed, accepted-operational, superseded, and expired records.
4. Freeze canonical graph snapshots and record every calculation against context, profile, and algorithm versions.
5. Establish chronological and source-family-disjoint train/calibration/test sets.
6. Measure discrimination, calibration, abstention, perturbation stability, and analyst decision utility.
7. Add production identity and authorisation, security monitoring, backup/restore tests, and model/data retention controls.
8. Obtain legal, policy, classification, security, and human-factors assurance; require consequential-action rationale and dual review.
