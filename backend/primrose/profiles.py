"""Versioned, non-overlapping PRIMROSE scoring profiles.

Weights are starting profiles from the supplied design brief.  Missing values
remain unavailable; they are never silently replaced with zero or reweighted.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PROFILES: dict[str, dict[str, Any]] = {
    "operational-criticality-v1": {
        "id": "operational-criticality-v1",
        "name": "Operational criticality",
        "version": "1.0.0",
        "queue": "act",
        "description": "Mission-conditioned consequence and dependency; evidence is a gate, not a bonus.",
        "weights": {
            "mission.consequence": 0.30,
            "functional.dependency": 0.25,
            "resilience.non_substitutability": 0.15,
            "temporal.urgency": 0.10,
            "multilayer.cross_layer_dependency": 0.10,
            "structural.component": 0.10,
        },
        "structural_weights": {
            "centrality.personalized_pagerank": 0.30,
            "structure.bridge_participation": 0.25,
            "resilience.alternate_path_loss": 0.25,
            "centrality.local_reach": 0.10,
            "community.role_stability": 0.10,
        },
        "excludes": [
            "retrieval.graphrag",
            "hypothesis.ultra",
            "hypothesis.novelty",
            "evidence.volume",
            "contradiction.severity",
            "collection.gap",
        ],
    },
    "evidential-confidence-v1": {
        "id": "evidential-confidence-v1",
        "name": "Evidential confidence",
        "version": "1.0.0",
        "queue": "evidence",
        "description": "Quality, independence and temporal validity of the evidence supporting an assessment.",
        "weights": {
            "evidence.independent_corroboration": 0.25,
            "evidence.extraction_resolution_certainty": 0.20,
            "evidence.source_reliability": 0.15,
            "evidence.information_credibility": 0.15,
            "evidence.temporal_validity": 0.15,
            "evidence.provenance_completeness": 0.10,
        },
    },
    "collection-priority-v1": {
        "id": "collection-priority-v1",
        "name": "Collection priority",
        "version": "1.0.0",
        "queue": "collect",
        "description": "How valuable and timely resolving an identified uncertainty is likely to be.",
        "weights": {
            "collection.decision_sensitivity": 0.30,
            "collection.assessment_change_likelihood": 0.25,
            "collection.unresolved_consequence": 0.20,
            "temporal.urgency": 0.15,
            "collection.feasibility": 0.10,
        },
    },
    "hypothesis-review-v1": {
        "id": "hypothesis-review-v1",
        "name": "Hypothesis review priority",
        "version": "1.0.0",
        "queue": "hypotheses",
        "description": "Review order for model-only candidate relationships; never an operational score.",
        "weights": {
            "hypothesis.mission_consequence_if_true": 0.30,
            "hypothesis.calibrated_plausibility": 0.20,
            "hypothesis.independent_agreement": 0.20,
            "collection.expected_information_gain": 0.15,
            "temporal.urgency": 0.10,
            "collection.feasibility": 0.05,
        },
    },
    "contradiction-review-v1": {
        "id": "contradiction-review-v1",
        "name": "Contradiction review priority",
        "version": "1.0.0",
        "queue": "challenge",
        "description": "Review order for material conflicts between independent source lineages.",
        "weights": {
            "contradiction.unresolved_consequence": 0.30,
            "contradiction.severity": 0.25,
            "contradiction.lineage_independence": 0.20,
            "temporal.urgency": 0.15,
            "contradiction.resolution_likelihood": 0.10,
        },
    },
}


def profile_catalogue() -> list[dict[str, Any]]:
    """Return profiles as safe copies for API clients."""

    return [deepcopy(PROFILES[key]) for key in sorted(PROFILES)]

