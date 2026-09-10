"""The UK/NATO targeting extraction schema, as a runtime registry.

`UK_NATO_Targeting_LangExtract_Gemma_Context_IESv5_v1.0.json` is a
module-selectable reference context, not a prompt. Its own
`runtime_contract.context_loading_rule` forbids serialising the whole file into
every chunk prompt: the host selects the mandatory CORE module plus the relevant
targeting modules, compiles that closed slice into the prompt, and validates
model output back against the same slice.

This module is that selector and validator. It is standard library only, so the
web application can browse the schema and record a selection without the
optional LangExtract toolchain installed. Prompt compilation and the two
extraction passes live in `building_kg/schema_extractor.py`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = ROOT / "UK_NATO_Targeting_LangExtract_Gemma_Context_IESv5_v1.0.json"

# The CORE module carries identity, evidence, resolution and fusion. Every other
# module's terms are meaningless without it, so it is never de-selectable.
CORE_MODULE_ID = "core:IDENTITY_EVIDENCE_RESOLUTION_FUSION"

# Types outside the profile's own hierarchy. They may appear as a declared
# domain, range or parent, and are resolvable for subtype checks, but the model
# is never asked to emit them as an entity type.
EXTERNAL_TYPE_IDS = frozenset(
    {
        "owl:Thing",
        "prov:Activity",
        "prov:Entity",
        "rdf:Property",
        "rdfs:Resource",
        "skos:Concept",
        "skos:ConceptScheme",
    }
)

# The host may only ask the model for predicates it is allowed to answer.
# HOST_DERIVED_ONLY predicates are derived by the host from validated qualified
# records; requesting one from the model is a contract breach, not a bad answer.
REJECTED_MODEL_ACTION = "HOST_DERIVED_ONLY"

# model_action -> the review_action the model is permitted to claim.
# Mirrors host_semantic_validation.checks_in_order, step 8.
PERMITTED_REVIEW_ACTIONS = {
    "EXTRACT_EXPLICIT_ONLY": ("EXTRACT_AS_REPORTED",),
    "EXTRACT_EXPLICIT_OR_PROPOSE_FOR_REVIEW": ("EXTRACT_AS_REPORTED", "PROPOSE_FOR_REVIEW"),
    "EXTRACT_EXPLICIT_HUMAN_AUTHORITATIVE_RECORD_ONLY": ("HUMAN_REVIEW_REQUIRED",),
}

# The sentinel an extraction may use when a mention is genuinely untypeable
# against the selected registry. Anything else off-registry is an error.
UNRESOLVED = "UNRESOLVED"

ENTITY_PASS = "ENTITY"
RELATIONSHIP_PASS = "RELATIONSHIP"
PASSES = (ENTITY_PASS, RELATIONSHIP_PASS)


class SchemaError(ValueError):
    """The schema document is missing or structurally unusable."""


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


@dataclass(frozen=True)
class Selection:
    """A resolved, reference-closed slice of the schema.

    `entity_type_ids` is exactly what the model may emit: abstract types and
    types the schema forbids as model output are already gone. Domain and range
    checks walk the parent chain separately, so a supertype can license a
    predicate without ever becoming an extractable type itself.
    """

    module_ids: tuple[str, ...]
    entity_type_ids: tuple[str, ...]
    relationship_type_ids: tuple[str, ...]
    value_set_ids: tuple[str, ...]
    dropped_relationships: tuple[dict[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_ids": list(self.module_ids),
            "entity_type_ids": list(self.entity_type_ids),
            "relationship_type_ids": list(self.relationship_type_ids),
            "value_set_ids": list(self.value_set_ids),
            "dropped_relationships": [dict(row) for row in self.dropped_relationships],
            "warnings": list(self.warnings),
            "counts": {
                "modules": len(self.module_ids),
                "entity_types": len(self.entity_type_ids),
                "relationship_types": len(self.relationship_type_ids),
                "value_sets": len(self.value_set_ids),
            },
        }


@dataclass
class ValidationResult:
    """The outcome of validating one extraction record against the selection."""

    ok: bool
    record: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    review_action: str = ""

    def __bool__(self) -> bool:  # `if result:` reads better at call sites
        return self.ok


class TargetingSchema:
    def __init__(self, data: dict[str, Any], path: Path | None = None):
        for key in ("entity_types", "relationship_types", "modules"):
            if not isinstance(data.get(key), list) or not data[key]:
                raise SchemaError(f"schema is missing a usable {key!r} list")
        self.path = path
        self.data = data
        self.entities: dict[str, dict[str, Any]] = {e["id"]: e for e in data["entity_types"]}
        self.relationships: dict[str, dict[str, Any]] = {
            r["id"]: r for r in data["relationship_types"]
        }
        self.modules: dict[str, dict[str, Any]] = {m["id"]: m for m in data["modules"]}
        self.value_sets: dict[str, dict[str, Any]] = {
            v["id"]: v for v in data.get("value_sets", [])
        }
        self.prompt_description: str = _normalise_text(data.get("prompt_description", ""))
        self.global_policies: dict[str, Any] = data.get("global_policies", {})
        self.distinction_rules: list[dict[str, Any]] = data.get("distinction_rules", [])
        self.few_shot_examples: list[dict[str, Any]] = data.get("few_shot_examples", [])
        self.no_extraction_examples: list[dict[str, Any]] = data.get(
            "no_extraction_examples", []
        )
        self.graph_projection_contract: dict[str, Any] = data.get(
            "graph_projection_contract", {}
        )
        self.namespaces: dict[str, str] = data.get("namespaces", {})
        self.scope: dict[str, Any] = data.get("scope", {})
        self.metadata: dict[str, Any] = data.get("metadata", {})

        # Attribute vocabularies come from the embedded JSON Schema rather than
        # being restated here, so a schema revision cannot leave the validator
        # enforcing last version's enums.
        self._enums = self._read_enums()

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TargetingSchema":
        path = Path(path) if path else DEFAULT_SCHEMA_PATH
        if not path.is_file():
            raise SchemaError(f"schema document not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SchemaError(f"schema document is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SchemaError("schema document must be a JSON object")
        return cls(data, path)

    def _read_enums(self) -> dict[str, tuple[str, ...]]:
        enums: dict[str, tuple[str, ...]] = {}
        for branch in self.data.get("host_validation_schema", {}).get("allOf", []):
            properties = (
                branch.get("then", {})
                .get("properties", {})
                .get("attributes", {})
                .get("properties", {})
            )
            for name, spec in properties.items():
                if isinstance(spec, dict) and isinstance(spec.get("enum"), list):
                    enums.setdefault(name, tuple(spec["enum"]))
        return enums

    def enum(self, name: str) -> tuple[str, ...]:
        return self._enums.get(name, ())

    def required_attributes(self, pass_id: str) -> tuple[str, ...]:
        wanted = "entity" if pass_id == ENTITY_PASS else "relationship"
        for branch in self.data.get("host_validation_schema", {}).get("allOf", []):
            const = (
                branch.get("if", {})
                .get("properties", {})
                .get("extraction_class", {})
                .get("const")
            )
            if const == wanted:
                attributes = branch.get("then", {}).get("properties", {}).get("attributes", {})
                return tuple(attributes.get("required", []))
        return ()

    def permitted_attributes(self, pass_id: str) -> frozenset[str]:
        wanted = "entity" if pass_id == ENTITY_PASS else "relationship"
        for branch in self.data.get("host_validation_schema", {}).get("allOf", []):
            const = (
                branch.get("if", {})
                .get("properties", {})
                .get("extraction_class", {})
                .get("const")
            )
            if const == wanted:
                attributes = branch.get("then", {}).get("properties", {}).get("attributes", {})
                return frozenset(attributes.get("properties", {}))
        return frozenset()

    # ------------------------------------------------------------- hierarchy

    @lru_cache(maxsize=None)
    def ancestors(self, type_id: str) -> tuple[str, ...]:
        """The type itself followed by its parent chain, nearest first."""
        chain: list[str] = []
        seen: set[str] = set()
        cursor: str | None = type_id
        while cursor and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            entity = self.entities.get(cursor)
            cursor = entity.get("parent_id") if entity else None
        return tuple(chain)

    def satisfies(self, type_id: str, declared: Iterable[str]) -> bool:
        """Does `type_id` satisfy a declared domain or range list?"""
        declared = set(declared)
        if not declared:
            return True
        return bool(declared.intersection(self.ancestors(type_id)))

    def is_extractable(self, type_id: str) -> bool:
        entity = self.entities.get(type_id)
        if entity is None:
            return False
        if entity.get("abstract") is True:
            return False
        return entity.get("model_output_allowed") is not False

    # ------------------------------------------------------------- catalogue

    def catalogue(self) -> dict[str, Any]:
        """Everything the selection UI needs, in one payload."""
        module_rows = []
        for module in self.data["modules"]:
            module_rows.append(
                {
                    "id": module["id"],
                    "code": module.get("code", ""),
                    "label": module.get("label", module["id"]),
                    "purpose": module.get("purpose", ""),
                    "question": module.get("question", ""),
                    "entity_type_ids": list(module.get("entity_type_ids", [])),
                    "relationship_type_ids": list(module.get("relationship_type_ids", [])),
                    "mandatory": module["id"] == CORE_MODULE_ID,
                    "default_selected": module.get("default_runtime_selection", True) is not False
                    or module["id"] == CORE_MODULE_ID,
                }
            )

        entity_rows = []
        for entity in self.data["entity_types"]:
            entity_rows.append(
                {
                    "id": entity["id"],
                    "label": entity.get("label", entity["id"]),
                    "definition": entity.get("definition", ""),
                    "layer": entity.get("layer", ""),
                    "parent_id": entity.get("parent_id", ""),
                    "module_ids": list(entity.get("module_ids", [])),
                    "aliases": list(entity.get("aliases", [])),
                    "tier": entity.get("tier", ""),
                    "extractable": self.is_extractable(entity["id"]),
                    "sme_review_required": bool(entity.get("sme_review_required")),
                    "review_boundary": entity.get("review_boundary", ""),
                }
            )

        relationship_rows = []
        for relationship in self.data["relationship_types"]:
            relationship_rows.append(
                {
                    "id": relationship["id"],
                    "label": relationship.get("label", relationship["id"]),
                    "definition": relationship.get("definition", ""),
                    "domain_type_ids": list(relationship.get("domain_type_ids", [])),
                    "range_type_ids": list(relationship.get("range_type_ids", [])),
                    "range_value_set_id": relationship.get("range_value_set_id", ""),
                    "model_action": relationship.get("model_action", ""),
                    "module_ids": list(relationship.get("module_ids", [])),
                    "surface_forms": list(relationship.get("surface_forms", [])),
                    "temporal_requirement": relationship.get("temporal_requirement", ""),
                    "model_facing": relationship.get("model_action") != REJECTED_MODEL_ACTION,
                }
            )

        return {
            "metadata": dict(self.metadata),
            "schema_version": self.data.get("schema_version", ""),
            "profile_id": self.data.get("profile_id", ""),
            "source": self.path.name if self.path else "",
            "scope": dict(self.scope),
            "core_module_id": CORE_MODULE_ID,
            "modules": module_rows,
            "entity_types": entity_rows,
            "relationship_types": relationship_rows,
            "value_sets": [
                {
                    "id": value_set["id"],
                    "label": value_set.get("label", value_set["id"]),
                    "closed": bool(value_set.get("closed")),
                    "values": [
                        {"id": v["id"], "label": v.get("label", v["id"])}
                        for v in value_set.get("values", [])
                    ],
                }
                for value_set in self.data.get("value_sets", [])
            ],
            "model_action_values": dict(self.data.get("model_action_values", {})),
            "review_actions": list(self.enum("review_action")),
        }

    # -------------------------------------------------------------- selection

    def default_selection(self) -> dict[str, Any]:
        """Every module the schema marks as a runtime default; no type filter."""
        return {
            "module_ids": [
                module["id"]
                for module in self.data["modules"]
                if module.get("default_runtime_selection", True) is not False
            ],
            "entity_type_ids": [],
            "relationship_type_ids": [],
        }

    def resolve(self, request: dict[str, Any] | None = None) -> Selection:
        """Turn an analyst's down-selection into a reference-closed slice.

        Empty `entity_type_ids` means "every extractable type in the selected
        modules". A non-empty list is a genuine narrowing: it is what makes a
        4B model's entity pass tractable, and it drives which predicates remain
        askable, because a predicate with no selected type at one end can only
        produce an edge pointing at something the analyst asked not to extract.
        """
        request = request or {}
        warnings: list[str] = []

        module_ids = self._resolve_modules(request.get("module_ids"), warnings)
        module_entity_ids: list[str] = []
        module_relationship_ids: list[str] = []
        module_value_set_ids: list[str] = []
        for module_id in module_ids:
            module = self.modules[module_id]
            module_entity_ids.extend(module.get("entity_type_ids", []))
            module_relationship_ids.extend(module.get("relationship_type_ids", []))
            module_value_set_ids.extend(module.get("value_set_ids", []))

        available_entities = [
            type_id
            for type_id in dict.fromkeys(module_entity_ids)
            if type_id in self.entities and self.is_extractable(type_id)
        ]
        entity_ids = self._narrow(
            available_entities,
            request.get("entity_type_ids"),
            kind="entity type",
            warnings=warnings,
        )
        if not entity_ids:
            warnings.append(
                "No extractable entity types remain in the selection; both passes "
                "would return nothing."
            )

        available_relationships = [
            type_id
            for type_id in dict.fromkeys(module_relationship_ids)
            if type_id in self.relationships
        ]
        requested_relationships = self._narrow(
            available_relationships,
            request.get("relationship_type_ids"),
            kind="relationship type",
            warnings=warnings,
        )

        selected_entities = set(entity_ids)
        value_set_ids: set[str] = set()
        relationship_ids: list[str] = []
        dropped: list[dict[str, str]] = []
        for type_id in requested_relationships:
            relationship = self.relationships[type_id]
            if relationship.get("model_action") == REJECTED_MODEL_ACTION:
                dropped.append(
                    {
                        "id": type_id,
                        "label": relationship.get("label", type_id),
                        "reason": "HOST_DERIVED_ONLY: the host derives this from validated records.",
                    }
                )
                continue
            if not self._endpoint_covered(relationship.get("domain_type_ids", []), selected_entities):
                dropped.append(
                    {
                        "id": type_id,
                        "label": relationship.get("label", type_id),
                        "reason": "No selected entity type satisfies its domain.",
                    }
                )
                continue
            value_set_id = relationship.get("range_value_set_id", "")
            if value_set_id:
                if value_set_id not in self.value_sets:
                    dropped.append(
                        {
                            "id": type_id,
                            "label": relationship.get("label", type_id),
                            "reason": f"Declared value set {value_set_id} is not in the schema.",
                        }
                    )
                    continue
                value_set_ids.add(value_set_id)
            elif not self._endpoint_covered(
                relationship.get("range_type_ids", []), selected_entities
            ):
                dropped.append(
                    {
                        "id": type_id,
                        "label": relationship.get("label", type_id),
                        "reason": "No selected entity type satisfies its range.",
                    }
                )
                continue
            relationship_ids.append(type_id)

        value_set_ids.update(
            value_set_id
            for value_set_id in module_value_set_ids
            if value_set_id in self.value_sets
        )

        if not relationship_ids:
            warnings.append(
                "No predicate survives the selection; the relationship pass will be skipped."
            )

        return Selection(
            module_ids=tuple(module_ids),
            entity_type_ids=tuple(entity_ids),
            relationship_type_ids=tuple(relationship_ids),
            value_set_ids=tuple(sorted(value_set_ids)),
            dropped_relationships=tuple(dropped),
            warnings=tuple(warnings),
        )

    def _resolve_modules(self, requested: Any, warnings: list[str]) -> list[str]:
        if not requested:
            module_ids = list(self.default_selection()["module_ids"])
        else:
            module_ids = []
            for module_id in requested:
                if module_id in self.modules:
                    module_ids.append(module_id)
                else:
                    warnings.append(f"Unknown module {module_id!r} was ignored.")
        if CORE_MODULE_ID not in module_ids:
            # context_loading_rule: the mandatory CORE module plus the relevant
            # targeting modules. Selecting TP3 alone would leave evidence,
            # assertion and mention types undefined.
            module_ids.insert(0, CORE_MODULE_ID)
        ordered = [module["id"] for module in self.data["modules"] if module["id"] in set(module_ids)]
        return ordered

    def _narrow(
        self,
        available: list[str],
        requested: Any,
        *,
        kind: str,
        warnings: list[str],
    ) -> list[str]:
        if not requested:
            return list(available)
        available_set = set(available)
        kept = [type_id for type_id in requested if type_id in available_set]
        unknown = [type_id for type_id in requested if type_id not in available_set]
        if unknown:
            warnings.append(
                f"{len(unknown)} requested {kind}(s) are not available in the selected "
                f"modules and were ignored: {', '.join(sorted(unknown)[:5])}"
                + ("…" if len(unknown) > 5 else "")
            )
        # Preserve schema order rather than request order so prompts are stable
        # between runs with the same set chosen in a different sequence.
        kept_set = set(kept)
        return [type_id for type_id in available if type_id in kept_set]

    def _endpoint_covered(self, declared: list[str], selected: set[str]) -> bool:
        """Is at least one selected entity type inside a declared domain/range?"""
        if not declared:
            return True
        declared_set = set(declared)
        if declared_set & EXTERNAL_TYPE_IDS:
            return True
        return any(declared_set.intersection(self.ancestors(type_id)) for type_id in selected)

    # ----------------------------------------------------------------- repair

    def repair_identifier(self, value: str, selection: Selection, kind: str) -> str:
        """Fix a canonical id whose local name is right but whose prefix is not.

        A small model reliably reproduces `providesInputTo` and then guesses
        `tgt_rel:` for a term that lives in `core_rel:`. Because the local name
        resolves to exactly one id in the selected registry, this is identifier
        normalisation rather than a semantic guess, and it recovers extractions
        that are otherwise correct in every field.

        Nothing else is repaired. Controlled values such as polarity and
        modality carry meaning, so a near-miss there stays a rejection.
        """
        value = (value or "").strip()
        if not value or value == UNRESOLVED:
            return value
        pool = (
            selection.entity_type_ids
            if kind == "entity"
            else selection.relationship_type_ids
        )
        if value in pool or value in EXTERNAL_TYPE_IDS:
            return value
        local = value.rsplit(":", 1)[-1].casefold()
        matches = [
            candidate for candidate in pool if candidate.rsplit(":", 1)[-1].casefold() == local
        ]
        return matches[0] if len(matches) == 1 else value

    def repair_extraction(
        self, record: dict[str, Any], selection: Selection, pass_id: str
    ) -> list[str]:
        """Apply identifier repairs in place; return a note per change made."""
        attributes = record.get("attributes")
        if not isinstance(attributes, dict):
            return []
        fields = (
            (("type_id", "entity"),)
            if pass_id == ENTITY_PASS
            else (
                ("subject_type_id", "entity"),
                ("object_type_id", "entity"),
                ("predicate_id", "relationship"),
            )
        )
        notes: list[str] = []
        for name, kind in fields:
            original = str(attributes.get(name, "") or "")
            repaired = self.repair_identifier(original, selection, kind)
            if repaired and repaired != original:
                attributes[name] = repaired
                notes.append(f"{name} {original!r} normalised to {repaired!r}")
        return notes

    # -------------------------------------------------------------- validate

    def validate_extraction(
        self, record: dict[str, Any], selection: Selection, pass_id: str
    ) -> ValidationResult:
        """Structural and registry validation of one post-resolver record.

        This covers host_semantic_validation steps that can be checked without
        the source text. Span grounding (step 1) and evidence-text occurrence
        (step 6) need the passage and are enforced by the extractor.
        """
        errors: list[str] = []
        if not isinstance(record, dict):
            return ValidationResult(False, {}, ["extraction record must be an object"])

        expected_class = "entity" if pass_id == ENTITY_PASS else "relationship"
        extraction_class = str(record.get("extraction_class", "")).strip()
        if extraction_class != expected_class:
            errors.append(
                f"extraction_class {extraction_class!r} is not valid for the {pass_id} pass"
            )
        extraction_text = str(record.get("extraction_text", "") or "")
        if not extraction_text.strip():
            errors.append("extraction_text is empty")

        raw_attributes = record.get("attributes")
        attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
        if not isinstance(raw_attributes, dict):
            errors.append("attributes must be an object")

        permitted = self.permitted_attributes(pass_id)
        unexpected = sorted(set(attributes) - permitted)
        for name in unexpected:
            attributes.pop(name)
        if unexpected:
            errors.append("dropped attribute(s) not in the schema: " + ", ".join(unexpected))

        validator = self._validate_entity if pass_id == ENTITY_PASS else self._validate_relationship
        review_action = validator(attributes, selection, errors)

        for name in self.required_attributes(pass_id):
            if not str(attributes.get(name, "") or "").strip():
                errors.append(f"missing required attribute {name!r}")

        clean = {
            "extraction_class": expected_class,
            "extraction_text": extraction_text,
            "attributes": attributes,
        }
        return ValidationResult(not errors, clean, errors, review_action)

    def _validate_entity(
        self, attributes: dict[str, Any], selection: Selection, errors: list[str]
    ) -> str:
        type_id = str(attributes.get("type_id", "") or "").strip()
        if type_id and type_id != UNRESOLVED:
            if type_id not in selection.entity_type_ids:
                errors.append(
                    f"type_id {type_id!r} is not in the selected entity registry"
                )
            elif not self.is_extractable(type_id):
                errors.append(f"type_id {type_id!r} is abstract or not a permitted model output")
        attributes["type_id"] = type_id

        review_action = str(attributes.get("review_action", "") or "").strip().upper()
        permitted = self.enum("review_action")
        if permitted and review_action not in permitted:
            errors.append(f"review_action {review_action!r} is not a permitted value")
        elif type_id == UNRESOLVED and review_action == "EXTRACT_AS_REPORTED":
            # An untypeable mention is exactly the case a human must look at.
            review_action = "HUMAN_REVIEW_REQUIRED"
        entity = self.entities.get(type_id)
        if entity and entity.get("sme_review_required") and review_action != "HUMAN_REVIEW_REQUIRED":
            review_action = "HUMAN_REVIEW_REQUIRED"
        attributes["review_action"] = review_action
        return review_action

    def _validate_relationship(
        self, attributes: dict[str, Any], selection: Selection, errors: list[str]
    ) -> str:
        for name in ("polarity", "certainty", "modality", "source_direction", "review_action"):
            value = str(attributes.get(name, "") or "").strip().upper()
            permitted = self.enum(name)
            if value and permitted and value not in permitted:
                errors.append(f"{name} {value!r} is not a permitted value")
            attributes[name] = value

        predicate_id = str(attributes.get("predicate_id", "") or "").strip()
        relationship = self.relationships.get(predicate_id)
        if not relationship:
            errors.append(f"predicate_id {predicate_id!r} is not in the schema")
            return str(attributes.get("review_action", ""))
        if relationship.get("model_action") == REJECTED_MODEL_ACTION:
            errors.append(f"predicate_id {predicate_id!r} is HOST_DERIVED_ONLY and must not be extracted")
            return str(attributes.get("review_action", ""))
        if predicate_id not in selection.relationship_type_ids:
            errors.append(f"predicate_id {predicate_id!r} is outside the selected slice")

        subject_type = str(attributes.get("subject_type_id", "") or "").strip()
        object_type = str(attributes.get("object_type_id", "") or "").strip()
        self._check_endpoint(
            subject_type, relationship.get("domain_type_ids", []), selection, "subject", errors
        )

        value_set_id = relationship.get("range_value_set_id", "")
        if value_set_id:
            self._check_controlled_value(attributes, value_set_id, object_type, errors)
        else:
            self._check_endpoint(
                object_type, relationship.get("range_type_ids", []), selection, "object", errors
            )

        # model_action decides what the model is allowed to claim about review.
        review_action = str(attributes.get("review_action", "") or "").strip().upper()
        permitted_actions = PERMITTED_REVIEW_ACTIONS.get(relationship.get("model_action", ""), ())
        if permitted_actions and review_action not in permitted_actions:
            # Escalating is always safe; downgrading is what the rule guards
            # against, so an out-of-contract claim is raised, never accepted.
            corrected = permitted_actions[-1] if len(permitted_actions) == 1 else "PROPOSE_FOR_REVIEW"
            errors.append(
                f"review_action {review_action!r} is not permitted for model_action "
                f"{relationship.get('model_action')!r}; expected one of "
                f"{', '.join(permitted_actions)}"
            )
            review_action = corrected
        attributes["review_action"] = review_action
        return review_action

    def _check_endpoint(
        self,
        type_id: str,
        declared: list[str],
        selection: Selection,
        role: str,
        errors: list[str],
    ) -> None:
        if not type_id or type_id == UNRESOLVED:
            return
        declared_set = set(declared)
        if type_id in EXTERNAL_TYPE_IDS:
            # host_semantic_validation: external RDF/RDFS/SKOS/PROV/TIME/IES
            # types are permitted only where the predicate itself declares them.
            if type_id not in declared_set:
                errors.append(
                    f"{role}_type_id {type_id!r} is an external type this predicate "
                    f"does not declare"
                )
            return
        if type_id not in selection.entity_type_ids:
            errors.append(f"{role}_type_id {type_id!r} is not in the selected entity registry")
            return
        if declared_set and not self.satisfies(type_id, declared_set):
            errors.append(
                f"{role}_type_id {type_id!r} does not satisfy the declared "
                f"{'domain' if role == 'subject' else 'range'} "
                f"({', '.join(declared)})"
            )

    def _check_controlled_value(
        self,
        attributes: dict[str, Any],
        value_set_id: str,
        object_type: str,
        errors: list[str],
    ) -> None:
        value_set = self.value_sets.get(value_set_id, {})
        if object_type and object_type != "skos:Concept":
            errors.append(
                f"object_type_id must be skos:Concept for a controlled-value predicate, "
                f"not {object_type!r}"
            )
        attributes["object_type_id"] = "skos:Concept"
        declared_set = str(attributes.get("value_set_id", "") or "").strip()
        if declared_set and declared_set != value_set_id:
            errors.append(
                f"value_set_id {declared_set!r} does not match the predicate's "
                f"declared set {value_set_id!r}"
            )
        attributes["value_set_id"] = value_set_id
        value_id = str(attributes.get("value_id", "") or "").strip()
        known = {value["id"] for value in value_set.get("values", [])}
        if value_set.get("closed"):
            if not value_id:
                errors.append(f"value_id is required for the closed scheme {value_set_id}")
            elif value_id not in known:
                errors.append(f"value_id {value_id!r} is not a member of {value_set_id}")
        elif value_id and value_id not in known:
            # controlled_value_open_scheme_rule: never mint an id for an open
            # scheme. Keep the source wording and force normalisation by a human.
            attributes.pop("value_id", None)
            attributes["review_action"] = "HUMAN_REVIEW_REQUIRED"


@lru_cache(maxsize=4)
def load_schema(path: str | None = None) -> TargetingSchema:
    """Process-wide cached load. The schema document is ~0.5 MB of JSON."""
    return TargetingSchema.load(path)
