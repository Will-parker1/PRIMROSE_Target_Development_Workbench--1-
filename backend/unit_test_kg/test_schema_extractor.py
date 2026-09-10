"""Regression tests for the shard-aware `SchemaGuidedExtractor` run path.

These patch `langextract.extract` so no local model is required. They check
that a broad selection is now driven by multiple budget-bounded prompts
instead of the single, over-budget prompt `PromptCompiler.compile` alone would
produce (see `PromptTests.test_every_default_module_overflows_the_budget` in
`test_schema.py`), and that a narrow selection still makes exactly one call,
unchanged from before sharding existed.
"""

from __future__ import annotations

import importlib.util
import unittest
from unittest import mock

from kg_backend.schema import load_schema
from kg_backend.schema_prompt import DEFAULT_PROMPT_BUDGET, RELATIONSHIP_PASS

SCHEMA_EXTRACTOR_AVAILABLE = (
    importlib.util.find_spec("networkx") is not None
    and importlib.util.find_spec("langextract") is not None
)

if SCHEMA_EXTRACTOR_AVAILABLE:
    from building_kg.schema_extractor import SchemaGuidedExtractor

TP3_TYPES = [
    "core:TargetSystem",
    "core:TargetSystemComponent",
    "core:Function",
    "core:Dependency",
]


class _FakeAnnotated:
    def __init__(self, extractions=None) -> None:
        self.extractions = extractions or []


def _covering_text(schema, selection) -> str:
    """Text that mentions every selected entity/predicate's first surface
    form, so relevance scoring skips nothing — used where a test wants to
    exercise sharding/packing on its own, not the relevance filter."""
    terms = []
    for type_id in selection.entity_type_ids:
        entity = schema.entities.get(type_id, {})
        terms.append(entity.get("label") or type_id)
    for type_id in selection.relationship_type_ids:
        relationship = schema.relationships.get(type_id, {})
        forms = relationship.get("surface_forms") or [relationship.get("label", type_id)]
        terms.append(forms[0])
    return ". ".join(terms)


@unittest.skipUnless(
    SCHEMA_EXTRACTOR_AVAILABLE, "networkx and langextract are not both installed"
)
class ShardedRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_schema()

    def _extractor(
        self, selection_request: dict, *, skip_zero_relevance_shards: bool = False
    ) -> "SchemaGuidedExtractor":
        return SchemaGuidedExtractor(
            schema=self.schema,
            selection_request=selection_request,
            skip_zero_relevance_shards=skip_zero_relevance_shards,
            verbose=False,
        )

    def test_broad_selection_drives_multiple_under_budget_calls(self) -> None:
        # The default (unnarrowed) selection is the known-broad case: see
        # PromptTests.test_every_default_module_overflows_the_budget.
        extractor = self._extractor({})
        self.assertGreater(len(extractor.selection.relationship_type_ids), 100)
        text = _covering_text(self.schema, extractor.selection)

        seen_prompts: list[str] = []

        def fake_extract(*, prompt_description: str, **_kwargs) -> _FakeAnnotated:
            seen_prompts.append(prompt_description)
            return _FakeAnnotated()

        with mock.patch("langextract.extract", side_effect=fake_extract):
            annotated, plan = extractor._run_pass(text, RELATIONSHIP_PASS)

        self.assertGreater(len(plan.shards), 1)
        self.assertEqual(plan.skipped_type_ids, ())
        self.assertEqual(len(seen_prompts), len(plan.shards))
        for prompt_text in seen_prompts:
            self.assertLessEqual(len(prompt_text), DEFAULT_PROMPT_BUDGET)
        # Every shard compiles a distinct slice of the catalogue.
        self.assertEqual(len(set(seen_prompts)), len(seen_prompts))
        self.assertEqual(annotated.extractions, [])

    def test_narrow_selection_still_makes_exactly_one_call(self) -> None:
        extractor = self._extractor(
            {"module_ids": ["tgt:TP3"], "entity_type_ids": TP3_TYPES}
        )
        text = _covering_text(self.schema, extractor.selection)
        calls: list[str] = []

        def fake_extract(*, prompt_description: str, **_kwargs) -> _FakeAnnotated:
            calls.append(prompt_description)
            return _FakeAnnotated()

        with mock.patch("langextract.extract", side_effect=fake_extract):
            _annotated, plan = extractor._run_pass(text, RELATIONSHIP_PASS)

        self.assertEqual(len(plan.shards), 1)
        self.assertEqual(len(calls), 1)

    def test_default_does_not_drop_a_paraphrased_but_real_relationship(self) -> None:
        # Live regression: a real document ("...takes supply from...", "...
        # attended under the standing maintenance framework...") plainly
        # states several relationships, but none of it repeats any of the
        # schema's curated surface_forms verbatim. With skip_zero_relevance
        # defaulting to True, that scored zero for 168 of 169 selected
        # predicates and the run never gave the model a chance to extract any
        # of them. The default must keep every selected predicate in the run.
        extractor = self._extractor({})
        text = (
            "Substation 'Mlynivske' takes supply from Substation 'Ternova Balka'; "
            "a fault at that location would propagate here. Hrushivka Tekhservis "
            "PrJSC attended under the standing maintenance framework."
        )

        with mock.patch(
            "langextract.extract", side_effect=lambda **_kwargs: _FakeAnnotated()
        ):
            _annotated, plan = extractor._run_pass(text, RELATIONSHIP_PASS)

        self.assertEqual(plan.skipped_type_ids, ())
        shard_ids = [type_id for shard in plan.shards for type_id in shard.type_ids]
        self.assertEqual(sorted(shard_ids), sorted(extractor.selection.relationship_type_ids))

    def test_relevant_text_skips_unrelated_shards_only_when_opted_in(self) -> None:
        extractor = self._extractor({}, skip_zero_relevance_shards=True)
        predicate_id = extractor.selection.relationship_type_ids[0]
        surface_form = extractor.schema.relationships[predicate_id]["surface_forms"][0]
        text = surface_form * 5

        with mock.patch(
            "langextract.extract", side_effect=lambda **_kwargs: _FakeAnnotated()
        ):
            _annotated, plan = extractor._run_pass(text, RELATIONSHIP_PASS)

        self.assertIn(predicate_id, plan.shards[0].type_ids)
        self.assertTrue(plan.skipped_type_ids)
        self.assertNotIn(predicate_id, plan.skipped_type_ids)

    def test_text_with_no_lexical_trace_skips_the_pass_without_erroring_when_opted_in(
        self,
    ) -> None:
        # Nothing in the run makes this impossible in principle: if the
        # document has no lexical trace of anything selected and the caller
        # opted into skipping, the pass should report zero shards, not raise.
        extractor = self._extractor(
            {"module_ids": ["tgt:TP3"], "entity_type_ids": TP3_TYPES},
            skip_zero_relevance_shards=True,
        )

        with mock.patch(
            "langextract.extract", side_effect=lambda **_kwargs: _FakeAnnotated()
        ):
            annotated, plan = extractor._run_pass(
                "a completely unrelated sentence about baking bread.", RELATIONSHIP_PASS
            )

        self.assertEqual(plan.shards, [])
        self.assertEqual(annotated.extractions, [])

    def test_process_text_records_shard_stats_on_the_report(self) -> None:
        extractor = self._extractor({})
        text = _covering_text(self.schema, extractor.selection)

        with mock.patch(
            "langextract.extract", side_effect=lambda **_kwargs: _FakeAnnotated()
        ):
            _graph, report = extractor.process_text(text, source="test")

        relationship_report = next(
            entry for entry in report.passes if entry.pass_id == RELATIONSHIP_PASS
        )
        self.assertGreater(relationship_report.shard_count, 1)


if __name__ == "__main__":
    unittest.main()
