"""Serve the schema catalogue and hold the analyst's extraction selection.

The selection is the application's answer to "which entities am I interested
in". It is deliberately persistent and separate from any one extraction job: an
analyst narrows the schema once, sees what that leaves in the predicate list and
in the compiled prompt, and every subsequent document is extracted against that
same slice until they change it.

Standard library only, so the selection screen works whether or not the optional
LangExtract toolchain is installed.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import CORE_MODULE_ID, PASSES, SchemaError, Selection, TargetingSchema, load_schema
from .schema_llm import SchemaMissionProvider
from .schema_prompt import PromptCompiler

SELECTION_FILENAME = "extraction_selection.json"

TOPIC_STOPWORDS = {
    "a", "about", "all", "an", "and", "are", "as", "at", "be", "by", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "what", "which", "with",
}


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SchemaService:
    def __init__(self, runtime_dir: str | Path, schema_path: str | Path | None = None):
        self.selection_path = Path(runtime_dir).resolve() / SELECTION_FILENAME
        self._lock = threading.RLock()
        self._schema: TargetingSchema | None = None
        self._error = ""
        try:
            self._schema = load_schema(str(schema_path) if schema_path else None)
        except SchemaError as exc:
            # A missing schema disables schema-guided extraction; it must not
            # stop the review, explore and query workflows from starting.
            self._error = str(exc)
        self._request = self._read_selection()
        self.mission_llm = SchemaMissionProvider()

    # ------------------------------------------------------------------ state

    @property
    def available(self) -> bool:
        return self._schema is not None

    @property
    def schema(self) -> TargetingSchema:
        if self._schema is None:
            raise RuntimeError(f"The targeting schema is not loaded: {self._error}")
        return self._schema

    def status(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "error": self._error}
        resolved = self.resolve()
        return {
            "available": True,
            "title": self.schema.metadata.get("title", ""),
            "version": self.schema.metadata.get("version", ""),
            "profile_id": self.schema.data.get("profile_id", ""),
            # Health is polled; the full id and exclusion lists belong on
            # /api/schema/selection, which is fetched only by the picker.
            "selection": {
                "module_ids": list(resolved.module_ids),
                "counts": resolved.as_dict()["counts"],
                "warnings": list(resolved.warnings),
            },
            "updated_at": self._request.get("updated_at", ""),
        }

    def _read_selection(self) -> dict[str, Any]:
        if not self.selection_path.exists():
            return {}
        try:
            stored = json.loads(self.selection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return stored if isinstance(stored, dict) else {}

    def _write_selection(self, request: dict[str, Any]) -> None:
        self.selection_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.selection_path.with_name(self.selection_path.name + ".tmp")
        temporary.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.selection_path)

    @staticmethod
    def clean_request(body: dict[str, Any]) -> dict[str, Any]:
        """Reduce an arbitrary request body to the three id lists we accept."""
        def ids(name: str) -> list[str]:
            value = body.get(name)
            if not isinstance(value, list):
                return []
            return [
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            ][:2000]

        return {
            "module_ids": ids("module_ids"),
            "entity_type_ids": ids("entity_type_ids"),
            "relationship_type_ids": ids("relationship_type_ids"),
        }

    # ------------------------------------------------------------------- API

    def catalogue(self) -> dict[str, Any]:
        payload = self.schema.catalogue()
        payload["saved_selection"] = {
            key: value for key, value in self._request.items() if key != "updated_at"
        }
        payload["default_selection"] = self.schema.default_selection()
        return payload

    def current_request(self) -> dict[str, Any]:
        with self._lock:
            return {key: list(value) for key, value in self._request.items() if key != "updated_at"}

    def resolve(self, request: dict[str, Any] | None = None) -> Selection:
        source = request if request is not None else self.current_request()
        return self.schema.resolve(source)

    def preview(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Resolve a candidate selection and cost its prompts, without saving.

        This is what makes down-selection a decision rather than a guess: the
        analyst sees the surviving predicates, the ones the closure dropped and
        whether the compiled prompt still fits the model's context.
        """
        selection = self.resolve(request)
        prompts = PromptCompiler(self.schema).compile(selection)
        payload = selection.as_dict()
        payload["prompts"] = {
            pass_id: {
                key: value
                for key, value in prompts[pass_id].as_dict().items()
                if key != "text"
            }
            for pass_id in PASSES
        }
        payload["ready"] = bool(
            selection.entity_type_ids
            and selection.relationship_type_ids
            and not any(
                note.startswith("The compiled")
                for pass_id in PASSES
                for note in prompts[pass_id].notes
            )
        )
        return payload

    def prompt_text(self, pass_id: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
        pass_id = (pass_id or "").strip().upper()
        if pass_id not in PASSES:
            raise ValueError("pass must be ENTITY or RELATIONSHIP")
        compiled = PromptCompiler(self.schema).compile(self.resolve(request))[pass_id]
        return compiled.as_dict()

    def suggest(self, topic: str, *, max_entity_types: int = 24) -> dict[str, Any]:
        """Suggest a reviewable ontology slice from plain topic words.

        This helper is deterministic and lexical: it never sends the ontology,
        topic or case data to a model, and it never saves the selection.  The
        analyst sees every match and the normal prompt preview before applying.
        """
        topic = re.sub(r"\s+", " ", str(topic or "")).strip()
        if len(topic) < 2:
            raise ValueError("Enter at least two characters describing the extraction topic.")
        max_entity_types = max(3, min(int(max_entity_types), 80))
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", topic.casefold())
            if len(token) > 1 and token not in TOPIC_STOPWORDS
        }
        if not tokens:
            raise ValueError("The topic contains no searchable terms.")

        def field_text(record: dict[str, Any], name: str) -> str:
            value = record.get(name, "")
            if isinstance(value, list):
                return " ".join(str(item) for item in value).casefold()
            return str(value).casefold()

        def score(record: dict[str, Any], weights: dict[str, int]) -> tuple[int, list[str]]:
            searchable = {name: field_text(record, name) for name in weights}
            matched: set[str] = set()
            value = 0
            for name, weight in weights.items():
                hits = {token for token in tokens if token in searchable[name]}
                matched.update(hits)
                value += weight * len(hits)
            if topic.casefold() in " ".join(searchable.values()):
                value += 8
            return value, sorted(matched)

        module_rows: list[dict[str, Any]] = []
        module_scores: dict[str, int] = {}
        for module in self.schema.data["modules"]:
            value, matched = score(
                module, {"label": 5, "purpose": 2, "question": 2, "code": 2, "id": 1}
            )
            if value:
                module_scores[module["id"]] = value
                module_rows.append(
                    {"id": module["id"], "label": module.get("label", module["id"]), "score": value, "matched_terms": matched}
                )

        entity_scores: dict[str, int] = {}
        entity_matches: dict[str, set[str]] = {}
        for entity in self.schema.data["entity_types"]:
            if not self.schema.is_extractable(entity["id"]):
                continue
            value, matched = score(
                entity, {"label": 7, "definition": 3, "id": 1, "review_boundary": 1}
            )
            if value:
                entity_scores[entity["id"]] = value
                entity_matches[entity["id"]] = set(matched)

        relationship_rows: list[dict[str, Any]] = []
        for relationship in self.schema.data["relationship_types"]:
            value, matched = score(
                relationship,
                {"label": 6, "definition": 3, "surface_forms": 4, "inverse_reading": 2, "id": 1},
            )
            if not value:
                continue
            relationship_rows.append(
                {"id": relationship["id"], "label": relationship.get("label", relationship["id"]), "score": value, "matched_terms": matched}
            )
            # A matching predicate is useful only if its endpoint classes are
            # present.  Endpoint boosts are therefore small and explicit.
            for type_id in [
                *relationship.get("domain_type_ids", []),
                *relationship.get("range_type_ids", []),
            ]:
                if type_id in self.schema.entities and self.schema.is_extractable(type_id):
                    entity_scores[type_id] = entity_scores.get(type_id, 0) + 2
                    entity_matches.setdefault(type_id, set()).update(matched)

        ranked_entities = sorted(
            entity_scores, key=lambda type_id: (-entity_scores[type_id], type_id)
        )[:max_entity_types]
        entity_rows = [
            {
                "id": type_id,
                "label": self.schema.entities[type_id].get("label", type_id),
                "score": entity_scores[type_id],
                "matched_terms": sorted(entity_matches.get(type_id, set())),
            }
            for type_id in ranked_entities
        ]

        inferred_modules: dict[str, int] = dict(module_scores)
        for type_id in ranked_entities:
            entity = self.schema.entities[type_id]
            for module_id in entity.get("module_ids", []):
                if module_id in self.schema.modules:
                    inferred_modules[module_id] = inferred_modules.get(module_id, 0) + entity_scores[type_id]
        ranked_modules = sorted(
            (module_id for module_id in inferred_modules if module_id != CORE_MODULE_ID),
            key=lambda module_id: (-inferred_modules[module_id], module_id),
        )[:4]
        request = {
            "module_ids": [CORE_MODULE_ID, *ranked_modules],
            "entity_type_ids": ranked_entities,
            "relationship_type_ids": [],
        }
        if not ranked_entities:
            return {
                "query": topic,
                "strategy": "deterministic lexical match",
                "saved": False,
                "suggestion": request,
                "matches": {"modules": [], "entity_types": [], "relationship_types": []},
                "ready": False,
                "warning": "No ontology terms matched. Refine the topic or use the manual picker.",
            }

        preview = self.preview(request)
        resolved_suggestion = {
            "module_ids": list(preview["module_ids"]),
            "entity_type_ids": list(preview["entity_type_ids"]),
            "relationship_type_ids": [],
        }
        module_rows.sort(key=lambda row: (-row["score"], row["id"]))
        relationship_rows.sort(key=lambda row: (-row["score"], row["id"]))
        return {
            "query": topic,
            "strategy": "deterministic lexical match",
            "saved": False,
            "notice": "A lexical suggestion is not an ontology decision. Review the matched terms and prompt preview before saving.",
            "suggestion": resolved_suggestion,
            "matches": {
                "modules": module_rows[:8],
                "entity_types": entity_rows,
                "relationship_types": relationship_rows[:24],
            },
            "preview": preview,
            "ready": preview["ready"],
        }

    def suggest_from_mission(self, mission: str, *, max_entity_types: int = 40) -> dict[str, Any]:
        """Suggest a reviewable ontology slice from a free-text mission statement.

        Unlike `suggest`, this sends the mission statement and a compact
        catalogue of ontology ids, labels and definitions (never case data) to
        the configured mission-narrowing provider. Everything the model
        returns is filtered back down to ids that actually exist in the loaded
        schema; nothing is saved, and the analyst sees the normal prompt
        preview before applying it.
        """
        mission = re.sub(r"\s+", " ", str(mission or "")).strip()
        if len(mission) < 8:
            raise ValueError("Enter a mission statement of at least a few words.")
        if not self.mission_llm.configured:
            raise RuntimeError(
                "The mission-narrowing provider is not configured. Set "
                "PRIMROSE_SCHEMA_LLM_MODEL_ID and PRIMROSE_SCHEMA_LLM_BASE_URL "
                "(or an equivalent PRIMROSE_GEMMA4_* / KG_* endpoint)."
            )
        max_entity_types = max(3, min(int(max_entity_types), 80))

        def trimmed(text: Any, limit: int = 160) -> str:
            return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]

        catalogue = {
            "modules": [
                {
                    "id": module["id"],
                    "label": module.get("label", module["id"]),
                    "definition": trimmed(module.get("purpose", "")),
                }
                for module in self.schema.data["modules"]
                if module["id"] != CORE_MODULE_ID
            ],
            "entity_types": [
                {
                    "id": entity["id"],
                    "label": entity.get("label", entity["id"]),
                    "definition": trimmed(entity.get("definition", "")),
                }
                for entity in self.schema.data["entity_types"]
                if self.schema.is_extractable(entity["id"])
            ],
            "relationship_types": [
                {
                    "id": relationship["id"],
                    "label": relationship.get("label", relationship["id"]),
                    "definition": trimmed(relationship.get("definition", "")),
                }
                for relationship in self.schema.data["relationship_types"]
                if relationship.get("model_action") != "HOST_DERIVED_ONLY"
            ],
        }

        result = self.mission_llm.narrow(mission, catalogue)
        entity_type_ids = [
            type_id
            for type_id in dict.fromkeys(result["entity_type_ids"])
            if self.schema.is_extractable(type_id)
        ][:max_entity_types]
        module_ids = [CORE_MODULE_ID, *[m for m in result["module_ids"] if m != CORE_MODULE_ID]]
        relationship_type_ids = list(dict.fromkeys(result["relationship_type_ids"]))

        if not entity_type_ids:
            return {
                "query": mission,
                "strategy": "llm mission narrowing",
                "saved": False,
                "model_id": result.get("model_id"),
                "rationale": result.get("rationale", ""),
                "suggestion": {
                    "module_ids": [CORE_MODULE_ID],
                    "entity_type_ids": [],
                    "relationship_type_ids": [],
                },
                "matches": {"modules": [], "entity_types": [], "relationship_types": []},
                "ready": False,
                "warning": "The model did not select any entity type from the ontology. "
                "Refine the mission statement or use the manual picker.",
            }

        request = {
            "module_ids": module_ids,
            "entity_type_ids": entity_type_ids,
            "relationship_type_ids": relationship_type_ids,
        }
        preview = self.preview(request)
        resolved_suggestion = {
            "module_ids": list(preview["module_ids"]),
            "entity_type_ids": list(preview["entity_type_ids"]),
            "relationship_type_ids": list(preview["relationship_type_ids"]),
        }
        return {
            "query": mission,
            "strategy": "llm mission narrowing",
            "saved": False,
            "notice": "A model-generated suggestion is not an ontology decision. Review "
            "the rationale and prompt preview before saving.",
            "model_id": result.get("model_id"),
            "rationale": result.get("rationale", ""),
            "suggestion": resolved_suggestion,
            "matches": {
                "modules": [
                    {"id": type_id, "label": self.schema.modules[type_id].get("label", type_id)}
                    for type_id in resolved_suggestion["module_ids"]
                    if type_id in self.schema.modules
                ],
                "entity_types": [
                    {"id": type_id, "label": self.schema.entities[type_id].get("label", type_id)}
                    for type_id in resolved_suggestion["entity_type_ids"]
                    if type_id in self.schema.entities
                ],
                "relationship_types": [
                    {"id": type_id, "label": self.schema.relationships[type_id].get("label", type_id)}
                    for type_id in resolved_suggestion["relationship_type_ids"]
                    if type_id in self.schema.relationships
                ],
            },
            "preview": preview,
            "ready": preview["ready"],
        }

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        request = self.clean_request(body)
        # Resolve before writing: an unusable selection is rejected at the API
        # boundary rather than discovered by a failed extraction job later.
        selection = self.schema.resolve(request)
        if not selection.entity_type_ids:
            raise ValueError(
                "The selection leaves no extractable entity type. Select at least one "
                "module or entity type."
            )
        stored = dict(request, updated_at=_stamp())
        with self._lock:
            self._write_selection(stored)
            self._request = stored
        return self.preview(request)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._request = {}
            if self.selection_path.exists():
                self.selection_path.unlink()
        return self.preview()
