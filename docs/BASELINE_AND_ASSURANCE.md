# Baseline, tests, and assurance findings

## Verified baseline

Before the additive implementation, the supplied backend discovered 83 tests: 62 passed and 21 were skipped because optional NetworkX/LangChain dependencies were not installed. The default graph contained 360 nodes and 936 directed edges. It was configured for `gemma-3-4b-it`, not Gemma-4. No ULTRA, AnyBURL/AMIE, TLogic/TILP, Leiden, or calibration artifact was supplied.

## Final verification

| Check | Result |
|---|---|
| Python compilation | Passed |
| Python unit/integration tests | 114 discovered; 93 executed and passed; 21 optional skips |
| Frontend lint | Passed |
| Production Vinext build and artifact validation | Passed |
| Frontend/API tests | 5 passed |
| Browser smoke test | Queue, graph, method drawer, dossier, and responsive shell rendered without application console errors |

New tests cover registry completeness, exact profile sums, excluded score contributors, null/unavailable semantics, prediction/rejection/duplicate projection filtering, order-independent snapshots, PageRank, personalised PageRank, articulation, score non-renormalisation, adapter status, Gemma structured citations, deterministic explanation fallback, analytics non-mutation, queue routing, exact-ID target preview, prompt stripping, restricted-object blocking, import trust stripping, review invalidation on changed merges, topic-to-schema suggestions, asynchronous target-job persistence and HTTP contracts, and authenticated GET/POST API access.

## Remediated integrity findings

The following inherited defects now have direct core regression tests rather than being hidden behind the new interface.

1. **Import trust bypass — fixed:** untrusted graph imports strip `host_derived`, `review_status`, and `review_reason` from nodes and relationships. Only the in-process extraction adapter can preserve host-derived provenance scaffolding, and even that path cannot import review decisions.
2. **Review transfer on merge — fixed:** when a merge changes an existing node payload, its prior accepted or rejected decision is removed and a `review_invalidated` audit event returns it to review. An identical merge retains the decision.
3. **Protected-screening mismatch — fixed:** `target_development.develop()`, the command-line entry point, the deterministic web adapter, and asynchronous target jobs all treat every restricted or otherwise non-developable screening verdict as a hard stop. The deprecated personnel flag cannot override it.
4. **Backend service authentication — added:** when `PRIMROSE_BACKEND_TOKEN` is configured, every `/api` GET and POST requires the matching bearer credential. This is constant-time shared-service authentication for the trusted web proxy; it is not end-user authorisation or workspace isolation.

## Open defects and assurance limits

1. **Cross-document mention collision:** mention IDs omit document/span identity, so same-text/type mentions can collapse across documents.
2. **Prompt budget is advisory:** the schema preview can report an oversized prompt, yet submission can still proceed.
3. **Degree inconsistency:** legacy `graph_slice()` degree may count rejected adjacency even when rejected links are hidden. The new UI replaces it with projection degree.
4. **Dossier limit mismatch:** the supplied `limit` bounds nodes, not relationships, so returned relationships can exceed it.
5. **Definition provenance flag:** the core always reports definitions as paraphrases, including after an authoritative overlay.
6. **Shared-deployment controls remain incomplete:** bearer service authentication does not provide end-user identity, role/compartment authorisation, rate limiting, workspace isolation, audit signing, or a complete request-timeout policy.
7. **Prompt-delimiter injection risk:** imported labels/evidence can contain prompt-like closing delimiters; the original generation path has no post-generation factual validator.

## Recommended remediation order

1. Add document/span identity to mention IDs and define a migration/deduplication policy.
2. Enforce prompt budgets at job submission.
3. Introduce assertion-level provenance, source-lineage independence, epistemic states, and append-only graph snapshots.
4. Add end-user identity, role/compartment authorisation, workspace isolation, rate limiting, and audit signing before any shared deployment.
5. Add post-generation factual validation and structured evidence entailment checks for generated target-development narratives.
