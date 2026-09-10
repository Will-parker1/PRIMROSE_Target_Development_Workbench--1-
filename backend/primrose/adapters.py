"""Status contracts for optional PRIMROSE inference and model adapters."""

from __future__ import annotations

import os
from typing import Any

from .gemma4 import Gemma4Provider


def adapter_statuses() -> list[dict[str, Any]]:
    configured_model = os.getenv("KG_MODEL_ID", "google/gemma-4-e4b")
    gemma4 = Gemma4Provider()
    return [
        {**gemma4.status(), "configured_model": gemma4.model_id or configured_model},
        {
            "id": "hypothesis.ultra",
            "name": "ULTRA candidate-link adapter",
            "status": "unavailable",
            "reason_code": "missing_model_artifact",
            "reason": "No pinned ULTRA artifact, relation mapping or calibration set is supplied.",
            "output_label": "uncalibrated_model_score",
            "candidates": [],
        },
        {
            "id": "hypothesis.anyburl",
            "name": "AnyBURL / AMIE rule adapter",
            "status": "unavailable",
            "reason_code": "missing_rule_engine",
            "reason": "No audited rule engine binary, licence decision or relation export mapping is supplied.",
            "candidates": [],
        },
        {
            "id": "hypothesis.tlogic",
            "name": "TLogic / TILP temporal-rule adapter",
            "status": "unavailable",
            "reason_code": "missing_temporal_event_history",
            "reason": "The static graph lacks the event-time history required for temporal rule learning.",
            "candidates": [],
        },
        {
            "id": "hypothesis.calibration",
            "name": "Relation-specific calibration",
            "status": "unavailable",
            "reason_code": "missing_adjudicated_outcomes",
            "reason": "No chronological, source-family-disjoint adjudication set is supplied.",
            "candidates": [],
        },
        {
            "id": "community.leiden",
            "name": "Leiden community adapter",
            "status": "unavailable",
            "reason_code": "missing_audited_runtime",
            "reason": "No pinned Leiden implementation, resolution policy or completed seed-stability runs are supplied.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "community.role_stability",
            "name": "Community and role stability runner",
            "status": "unavailable",
            "reason_code": "missing_stability_runs",
            "reason": "No documented community seed, bootstrap or source-family perturbation runs are available.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "structure.typed_temporal_motifs",
            "name": "Directed typed temporal motif engine",
            "status": "unavailable",
            "reason_code": "missing_temporal_event_history",
            "reason": "The static projection lacks an event-time sequence and an ontology-approved temporal motif catalogue.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "resilience.capacity_flow",
            "name": "Capacity-aware flow adapter",
            "status": "unavailable",
            "reason_code": "missing_capacity_model",
            "reason": "No compatible capacity units, demands, conservation rules or pinned solver configuration are supplied.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "resilience.fault_tree",
            "name": "Fault-tree adapter",
            "status": "unavailable",
            "reason_code": "missing_fault_tree",
            "reason": "No engineered top event, Boolean gates, base events or probability assumptions are supplied.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "resilience.bayesian_network",
            "name": "Bayesian network adapter",
            "status": "unavailable",
            "reason_code": "missing_bayesian_model",
            "reason": "No validated Bayesian DAG, state definitions or conditional probability tables are supplied.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "resilience.reliability_model",
            "name": "Reliability and system-dynamics adapter",
            "status": "unavailable",
            "reason_code": "missing_reliability_model",
            "reason": "No component failure, repair, delay, recovery or feedback model is supplied.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "collection.expected_value_information",
            "name": "Expected value of information adapter",
            "status": "unavailable",
            "reason_code": "missing_decision_model",
            "reason": "No explicit alternatives, utility model, prior distribution or observation model is supplied.",
            "outputs": [],
            "numerical_weight": 0,
        },
        {
            "id": "retrieval.graphrag",
            "name": "Existing local GraphRAG",
            "status": "configured",
            "reason_code": None,
            "reason": "Existing reasoning boundary is preserved; runtime availability is reported by /api/health.",
            "numerical_weight": 0,
        },
    ]
