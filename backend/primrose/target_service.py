"""Fail-closed web adapter for the existing target-development script."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from target_development import (
    DOCTRINE,
    VERDICT_DEVELOPABLE,
    TargetScreeningError,
    develop,
    screen_entity,
)


class TargetDevelopmentService:
    """Expose dry-run target-development records without changing the core.

    Model generation is intentionally not synchronous here: a basic record can
    make ten sequential provider calls.  This initial adapter assembles the
    deterministic dossier, graph facts and prompts, while stripping raw prompts
    from the normal HTTP response.
    """

    def __init__(self, store: Any):
        self.store = store

    @staticmethod
    def _screening_payload(screening: Any) -> dict[str, Any]:
        return {
            "verdict": screening.verdict,
            "category": screening.category,
            "rationale": screening.rationale,
            "protected_indicators": deepcopy(screening.protected_indicators),
            "dual_use": screening.dual_use,
            "cautions": list(screening.cautions),
            "adapter_allows_preview": screening.verdict == VERDICT_DEVELOPABLE,
        }

    def meta(self) -> dict[str, Any]:
        return {
            "name": "Target development insights",
            "mode": "deterministic-dry-run",
            "model_generation": {
                "status": "disabled",
                "reason": "Use an asynchronous, schema-validated Gemma-4 job after the actual model identifier and endpoint are configured.",
            },
            "handling": "ANALYTICAL AID — REVIEW REQUIRED — NOT AUTHORITY TO ACT",
            "phases": [
                {
                    "id": phase,
                    "title": body["title"],
                    "purpose": body["purpose"],
                    "sections": [
                        {
                            "key": section.key,
                            "title": section.title,
                            "mandatory": section.mandatory,
                        }
                        for section in body["sections"]
                    ],
                }
                for phase, body in DOCTRINE.items()
            ],
            "safety_boundary": [
                "The adapter requires an exact entity id.",
                "Personnel, areas, rejected records and protected/restricted objects fail closed.",
                "No engagement, means selection, legal determination or collateral-damage estimate is produced.",
                "The existing target_development.py dossier and prompt implementation remains authoritative.",
            ],
        }

    def candidates(self, query: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        query = str(query).strip().casefold()
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        rows: list[dict[str, Any]] = []
        for record in self.store.export_graph(include_review=True).get("nodes", []):
            node_id = str(record.get("id", ""))
            if not node_id:
                continue
            item = self.store.item("node", node_id)
            if query and query not in item["label"].casefold() and query not in node_id.casefold():
                continue
            screening = screen_entity(item)
            if screening.verdict == "not-a-target" or screening.category == "Individual":
                continue
            rows.append(
                {
                    "id": node_id,
                    "label": item["label"],
                    "type": item["type"],
                    "review_status": item["status"],
                    "degree": item["degree"],
                    "screening": self._screening_payload(screening),
                }
            )
        rows.sort(key=lambda row: (-row["degree"], row["label"], row["id"]))
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }

    def preview(
        self,
        target_id: str,
        *,
        phase: str = "basic",
        depth: int = 2,
        limit: int = 90,
        include_prompts: bool = False,
    ) -> dict[str, Any]:
        target_id = str(target_id).strip()
        if not target_id:
            raise ValueError("target_id is required")
        if self.store.resolve_node(target_id) != target_id:
            raise ValueError("target_id must be an exact stable entity id")
        if phase not in DOCTRINE:
            raise ValueError(f"phase must be one of: {', '.join(sorted(DOCTRINE))}")
        depth = max(1, min(int(depth), 3))
        limit = max(10, min(int(limit), 150))
        item = self.store.item("node", target_id)
        screening = screen_entity(item)
        if item["status"] == "rejected":
            raise TargetScreeningError("Rejected graph records cannot enter target development.")
        if screening.verdict != VERDICT_DEVELOPABLE:
            raise TargetScreeningError(screening.rationale)

        record = develop(
            self.store,
            target_id,
            phase=phase,
            generator=None,
            depth=depth,
            limit=limit,
            allow_personnel=False,
        )
        record["screening"] = self._screening_payload(screening)
        record["adapter"] = {
            "mode": "deterministic-dry-run",
            "model_called": False,
            "protected_core_changed": True,
            "protected_core_change": "Core screening now fails closed for every restricted or protected verdict, matching this web adapter.",
            "requested_relationship_limit": limit,
            "actual_relationship_count": record["evidence"]["relationships"],
            "limit_note": "The protected core currently treats this as a graph-slice node cap; the actual relationship count is reported explicitly.",
        }
        record["evidence"].pop("block", None)
        for section in record["sections"]:
            if not include_prompts:
                section.pop("prompt", None)
        return record
