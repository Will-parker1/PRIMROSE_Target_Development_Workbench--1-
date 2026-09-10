"""Compile a selected schema slice into the two LangExtract pass prompts.

`runtime_contract` requires the ENTITY and RELATIONSHIP passes to be separate
`lx.extract()` calls with separately filtered examples, and requires the
compiled module slice to fit inside the deployed model's context with output
headroom. Both rules are enforced here rather than in the extractor, so the
application can preview and budget a prompt before any model is running.

Standard library only.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Sequence

from .schema import (
    ENTITY_PASS,
    PERMITTED_REVIEW_ACTIONS,
    RELATIONSHIP_PASS,
    Selection,
    TargetingSchema,
    UNRESOLVED,
)

# Gemma-class tokenisers land near four characters per token on this kind of
# structured English. It is an estimate for a budget warning, never a substitute
# for the real pre-tokenisation the runtime contract demands before a run.
CHARS_PER_TOKEN = 4

# Above this, a term list stops being a vocabulary and becomes noise a small
# model will sample from at random. Definitions are dropped first, then the
# builder refuses to pretend the selection is workable.
DEFAULT_PROMPT_BUDGET = 24_000


@dataclass
class CompiledPrompt:
    pass_id: str
    text: str
    detail: str  # "full" | "compact" — whether definitions survived the budget
    term_count: int
    notes: list[str] = field(default_factory=list)
    # The ids this prompt actually enumerates. Empty for a whole-selection
    # preview compiled by `entity_prompt`/`relationship_prompt` directly;
    # populated by `PromptCompiler.plan` for a shard, so a shard's few-shot
    # examples and audit trail can be filtered to what it actually lists.
    type_ids: tuple[str, ...] = ()

    @property
    def characters(self) -> int:
        return len(self.text)

    @property
    def estimated_tokens(self) -> int:
        return (len(self.text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass": self.pass_id,
            "text": self.text,
            "detail": self.detail,
            "term_count": self.term_count,
            "characters": self.characters,
            "estimated_tokens": self.estimated_tokens,
            "notes": list(self.notes),
            "type_ids": list(self.type_ids),
        }


@dataclass
class ShardPlan:
    """A pass split into one or more budget-bounded `lx.extract()` prompts.

    `shards` always covers every id in the selection that survived relevance
    filtering — nothing is silently dropped for being over budget the way a
    single compiled prompt's "compact" fallback can still overflow. Ids
    dropped by `skip_zero_relevance` land in `skipped_type_ids` instead, with
    `skipped_reason` explaining why.
    """

    pass_id: str
    shards: list[CompiledPrompt]
    skipped_type_ids: tuple[str, ...] = ()
    skipped_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass": self.pass_id,
            "shard_count": len(self.shards),
            "shards": [shard.as_dict() for shard in self.shards],
            "skipped_type_ids": list(self.skipped_type_ids),
            "skipped_reason": self.skipped_reason,
        }


def _sentence(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _relevance(text: str, terms: Sequence[str]) -> int:
    """Case-insensitive substring hit count of `terms` inside `text`.

    Deliberately a plain substring scan, not an embedding lookup: this module
    is standard-library only, and the schema already curates good terms for
    this — every relationship type carries `surface_forms`, and most entity
    types carry `aliases`.
    """
    haystack = text.casefold()
    return sum(haystack.count(term.casefold()) for term in terms if term)


def score_entities(schema: TargetingSchema, selection: Selection, text: str) -> dict[str, int]:
    """Relevance score per selected entity type: hits of its label/aliases in `text`."""
    scores: dict[str, int] = {}
    for type_id in selection.entity_type_ids:
        entity = schema.entities.get(type_id, {})
        terms = [entity.get("label", "")] + list(entity.get("aliases") or [])
        scores[type_id] = _relevance(text, terms)
    return scores


def score_relationships(schema: TargetingSchema, selection: Selection, text: str) -> dict[str, int]:
    """Relevance score per selected predicate: hits of its label/surface forms in `text`."""
    scores: dict[str, int] = {}
    for type_id in selection.relationship_type_ids:
        relationship = schema.relationships.get(type_id, {})
        terms = [relationship.get("label", "")] + list(relationship.get("surface_forms") or [])
        scores[type_id] = _relevance(text, terms)
    return scores


class PromptCompiler:
    def __init__(self, schema: TargetingSchema, budget: int = DEFAULT_PROMPT_BUDGET):
        self.schema = schema
        self.budget = budget

    # ------------------------------------------------------------------ shared

    def _preamble(self, selection: Selection, pass_id: str) -> list[str]:
        modules = ", ".join(
            f"{self.schema.modules[m].get('code', m)} ({self.schema.modules[m].get('label', m)})"
            for m in selection.module_ids
        )
        lines = [
            self.schema.prompt_description,
            "",
            f"ACTIVE PASS: {pass_id}. Emit only extraction_class "
            f'"{"entity" if pass_id == ENTITY_PASS else "relationship"}". '
            "Ignore every concept that is not listed below.",
            f"SELECTED MODULES: {modules}.",
        ]
        prohibited = self.schema.scope.get("prohibited_uses", [])
        if prohibited:
            lines += ["", "NEVER DO ANY OF THE FOLLOWING:"]
            lines += [f"  - {item}" for item in prohibited]
        return lines

    def _distinctions(self, selection: Selection) -> list[str]:
        """Distinction rules whose wording touches a selected term's label.

        The full list is 17 rules about terms the analyst may not have selected;
        an irrelevant "do not confuse X with Y" costs context and invites the
        model to look for X.
        """
        labels = {
            self.schema.entities[type_id].get("label", "").casefold()
            for type_id in selection.entity_type_ids
        }
        labels.discard("")
        kept = []
        for rule in self.schema.distinction_rules:
            left = str(rule.get("left", "")).casefold()
            right = str(rule.get("right", "")).casefold()
            if left in labels or right in labels:
                kept.append(f"  - {rule.get('left')} vs {rule.get('right')}: {rule.get('rule')}")
        if not kept:
            return []
        return ["", "KEEP THESE APART:"] + kept

    # ------------------------------------------------------------------ entity

    def entity_prompt(self, selection: Selection) -> CompiledPrompt:
        notes: list[str] = []
        detail = "full"
        body = self._entity_body(selection, detail)
        head = self._entity_head(selection)
        tail = self._entity_tail(selection)
        text = "\n".join(head + body + tail)
        if len(text) > self.budget:
            detail = "compact"
            body = self._entity_body(selection, detail)
            text = "\n".join(head + body + tail)
            notes.append(
                "Type definitions were dropped to fit the prompt budget. Narrow the "
                "entity selection to restore them."
            )
        if len(text) > self.budget:
            notes.append(
                f"The compiled entity prompt is {len(text):,} characters, over the "
                f"{self.budget:,} character budget. Select fewer entity types or fewer "
                f"modules before running extraction."
            )
        return CompiledPrompt(
            ENTITY_PASS, text, detail, len(selection.entity_type_ids), notes
        )

    def _entity_head(self, selection: Selection) -> list[str]:
        lines = self._preamble(selection, ENTITY_PASS)
        lines += [
            "",
            "For every entity mention, set these attributes:",
            "  type_id       (required) one of the exact ids listed below, or "
            f"{UNRESOLVED} when the mention is clearly in scope but no listed type fits.",
            "  review_action (required) EXTRACT_AS_REPORTED for a plainly stated mention; "
            "PROPOSE_FOR_REVIEW when the type is a reading rather than the source's own "
            "wording; HUMAN_REVIEW_REQUIRED for any authority, approval, legal, "
            "protection, restriction or list record, and for " + UNRESOLVED + ".",
            "  canonical_name (optional) the fuller form of the name if the source gives one.",
            "  identifier     (optional) a designator or reference number stated in the source.",
            "  mention_role   (optional) the role the mention plays in the sentence.",
            "",
            "extraction_text must be the exact contiguous mention span, not the sentence "
            "around it. Do not resolve two mentions into one entity; resolution is a "
            "separate reviewed decision.",
            "",
            "ENTITY TYPES:",
        ]
        return lines

    def _entity_body(self, selection: Selection, detail: str) -> list[str]:
        lines = []
        for type_id in selection.entity_type_ids:
            entity = self.schema.entities[type_id]
            label = entity.get("label", type_id)
            aliases = entity.get("aliases") or []
            suffix = f" [also: {', '.join(aliases)}]" if aliases else ""
            if detail == "full":
                lines.append(
                    f"  {type_id} = {label}{suffix} — {_sentence(entity.get('definition', ''))}"
                )
            else:
                lines.append(f"  {type_id} = {label}{suffix}")
        return lines

    def _entity_tail(self, selection: Selection) -> list[str]:
        return self._distinctions(selection)

    # ------------------------------------------------------------ relationship

    def relationship_prompt(self, selection: Selection) -> CompiledPrompt:
        notes: list[str] = []
        detail = "full"
        head = self._relationship_head(selection)
        tail = self._relationship_tail(selection)
        body = self._relationship_body(selection, detail)
        text = "\n".join(head + body + tail)
        if len(text) > self.budget:
            detail = "compact"
            body = self._relationship_body(selection, detail)
            text = "\n".join(head + body + tail)
            notes.append(
                "Predicate definitions and surface forms were trimmed to fit the prompt "
                "budget. Narrow the module or entity selection to restore them."
            )
        if len(text) > self.budget:
            notes.append(
                f"The compiled relationship prompt is {len(text):,} characters, over the "
                f"{self.budget:,} character budget. A small model will not hold this many "
                f"predicates apart; narrow the selection before running extraction."
            )
        return CompiledPrompt(
            RELATIONSHIP_PASS, text, detail, len(selection.relationship_type_ids), notes
        )

    def _relationship_head(self, selection: Selection) -> list[str]:
        lines = self._preamble(selection, RELATIONSHIP_PASS)
        lines += [
            "",
            "For every stated relationship, set these attributes:",
            "  subject_text, object_text   (required) exact spans from the source.",
            "  subject_type_id, object_type_id (required) exact entity type ids from the list below.",
            "  predicate_id                (required) an exact id from the PREDICATES list. "
            "Never invent one; if none fits, extract nothing.",
            "  polarity                    (required) "
            + " | ".join(self.schema.enum("polarity")),
            "  certainty                   (required) "
            + " | ".join(self.schema.enum("certainty")),
            "  modality                    (required) "
            + " | ".join(self.schema.enum("modality")),
            "  source_direction            (required) CANONICAL when the source states it "
            "subject-first; INVERSE_NORMALISED when the source uses the inverse wording and "
            "you have flipped it into the canonical direction.",
            "  review_action               (required) as permitted by the predicate's action, below.",
            "",
            "Optional, only when the source states them, copied verbatim: evidence_text, "
            "valid_time_text, observation_time_text, assessor_or_authority_text, "
            "uncertainty_text, analytical_confidence_text, finding_text, "
            "qualitative_value_text, quantitative_value_text, unit_text, qualifier_text, "
            "baseline_or_scenario_text, record_type_id, value_set_id, value_id. "
            "List any qualifier the predicate needs but the source omits in "
            "missing_required_qualifiers. Never invent one to make a record look complete.",
            "",
            "extraction_text must be the exact contiguous span that states the link, "
            "copied character-for-character from the source. Do not paraphrase, "
            "summarise, change tense or wording, or add words that are not in the "
            "source, even if the meaning would stay the same: if the source says "
            "\"recorded at 03:16\" you must write \"recorded at 03:16\", not "
            "\"occurred at 03:16\" or any other rewording. A span you have edited in "
            "any way will be rejected.",
            "",
            "PREDICATES (stored subject -> object in the direction shown):",
        ]
        return lines

    def _relationship_body(self, selection: Selection, detail: str) -> list[str]:
        lines = []
        for type_id in selection.relationship_type_ids:
            relationship = self.schema.relationships[type_id]
            label = relationship.get("label", type_id)
            domain = ", ".join(relationship.get("domain_type_ids", [])) or "any"
            value_set_id = relationship.get("range_value_set_id", "")
            if value_set_id:
                range_text = f"skos:Concept from {value_set_id}"
            else:
                range_text = ", ".join(relationship.get("range_type_ids", [])) or "any"
            actions = PERMITTED_REVIEW_ACTIONS.get(relationship.get("model_action", ""), ())
            lines.append(f"  {type_id} = {label}")
            lines.append(f"      {domain} -> {range_text}")
            if actions:
                lines.append(f"      review_action: {' | '.join(actions)}")
            if detail == "full":
                definition = _sentence(relationship.get("definition", ""), 180)
                if definition:
                    lines.append(f"      meaning: {definition}")
                surface = relationship.get("surface_forms") or []
                if surface:
                    lines.append(f"      stated as: {'; '.join(surface[:4])}")
                inverse = relationship.get("inverse_reading")
                if inverse:
                    lines.append(
                        f"      inverse wording \"{inverse}\" — flip it and set "
                        f"source_direction INVERSE_NORMALISED"
                    )
                temporal = relationship.get("temporal_requirement")
                if temporal:
                    lines.append(f"      time: {temporal}")
        return lines

    def _relationship_tail(self, selection: Selection) -> list[str]:
        lines: list[str] = []
        if selection.value_set_ids:
            lines += ["", "CONTROLLED VALUES (set object_type_id skos:Concept, value_set_id and value_id):"]
            for value_set_id in selection.value_set_ids:
                value_set = self.schema.value_sets.get(value_set_id, {})
                closed = value_set.get("closed")
                values = value_set.get("values", [])
                lines.append(
                    f"  {value_set_id} = {value_set.get('label', value_set_id)}"
                    + ("" if closed else "  [OPEN: keep the source wording, do not invent a value_id]")
                )
                if closed:
                    for value in values:
                        lines.append(f"      {value['id']} = {value.get('label', value['id'])}")

        lines += ["", "ENTITY TYPE IDS for subject_type_id and object_type_id:"]
        for type_id in selection.entity_type_ids:
            lines.append(f"  {type_id} = {self.schema.entities[type_id].get('label', type_id)}")

        lines += self._distinctions(selection)

        negatives = [
            example
            for example in self.schema.no_extraction_examples
            if example.get("pass") == RELATIONSHIP_PASS
        ]
        if negatives:
            # negative_example_rule: these are prompt guardrails, not ExampleData,
            # because zero-extraction examples were not stable in LangExtract 1.6.0.
            lines += ["", "EXTRACT NOTHING FROM WORDING LIKE THIS:"]
            for example in negatives:
                lines.append(f"  \"{example.get('text', '')}\" — {example.get('reason', '')}")
        return lines

    # ------------------------------------------------------------- sharding

    def plan(
        self,
        selection: Selection,
        pass_id: str,
        text: str = "",
        *,
        skip_zero_relevance: bool = False,
    ) -> ShardPlan:
        """Split a pass into one or more prompts that each fit the budget.

        `entity_prompt`/`relationship_prompt` compile the *whole* selection
        into one prompt and, past a point, can only fall back to a "compact"
        detail level that still overflows the budget for a broad selection.
        `plan` instead packs the selection's ids into as many budget-bounded
        prompts as needed, each compiled at the best detail level that fits on
        its own. When `text` is given, ids with no lexical trace of their
        surface forms in `text` are ordered first-to-last by relevance, so the
        model sees the best-evidenced ids in shard 1.

        `skip_zero_relevance` defaults to False: relevance scoring here is a
        plain substring match against the schema's curated `surface_forms`,
        which real prose paraphrases constantly (measured live: a document
        that plainly states a dependency, an operator and a maintainer
        relationship still scored zero substring hits against 168 of 169
        selected predicates, because none of it was phrased exactly like the
        schema's canonical wording). A closed-vocabulary model asked to pick
        from a list is far better at recognising a paraphrase than this
        pre-filter is, so gating the model out entirely on a zero score costs
        real recall for a cost saving that isn't needed once shards already
        fit the budget. Set it to True only when the caller has independently
        decided the recall/cost trade-off is worth it (e.g. very large batch
        runs where most of a broad selection is known to be off-topic).
        Passing `text=""` (a preview with no document yet) disables scoring
        entirely regardless of this flag: every candidate is kept and packed
        in its declared order.
        """
        if pass_id == ENTITY_PASS:
            return self._plan_entities(selection, text, skip_zero_relevance)
        if pass_id == RELATIONSHIP_PASS:
            return self._plan_relationships(selection, text, skip_zero_relevance)
        raise ValueError(f"unknown pass id {pass_id!r}")

    @staticmethod
    def _order_and_filter(
        candidate_ids: Sequence[str],
        scores: dict[str, int] | None,
        skip_zero_relevance: bool,
    ) -> tuple[list[str], list[str]]:
        if scores is None:
            return list(candidate_ids), []
        kept: list[str] = []
        skipped: list[str] = []
        for type_id in candidate_ids:
            if skip_zero_relevance and scores.get(type_id, 0) == 0:
                skipped.append(type_id)
            else:
                kept.append(type_id)
        order = {type_id: index for index, type_id in enumerate(candidate_ids)}
        kept.sort(key=lambda type_id: (-scores.get(type_id, 0), order[type_id]))
        return kept, skipped

    def _pack_shards(
        self,
        ordered_ids: Sequence[str],
        build_selection,
        compile_fn,
    ) -> list[tuple[tuple[str, ...], CompiledPrompt]]:
        """Greedily fill shards up to `self.budget`, closing a shard only once
        it holds at least one id — a single id that overflows the budget on
        its own still becomes its own (over-budget) shard rather than being
        silently dropped; `compile_fn`'s own full/compact fallback and notes
        already flag that case.

        A shard is also closed as soon as the next id would force it from
        "full" to "compact" detail, even though compact would still fit the
        character budget. Measured live against the real model: with the
        same document and the same predicate offered, "full" detail (keeping
        each predicate's definition, surface forms and inverse-reading hint)
        extracted the relationship correctly; "compact" detail (bare
        `id = label`, `domain -> range` lines only, for ~70-90 predicates in
        one prompt) produced nothing. This trades more, smaller model calls
        for the wording hints a small local model actually needs to bridge a
        document's plain prose to the schema's predicate ids — worth it even
        though it costs several more calls per pass (measured: 3 shards for
        a 3-module selection, 7 for the broadest possible one, all at full
        detail, versus 1-3 shards that fall back to compact today).
        """
        shards: list[tuple[tuple[str, ...], CompiledPrompt]] = []
        current_ids: list[str] = []
        current_prompt: CompiledPrompt | None = None
        for candidate_id in ordered_ids:
            trial_ids = current_ids + [candidate_id]
            trial_prompt = compile_fn(build_selection(trial_ids))
            over_budget = trial_prompt.characters > self.budget
            loses_full_detail = trial_prompt.detail != "full" and (
                current_prompt is None or current_prompt.detail == "full"
            )
            if (over_budget or loses_full_detail) and current_ids:
                shards.append((tuple(current_ids), current_prompt))
                current_ids = [candidate_id]
                current_prompt = compile_fn(build_selection(current_ids))
            else:
                current_ids = trial_ids
                current_prompt = trial_prompt
        if current_ids:
            shards.append((tuple(current_ids), current_prompt))
        return shards

    def _plan_entities(self, selection: Selection, text: str, skip_zero_relevance: bool) -> ShardPlan:
        scores = score_entities(self.schema, selection, text) if text else None
        ordered, skipped = self._order_and_filter(selection.entity_type_ids, scores, skip_zero_relevance)

        def build(ids: list[str]) -> Selection:
            return dataclasses.replace(selection, entity_type_ids=tuple(ids))

        shards = []
        for ids, prompt in self._pack_shards(ordered, build, self.entity_prompt):
            prompt.type_ids = ids
            shards.append(prompt)
        reason = (
            f"{len(skipped)} entity type(s) skipped: no label or alias found in the document text."
            if skipped
            else ""
        )
        return ShardPlan(ENTITY_PASS, shards, tuple(skipped), reason)

    def _scoped_entities(
        self, relationship_ids: Sequence[str], full_entity_ids: Sequence[str]
    ) -> tuple[str, ...]:
        """The subset of `full_entity_ids` these predicates can actually point
        at, via their domain/range — so a shard's "ENTITY TYPE IDS" tail only
        lists what its own predicates need, not every entity the analyst
        selected for the whole run."""
        full = set(full_entity_ids)
        keep: set[str] = set()
        for type_id in relationship_ids:
            relationship = self.schema.relationships.get(type_id, {})
            for endpoint_id in (
                *relationship.get("domain_type_ids", []),
                *relationship.get("range_type_ids", []),
            ):
                if endpoint_id in full:
                    keep.add(endpoint_id)
        return tuple(type_id for type_id in full_entity_ids if type_id in keep)

    def _plan_relationships(self, selection: Selection, text: str, skip_zero_relevance: bool) -> ShardPlan:
        scores = score_relationships(self.schema, selection, text) if text else None
        ordered, skipped = self._order_and_filter(selection.relationship_type_ids, scores, skip_zero_relevance)

        def build(ids: list[str]) -> Selection:
            entity_ids = self._scoped_entities(ids, selection.entity_type_ids)
            return dataclasses.replace(
                selection, relationship_type_ids=tuple(ids), entity_type_ids=entity_ids
            )

        shards = []
        for ids, prompt in self._pack_shards(ordered, build, self.relationship_prompt):
            prompt.type_ids = ids
            shards.append(prompt)
        reason = (
            f"{len(skipped)} predicate(s) skipped: no surface form found in the document text."
            if skipped
            else ""
        )
        return ShardPlan(RELATIONSHIP_PASS, shards, tuple(skipped), reason)

    # ----------------------------------------------------------------- facade

    def compile(self, selection: Selection) -> dict[str, CompiledPrompt]:
        return {
            ENTITY_PASS: self.entity_prompt(selection),
            RELATIONSHIP_PASS: self.relationship_prompt(selection),
        }


def examples_for(
    schema: TargetingSchema, selection: Selection, pass_id: str
) -> list[dict[str, Any]]:
    """Few-shot examples that are legal inside this slice.

    host_semantic_validation.module_example_rule: filter by pass and selected
    modules, then include an example only when every id it references is
    available in the selection. An example naming a de-selected type would
    teach the model to emit exactly what the host is about to reject.
    """
    modules = set(selection.module_ids)
    entity_ids = set(selection.entity_type_ids)
    relationship_ids = set(selection.relationship_type_ids)
    kept: list[dict[str, Any]] = []
    for example in schema.few_shot_examples:
        if example.get("pass") != pass_id:
            continue
        if example.get("module_ids") and not modules.intersection(example["module_ids"]):
            continue
        referenced_ok = True
        for extraction in example.get("extractions", []):
            attributes = extraction.get("attributes", {})
            for name in ("type_id", "subject_type_id", "object_type_id"):
                value = attributes.get(name)
                if value and value not in entity_ids and value != "skos:Concept":
                    referenced_ok = False
            predicate_id = attributes.get("predicate_id")
            if predicate_id and predicate_id not in relationship_ids:
                referenced_ok = False
            value_set_id = attributes.get("value_set_id")
            if value_set_id and value_set_id not in selection.value_set_ids:
                referenced_ok = False
        if referenced_ok:
            kept.append(example)
    return kept
