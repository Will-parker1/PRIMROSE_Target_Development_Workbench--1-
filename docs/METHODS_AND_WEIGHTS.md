# Methods and weighting profiles

## Why there is no single priority score

Mission consequence, evidence confidence, uncertainty, contradiction, collection value, and model novelty answer different questions. Adding them into one number can make weak evidence appear stronger, reward contradiction as importance, and allow model novelty to alter a consequential ranking. PRIMROSE therefore keeps five profiles and five workflow queues separate.

These weights are reviewable starting hypotheses, not validated production thresholds. Every profile sums to 1.00. A missing required contributor blocks that profile's score; remaining weights are shown for diagnosis but are not rescaled. Evidence quality is a gate/parallel assessment, not a bonus added to mission consequence.

## Recommended starting profiles

### Operational criticality

| Component | Weight |
|---|---:|
| Mission consequence | 30% |
| Functional dependency | 25% |
| Non-substitutability / recovery | 15% |
| Temporal urgency | 10% |
| Cross-layer dependency | 10% |
| Structural diagnostic component | 10% |

The structural component is mission-seeded PageRank 30%, bridge/participation 25%, alternate-path loss 25%, local reach 10%, and community-role stability 10%. GraphRAG, Gemma, ULTRA, novelty, evidence volume, contradiction severity, and collection gaps have **zero operational-criticality weight**.

Rationale: consequence and functional dependency dominate because topology alone cannot establish mission effect. Non-substitutability is material but held below those direct mission terms. Time and cross-layer context each receive modest weight. Structure is capped at 10% and itself requires stability/perturbation inputs so a graph collection artefact cannot dominate the result.

### Evidential confidence

| Component | Weight |
|---|---:|
| Independent corroboration | 25% |
| Extraction and entity-resolution certainty | 20% |
| Source reliability | 15% |
| Information credibility | 15% |
| Temporal validity | 15% |
| Provenance completeness | 10% |

Rationale: independence is weighted highest because repeated reporting from one lineage is not corroboration. Extraction/resolution is next because a well-sourced claim attached to the wrong entity remains unusable. Reliability, credibility, and timeliness are equally necessary dimensions; provenance completeness is important but cannot substitute for their quality.

### Collection priority

| Component | Weight |
|---|---:|
| Decision sensitivity | 30% |
| Likelihood the answer changes the assessment | 25% |
| Consequence of unresolved uncertainty | 20% |
| Temporal urgency | 15% |
| Collection feasibility | 10% |

Rationale: a collection question should first be capable of changing a decision or assessment. Consequence and urgency then bound why the uncertainty matters. Feasibility receives weight but cannot make an irrelevant question valuable. Missing information alone never creates collection priority; a concrete decision-relevant question is required.

### Hypothesis-review priority

| Component | Weight |
|---|---:|
| Mission consequence if true | 30% |
| Calibrated plausibility | 20% |
| Independent generator agreement | 20% |
| Expected information gain | 15% |
| Temporal urgency | 10% |
| Collection feasibility | 5% |

Rationale: this is a review-order profile, not a truth or action score. Conditional consequence leads, while calibrated plausibility and lineage-distinct method agreement are kept separate. Information gain and urgency influence review sequencing. Feasibility is deliberately small so easy-to-check but immaterial candidates do not crowd out consequential ones. ULTRA raw output remains an `uncalibrated_model_score`, not a probability.

### Contradiction-review priority

| Component | Weight |
|---|---:|
| Consequence while unresolved | 30% |
| Contradiction severity | 25% |
| Source-lineage independence | 20% |
| Temporal urgency | 15% |
| Resolution likelihood | 10% |

Rationale: unresolved decision consequence leads; severity and independent origin determine whether the conflict is material rather than duplicated reporting. Urgency affects sequencing, while ease of resolution is last so convenient low-impact contradictions do not dominate.

## Registry coverage

The application exposes 111 methods. Every definition includes the analytical question, definition, calculation, formula and raw LaTeX, required inputs, graph layers, assumptions, limitations, normalisation, output units, version, reference, scoring role, and—when an object is selected—its calculation trace or unavailability reason.

| Family | Count | Incorporated methods |
|---|---:|---|
| Mission | 2 | Mission consequence; functional dependency |
| Multilayer | 1 | Cross-layer dependency |
| Classical graph | 5 | Typed weighted degree; local reach; PageRank; mission-seeded PageRank; harmonic proximity |
| Communities and bridges | 5 | Participation coefficient; articulation points; bridge/participation; Leiden; community-role stability |
| Resilience | 4 | Non-substitutability; alternate-path loss; robustness loss; redundancy |
| Evidence | 6 | Provenance completeness; independent corroboration; extraction/resolution certainty; source reliability; information credibility; temporal validity |
| Collection | 5 | Decision sensitivity; assessment-change likelihood; unresolved consequence; feasibility; expected information gain |
| Contradiction | 4 | Severity; unresolved consequence; lineage independence; resolution likelihood |
| Temporal | 3 | Urgency; collection-normalised activity change; change-point detection |
| Hypothesis | 4 | Consequence-if-true; ULTRA; AnyBURL/AMIE; TLogic/TILP |
| Hypothesis assurance | 3 | Calibrated plausibility; independent generator agreement; relation-specific calibration |
| Composite | 1 | Structural diagnostic component |
| Retrieval | 1 | Existing GraphRAG retrieval/synthesis |
| Model synthesis | 1 | Optional Gemma explanation synthesis |

## Applicability today

- Standard-library projection, typed degree/reach, PageRank, harmonic proximity, articulation diagnostics, and snapshot hashing run against the live graph.
- Provenance, mission, temporal, resilience, multilayer, stability, calibration, and many composite values remain unavailable when the graph lacks their required inputs.
- Leiden needs pinned `igraph`/`leidenalg`, explicit seed, and resolution.
- ULTRA needs a pinned model artifact, entity/relation mapping, isolated candidate store, and relation-specific evaluation/calibration.
- AnyBURL/AMIE needs a reviewed engine/licence, pinned binary digest, and relation export mapping.
- TLogic/TILP needs event-time history, relation mapping, a fixed horizon, and an evaluated artifact.
- Change-point/activity metrics require observation/transaction time and collection-opportunity history; `valid_from` alone is insufficient.
- GraphRAG and Gemma may be technically available while remaining analytically weightless.

## How to change weights responsibly

1. Define one decision context and outcome before changing a profile.
2. Keep evidence, consequence, collection, contradiction, and hypothesis questions separate.
3. Use historical, adjudicated, source-family-disjoint data; do not tune against the same graph used for evaluation.
4. Compare calibration, ranking stability, abstention, and analyst utility—not just apparent face validity.
5. Version the profile and its context, record sensitivity to reasonable alternatives, and preserve the previous profile.
6. Do not activate a missing contributor by assigning a guessed default. Keep it unavailable until the required data and validation exist.
