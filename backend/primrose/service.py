"""Read-only orchestration service for the PRIMROSE workbench API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from target_development import screen_entity

from .adapters import adapter_statuses
from .analytics import (
    CRITICALITY_MAP,
    articulation_points,
    build_projection,
    node_observations,
    pagerank,
    score_result,
)
from .gemma4 import Gemma4Provider
from .profiles import PROFILES, profile_catalogue
from .registry import method_catalogue, method_definition
from .target_service import TargetDevelopmentService
from .target_jobs import TargetDevelopmentJobs


class PrimroseWorkbench:
    """Additive analyst-workbench facade over the existing graph store."""

    CONTEXT_ID = "live-analyst-review-v1"

    def __init__(self, store: Any):
        self.store = store
        self.target_development = TargetDevelopmentService(store)
        self.target_jobs = TargetDevelopmentJobs(store, store.state_path.parent)
        self.gemma4 = Gemma4Provider()

    def target_development_meta(self) -> dict[str, Any]:
        payload = self.target_development.meta()
        payload["model_generation"] = self.target_jobs.status()
        return payload

    def _projection(self):
        return build_projection(
            self.store.export_graph(include_review=True),
            statuses=("accepted", "unreviewed"),
        )

    def context(self, snapshot_id: str) -> dict[str, Any]:
        summary = self.store.summary()
        graph_name = summary.get("graph_name") or "Loaded knowledge graph"
        return {
            "id": self.CONTEXT_ID,
            "version": "1.0.0",
            "name": f"{graph_name} — analyst review",
            "mission": "Prioritise evidence review, contradiction resolution and collection questions for the loaded graph",
            "analytical_question": "Which recorded objects warrant evidence review, collection or monitoring?",
            "scenario": "Current loaded graph snapshot",
            "desired_effect_or_purpose": "Improve evidence quality and surface structural diagnostics without authorising action",
            "seed_objects": [],
            "time_horizon": None,
            "graph_snapshot_id": snapshot_id,
            "data_cutoff": summary.get("updated_at"),
            "layers": sorted(set(self._configured_layers())),
            "relation_types": [row["name"] for row in self.store.summary().get("relationship_types", [])],
            "assumptions": [
                "Candidate records remain unreviewed and cannot become accepted facts through analytics or model output.",
                "Structural metrics are not causal effects or operational importance.",
                "Act means an analyst review action, never engagement or means selection.",
            ],
            "weight_profile_id": "operational-criticality-v1",
            "projection_kind": "candidate_diagnostic",
            "scope": "ANALYST DECISION SUPPORT / NOT AUTHORITY TO ACT",
            "operational_scoring_enabled": False,
        }

    @staticmethod
    def _configured_layers() -> tuple[str, ...]:
        return (
            "physical", "command", "communications", "logistics", "financial",
            "cyber", "influence", "organisational", "production", "transportation",
        )

    def methods(self) -> dict[str, Any]:
        return {"items": method_catalogue(), "total": len(method_catalogue())}

    def method(self, identifier: str) -> dict[str, Any]:
        return method_definition(identifier)

    def profiles(self) -> dict[str, Any]:
        return {"items": profile_catalogue(), "total": len(PROFILES)}

    def adapters(self) -> dict[str, Any]:
        items = adapter_statuses()
        return {"items": items, "total": len(items)}

    @staticmethod
    def _observation(observations: list[dict[str, Any]], method_id: str) -> dict[str, Any]:
        return next(
            (item for item in observations if item["method_id"] == method_id),
            {
                "method_id": method_id,
                "applicability": "unavailable",
                "raw_value": None,
                "normalised_value": None,
                "band": None,
                "reason": "Metric was not calculated for this object.",
                "trace": [],
            },
        )

    def _calculation_bundle(self):
        projection = self._projection()
        rank = pagerank(projection)
        articulation = articulation_points(projection)
        return projection, rank, articulation

    def _item(
        self,
        projection: Any,
        rank: dict[str, float],
        articulation: set[str],
        node_id: str,
    ) -> dict[str, Any]:
        item = self.store.item("node", node_id)
        observations = node_observations(
            projection,
            node_id,
            page_rank=rank,
            articulation=articulation,
        )
        screening = screen_entity(item)
        mission = self._observation(observations, "mission.consequence")
        evidence = score_result(PROFILES["evidential-confidence-v1"], observations)
        operational = score_result(PROFILES["operational-criticality-v1"], observations)

        state = "extracted_assertion" if item["status"] == "unreviewed" else "resolved_assertion"
        gates = [
            {"id": "analyst_support_scope", "passed": True, "reason": "Routes identify analyst workflow actions only and confer no operational authority."},
            {"id": "accepted_operational_state", "passed": False, "reason": f"Current state is {state}; accepted operational assertion is not implemented in the legacy review model."},
            {"id": "minimum_provenance", "passed": False, "reason": "Source lineage and required assertion-level provenance are absent."},
            {"id": "rank_stability", "passed": False, "reason": "No perturbation-based stability run is available."},
            {"id": "fatal_contradiction", "passed": True, "reason": "No fatal contradiction record is attached; this is not proof of agreement."},
        ]

        route = "monitor"
        recommended_action = "Monitor and improve provenance before any consequential assessment."
        route_trace = ["Act gates do not pass; no operational score is emitted."]
        if (
            mission.get("applicability") == "available"
            and float(mission.get("normalised_value") or 0.0) >= 0.75
            and evidence["status"] == "unavailable"
        ):
            route = "collect"
            recommended_action = f"Establish source lineage, independent corroboration and temporal validity for {item['label']}."
            route_trace = [
                "Recorded mission-consequence metadata is high.",
                "Evidence gates are unavailable rather than low.",
                "A concrete evidence-collection question is attached.",
            ]

        return {
            "kind": "node",
            "id": node_id,
            "label": item["label"],
            "type": item["type"],
            "review_status": item["status"],
            "epistemic_state": state,
            "degree": len(projection.incident.get(node_id, ())),
            "route": route,
            "route_label": route.title(),
            "route_score": None,
            "recommended_action": recommended_action,
            "route_trace": route_trace,
            "gates": gates,
            "screening": {
                "verdict": screening.verdict,
                "category": screening.category,
                "rationale": screening.rationale,
                "protected_indicators": screening.protected_indicators,
                "dual_use": screening.dual_use,
                "cautions": screening.cautions,
            },
            "metrics": observations,
            "scores": {
                "operational_criticality": operational,
                "evidential_confidence": evidence,
                "collection_priority": score_result(PROFILES["collection-priority-v1"], observations),
                "hypothesis_review_priority": score_result(PROFILES["hypothesis-review-v1"], observations),
                "contradiction_review_priority": score_result(PROFILES["contradiction-review-v1"], observations),
            },
            "summary_metrics": {
                method_id: self._observation(observations, method_id)
                for method_id in (
                    "mission.consequence",
                    "centrality.pagerank",
                    "structure.bridge_participation",
                    "resilience.alternate_path_loss",
                    "multilayer.cross_layer_dependency",
                    "evidence.independent_corroboration",
                    "contradiction.severity",
                    "temporal.activity_change",
                )
            },
        }

    def queues(self, limit: int = 50) -> dict[str, Any]:
        projection, rank, articulation = self._calculation_bundle()
        limit = max(1, min(int(limit), 100))
        candidates = sorted(
            projection.node_ids,
            key=lambda node_id: (
                -CRITICALITY_MAP.get(
                    str(projection.nodes[node_id].get("criticality", "")).upper(), -1.0
                ),
                -len(projection.incident[node_id]),
                str(projection.nodes[node_id].get("label", node_id)),
                node_id,
            ),
        )[:limit]
        queue_map: dict[str, list[dict[str, Any]]] = {
            "act": [], "collect": [], "challenge": [], "hypotheses": [], "monitor": []
        }
        for node_id in candidates:
            result = self._item(projection, rank, articulation, node_id)
            queue_map[result["route"]].append(result)
        for values in queue_map.values():
            values.sort(
                key=lambda row: (
                    -float(row["summary_metrics"]["mission.consequence"].get("normalised_value") or -1),
                    -row["degree"],
                    row["label"],
                    row["id"],
                )
            )
        return {
            "context": self.context(projection.snapshot_id),
            "queues": queue_map,
            "counts": {key: len(value) for key, value in queue_map.items()},
            "truncated_to": limit,
            "notice": "Queues are deterministic routes over the live graph snapshot. Missing values remain unavailable and model synthesis has zero numerical weight.",
        }

    def object_detail(self, kind: str, identifier: str) -> dict[str, Any]:
        if kind != "node":
            raise ValueError("This vertical slice currently exposes node diagnostics only.")
        projection, rank, articulation = self._calculation_bundle()
        if identifier not in projection.nodes:
            raise KeyError(identifier)
        result = self._item(projection, rank, articulation, identifier)
        base = self.store.item("node", identifier)
        result.update(
            {
                "graph_snapshot_id": projection.snapshot_id,
                "decision_context_id": self.CONTEXT_ID,
                "evidence": {
                    "supporting": [
                        {
                            "id": document,
                            "title": document,
                            "passage": base.get("evidence", ""),
                            "lineage": "unavailable",
                        }
                        for document in base.get("source_documents", [])
                    ],
                    "disconfirming": [],
                    "source_independence": "unavailable",
                    "notice": "No source document is treated as independent without a recorded lineage.",
                },
                "attributes": base.get("metadata", {}),
                "neighbours": base.get("neighbours", []),
                "target_development": {
                    "available": result["screening"]["verdict"] == "developable" and base["status"] != "rejected",
                    "mode": "deterministic-dry-run",
                    "reason": result["screening"]["rationale"],
                },
                "explanation": {
                    "origin": "deterministic-fallback",
                    "model_used": False,
                    "text": " ".join(result["route_trace"] + [result["recommended_action"]]),
                    "unresolved_inference": "Operational consequence, evidence confidence and rank stability remain unavailable until their required inputs exist.",
                },
            }
        )
        return result

    def explain(self, identifier: str, question: str) -> dict[str, Any]:
        question = str(question).strip()
        if not question:
            raise ValueError("question is required")
        detail = self.object_detail("node", str(identifier).strip())
        if not self.gemma4.configured:
            fallback = detail["explanation"]
            return {
                "answer": fallback["text"],
                "retrieved_evidence": [],
                "deterministic_calculation": detail["route_trace"],
                "model_synthesis": "No model synthesis was generated.",
                "unresolved_inference": [fallback["unresolved_inference"]],
                "citations": [],
                "origin": "deterministic-fallback",
                "model_id": None,
                "can_change_scores": False,
                "can_change_graph": False,
            }
        return self.gemma4.explain(detail, question)

    def graph(self, focus: str = "", depth: int = 1, limit: int = 60) -> dict[str, Any]:
        projection = self._projection()
        window = self.store.graph_slice(
            focus=focus,
            depth=max(1, min(int(depth), 3)),
            limit=max(10, min(int(limit), 120)),
            statuses="accepted,unreviewed",
        )
        visible_ids = {node["id"] for node in window["nodes"] if node["id"] in projection.nodes}
        return {
            "focus_id": window.get("focus_id"),
            "snapshot_id": projection.snapshot_id,
            "projection_kind": "candidate_diagnostic",
            "nodes": [
                {
                    **node,
                    "degree": len(projection.incident.get(node["id"], ())),
                    "layer_count": len(
                        {
                            link.get("relation") for link in projection.incident.get(node["id"], ())
                        }
                    ),
                }
                for node in window["nodes"]
                if node["id"] in visible_ids
            ],
            "links": [
                link for link in window["links"]
                if link["source"] in visible_ids and link["target"] in visible_ids
            ],
            "legend": {
                "accepted": "solid cyan",
                "candidate": "solid green",
                "hypothesis": "dashed violet",
                "contradiction": "solid red",
                "collection_gap": "dotted amber",
            },
        }

    def bootstrap(self) -> dict[str, Any]:
        queue_payload = self.queues(limit=36)
        selected = (
            (queue_payload["queues"]["collect"] or queue_payload["queues"]["monitor"] or [None])[0]
        )
        selected_id = selected["id"] if selected else ""
        return {
            "application": "PRIMROSE",
            "subtitle": "Evidence-first graph analysis",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "handling": "ANALYST DECISION SUPPORT — REVIEW REQUIRED — NOT AUTHORITY TO ACT",
            "summary": self.store.summary(),
            **queue_payload,
            "selected_object": self.object_detail("node", selected_id) if selected_id else None,
            "graph": self.graph(selected_id, depth=1, limit=38) if selected_id else self.graph(),
            "adapters": self.adapters(),
            "target_development": self.target_development_meta(),
            "method_count": len(method_catalogue()),
            "profile_count": len(PROFILES),
        }
