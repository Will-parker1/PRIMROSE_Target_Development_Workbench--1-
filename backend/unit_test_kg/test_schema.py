"""Regression tests for schema selection, prompt compilation and validation.

These cover the parts of the schema contract that a model cannot be trusted to
respect: what the down-selection lets through, what the validator rejects, and
what the projection produces. No local model is required.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from kg_backend.schema import (
    CORE_MODULE_ID,
    ENTITY_PASS,
    RELATIONSHIP_PASS,
    UNRESOLVED,
    load_schema,
)
from kg_backend.schema_prompt import DEFAULT_PROMPT_BUDGET, PromptCompiler, examples_for
from kg_backend.schema_service import SchemaService

TP3 = "tgt:TP3"
TP3_TYPES = [
    "core:TargetSystem",
    "core:TargetSystemComponent",
    "core:Function",
    "core:Dependency",
]


def relationship_record(**overrides) -> dict:
    attributes = {
        "subject_text": "Component C-4",
        "subject_type_id": "core:TargetSystemComponent",
        "predicate_id": "tgt_rel:componentContributesToFunction",
        "object_text": "Function F-2",
        "object_type_id": "core:Function",
        "polarity": "POSITIVE",
        "certainty": "ASSERTED",
        "modality": "REPORTED",
        "source_direction": "CANONICAL",
        "review_action": "EXTRACT_AS_REPORTED",
    }
    attributes.update(overrides)
    return {
        "extraction_class": "relationship",
        "extraction_text": "Component C-4 contributes to Function F-2",
        "attributes": attributes,
    }


class SchemaMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_schema()

    def narrow(self):
        return self.schema.resolve({"module_ids": [TP3], "entity_type_ids": TP3_TYPES})


class SelectionTests(SchemaMixin):
    def test_core_module_is_always_loaded(self) -> None:
        selection = self.schema.resolve({"module_ids": [TP3]})
        self.assertIn(CORE_MODULE_ID, selection.module_ids)

    def test_generic_module_is_not_a_runtime_default(self) -> None:
        selection = self.schema.resolve({})
        self.assertNotIn("core:GENERIC_TSA_REFERENCE", selection.module_ids)

    def test_narrowing_types_narrows_predicates(self) -> None:
        wide = self.schema.resolve({"module_ids": [TP3]})
        narrow = self.narrow()
        self.assertLess(len(narrow.entity_type_ids), len(wide.entity_type_ids))
        self.assertLess(len(narrow.relationship_type_ids), len(wide.relationship_type_ids))

    def test_host_derived_predicates_are_never_offered(self) -> None:
        selection = self.schema.resolve({})
        for type_id in selection.relationship_type_ids:
            self.assertNotEqual(
                self.schema.relationships[type_id].get("model_action"),
                "HOST_DERIVED_ONLY",
            )

    def test_surviving_predicates_have_both_endpoints_covered(self) -> None:
        selection = self.narrow()
        selected = set(selection.entity_type_ids)
        for type_id in selection.relationship_type_ids:
            relationship = self.schema.relationships[type_id]
            self.assertTrue(
                self.schema._endpoint_covered(relationship["domain_type_ids"], selected),
                f"{type_id} domain is not covered",
            )
            if not relationship.get("range_value_set_id"):
                self.assertTrue(
                    self.schema._endpoint_covered(relationship["range_type_ids"], selected),
                    f"{type_id} range is not covered",
                )

    def test_abstract_types_are_not_extractable(self) -> None:
        selection = self.schema.resolve({})
        self.assertNotIn("tgt:TargetingAssessment", selection.entity_type_ids)

    def test_unknown_ids_warn_rather_than_fail(self) -> None:
        selection = self.schema.resolve(
            {"module_ids": ["tgt:NOPE", TP3], "entity_type_ids": [*TP3_TYPES, "core:Nope"]}
        )
        self.assertTrue(selection.warnings)
        self.assertIn("core:TargetSystem", selection.entity_type_ids)

    def test_selection_order_is_stable(self) -> None:
        first = self.schema.resolve({"module_ids": [TP3], "entity_type_ids": TP3_TYPES})
        second = self.schema.resolve(
            {"module_ids": [TP3], "entity_type_ids": list(reversed(TP3_TYPES))}
        )
        self.assertEqual(first.entity_type_ids, second.entity_type_ids)


class PromptTests(SchemaMixin):
    def test_narrow_selection_keeps_full_detail(self) -> None:
        prompts = PromptCompiler(self.schema).compile(self.narrow())
        for compiled in prompts.values():
            self.assertEqual(compiled.detail, "full")
            self.assertEqual(compiled.notes, [])

    def test_every_default_module_overflows_the_budget(self) -> None:
        # The reason down-selection exists: all modules at once is not a prompt
        # a small model can use, and the compiler must say so rather than
        # quietly emitting it.
        prompts = PromptCompiler(self.schema).compile(self.schema.resolve({}))
        self.assertTrue(prompts[RELATIONSHIP_PASS].notes)

    def test_prompt_only_names_selected_terms(self) -> None:
        selection = self.narrow()
        text = PromptCompiler(self.schema).compile(selection)[ENTITY_PASS].text
        self.assertIn("core:TargetSystem =", text)
        self.assertNotIn("tgt:TargetEngagementAuthority =", text)

    def test_prohibited_uses_are_compiled_into_both_passes(self) -> None:
        prompts = PromptCompiler(self.schema).compile(self.narrow())
        for compiled in prompts.values():
            self.assertIn("Selecting, recommending or prioritising real targets", compiled.text)

    def test_examples_are_filtered_to_the_selection(self) -> None:
        selection = self.narrow()
        for example in examples_for(self.schema, selection, RELATIONSHIP_PASS):
            for extraction in example["extractions"]:
                predicate = extraction["attributes"].get("predicate_id")
                self.assertIn(predicate, selection.relationship_type_ids)

    def test_no_extraction_examples_are_guardrails_not_examples(self) -> None:
        text = PromptCompiler(self.schema).compile(self.narrow())[RELATIONSHIP_PASS].text
        self.assertIn("EXTRACT NOTHING FROM WORDING LIKE THIS", text)


class ShardPlanTests(SchemaMixin):
    """`PromptCompiler.plan` replaces "one prompt that may overflow the
    budget" with "as many budget-bounded prompts as the selection needs" —
    see PromptTests.test_every_default_module_overflows_the_budget for the
    single-prompt behaviour this exists alongside, unchanged."""

    def test_broad_selection_shards_all_stay_under_budget(self) -> None:
        selection = self.schema.resolve({})
        plan = PromptCompiler(self.schema).plan(selection, RELATIONSHIP_PASS, "")
        self.assertGreater(len(plan.shards), 1)
        for shard in plan.shards:
            self.assertLessEqual(shard.characters, DEFAULT_PROMPT_BUDGET)
        # every predicate the analyst selected is accounted for: it is either
        # rendered in exactly one shard, or explicitly skipped (it is not,
        # here, since scoring is disabled for an empty document).
        self.assertEqual(plan.skipped_type_ids, ())
        shard_ids = [type_id for shard in plan.shards for type_id in shard.type_ids]
        self.assertEqual(sorted(shard_ids), sorted(selection.relationship_type_ids))

    def test_narrow_selection_is_a_single_full_detail_shard(self) -> None:
        selection = self.narrow()
        for pass_id in (ENTITY_PASS, RELATIONSHIP_PASS):
            plan = PromptCompiler(self.schema).plan(selection, pass_id, "")
            self.assertEqual(len(plan.shards), 1)
            self.assertEqual(plan.shards[0].detail, "full")

    def test_empty_text_disables_relevance_filtering(self) -> None:
        selection = self.schema.resolve({})
        plan = PromptCompiler(self.schema).plan(selection, RELATIONSHIP_PASS, "")
        self.assertEqual(plan.skipped_type_ids, ())

    def test_text_orders_matching_predicates_first_but_skips_nothing_by_default(self) -> None:
        # skip_zero_relevance defaults to False: relevance is a plain
        # substring match against curated surface forms, and real prose
        # paraphrases far more often than it repeats them verbatim (this is
        # exactly what a live run against a real document showed — see
        # test_relevant_text_skips_unrelated_shards_only_when_opted_in in
        # test_schema_extractor.py). The default must still offer every
        # selected predicate to the model, just packed with the
        # best-evidenced ones first.
        selection = self.schema.resolve({})
        predicate_id = selection.relationship_type_ids[0]
        surface_form = self.schema.relationships[predicate_id]["surface_forms"][0]
        text = surface_form * 5

        plan = PromptCompiler(self.schema).plan(selection, RELATIONSHIP_PASS, text)

        self.assertEqual(plan.skipped_type_ids, ())
        self.assertIn(predicate_id, plan.shards[0].type_ids)
        shard_ids = [type_id for shard in plan.shards for type_id in shard.type_ids]
        self.assertEqual(sorted(shard_ids), sorted(selection.relationship_type_ids))

    def test_text_narrows_to_matching_predicates_when_opted_in(self) -> None:
        selection = self.schema.resolve({})
        predicate_id = selection.relationship_type_ids[0]
        surface_form = self.schema.relationships[predicate_id]["surface_forms"][0]
        text = surface_form * 5

        plan = PromptCompiler(self.schema).plan(
            selection, RELATIONSHIP_PASS, text, skip_zero_relevance=True
        )

        self.assertIn(predicate_id, plan.shards[0].type_ids)
        self.assertNotIn(predicate_id, plan.skipped_type_ids)
        self.assertTrue(plan.skipped_type_ids)
        self.assertLess(len(plan.shards), 3)

    def test_shard_entity_tail_is_scoped_to_its_own_predicates(self) -> None:
        # A relationship shard should only list the entity type ids its own
        # predicates can actually point at, not every entity type the analyst
        # selected for the whole run.
        selection = self.schema.resolve({})
        plan = PromptCompiler(self.schema).plan(selection, RELATIONSHIP_PASS, "")
        first_shard = plan.shards[0]
        scoped = PromptCompiler(self.schema)._scoped_entities(
            first_shard.type_ids, selection.entity_type_ids
        )
        self.assertLess(len(scoped), len(selection.entity_type_ids))
        for type_id in scoped:
            self.assertIn(f"  {type_id} =", first_shard.text)

    def test_shards_cover_every_id_exactly_once(self) -> None:
        selection = self.schema.resolve({})
        plan = PromptCompiler(self.schema).plan(selection, RELATIONSHIP_PASS, "")
        seen: list[str] = []
        for shard in plan.shards:
            seen.extend(shard.type_ids)
        self.assertEqual(len(seen), len(set(seen)))


class ValidationTests(SchemaMixin):
    def test_valid_relationship_passes(self) -> None:
        result = self.schema.validate_extraction(
            relationship_record(), self.narrow(), RELATIONSHIP_PASS
        )
        self.assertTrue(result.ok, result.errors)

    def test_off_registry_type_is_rejected(self) -> None:
        record = {
            "extraction_class": "entity",
            "extraction_text": "TEA-2",
            "attributes": {
                "type_id": "tgt:TargetEngagementAuthority",
                "review_action": "EXTRACT_AS_REPORTED",
            },
        }
        result = self.schema.validate_extraction(record, self.narrow(), ENTITY_PASS)
        self.assertFalse(result.ok)

    def test_domain_violation_is_rejected(self) -> None:
        result = self.schema.validate_extraction(
            relationship_record(subject_type_id="core:Function"),
            self.narrow(),
            RELATIONSHIP_PASS,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("domain" in error for error in result.errors))

    def test_controlled_values_are_not_repaired(self) -> None:
        result = self.schema.validate_extraction(
            relationship_record(polarity="NEGATIVE"), self.narrow(), RELATIONSHIP_PASS
        )
        self.assertFalse(result.ok)

    def test_unknown_attributes_are_dropped(self) -> None:
        result = self.schema.validate_extraction(
            relationship_record(colour="red"), self.narrow(), RELATIONSHIP_PASS
        )
        self.assertNotIn("colour", result.record["attributes"])

    def test_unresolved_entity_escalates_to_human_review(self) -> None:
        record = {
            "extraction_class": "entity",
            "extraction_text": "the thing",
            "attributes": {"type_id": UNRESOLVED, "review_action": "EXTRACT_AS_REPORTED"},
        }
        result = self.schema.validate_extraction(record, self.narrow(), ENTITY_PASS)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.record["attributes"]["review_action"], "HUMAN_REVIEW_REQUIRED")

    def test_review_action_must_respect_model_action(self) -> None:
        # An EXTRACT_EXPLICIT_ONLY predicate may only ever be reported as stated.
        explicit = next(
            type_id
            for type_id in self.narrow().relationship_type_ids
            if self.schema.relationships[type_id].get("model_action") == "EXTRACT_EXPLICIT_ONLY"
        )
        relationship = self.schema.relationships[explicit]
        result = self.schema.validate_extraction(
            relationship_record(
                predicate_id=explicit,
                subject_type_id=relationship["domain_type_ids"][0],
                object_type_id=relationship["range_type_ids"][0],
                review_action="PROPOSE_FOR_REVIEW",
            ),
            self.narrow(),
            RELATIONSHIP_PASS,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("model_action" in error for error in result.errors))

    def test_external_type_needs_to_be_declared(self) -> None:
        result = self.schema.validate_extraction(
            relationship_record(object_type_id="skos:Concept"),
            self.narrow(),
            RELATIONSHIP_PASS,
        )
        self.assertFalse(result.ok)

    def test_wrong_pass_class_is_rejected(self) -> None:
        result = self.schema.validate_extraction(
            relationship_record(), self.narrow(), ENTITY_PASS
        )
        self.assertFalse(result.ok)


class RepairTests(SchemaMixin):
    def test_prefix_is_normalised_when_unambiguous(self) -> None:
        selection = self.narrow()
        self.assertEqual(
            self.schema.repair_identifier("tgt_rel:performsFunction", selection, "relationship"),
            "core_rel:performsFunction",
        )

    def test_unknown_local_name_is_left_alone(self) -> None:
        selection = self.narrow()
        self.assertEqual(
            self.schema.repair_identifier("tgt_rel:invented", selection, "relationship"),
            "tgt_rel:invented",
        )

    def test_repair_reports_what_it_changed(self) -> None:
        record = relationship_record(predicate_id="tgt_rel:performsFunction")
        notes = self.schema.repair_extraction(record, self.narrow(), RELATIONSHIP_PASS)
        self.assertEqual(len(notes), 1)
        self.assertEqual(record["attributes"]["predicate_id"], "core_rel:performsFunction")


NETWORKX_AVAILABLE = importlib.util.find_spec("networkx") is not None

SOURCE = (
    "Target System S-1 contains Component C-4. Component C-4 contributes to "
    "Function F-2, reported on 12 March 2026."
)


class FakeInterval:
    def __init__(self, start: int, end: int):
        self.start_pos, self.end_pos = start, end


class FakeExtraction:
    """Stands in for a LangExtract Extraction so projection is testable offline."""

    def __init__(self, extraction_class: str, text: str, attributes: dict):
        self.extraction_class = extraction_class
        self.extraction_text = text
        self.attributes = attributes
        start = SOURCE.find(text)
        self.char_interval = FakeInterval(start, start + len(text)) if start >= 0 else None


class FakeDocument:
    def __init__(self, extractions):
        self.extractions = extractions


@unittest.skipUnless(NETWORKX_AVAILABLE, "networkx is not installed")
class ProjectionTests(SchemaMixin):
    def extractor(self):
        from building_kg.schema_extractor import SchemaGuidedExtractor

        return SchemaGuidedExtractor(
            schema=self.schema,
            selection_request={"module_ids": [TP3], "entity_type_ids": TP3_TYPES},
            verbose=False,
        )

    def test_ungrounded_span_is_rejected(self) -> None:
        extractor = self.extractor()
        document = FakeDocument(
            [
                FakeExtraction(
                    "entity",
                    "A Paraphrased System",
                    {"type_id": "core:TargetSystem", "review_action": "EXTRACT_AS_REPORTED"},
                )
            ]
        )
        records, report = extractor._collect(document, ENTITY_PASS, SOURCE)
        self.assertEqual(records, [])
        self.assertEqual(report.rejected, 1)

    def test_fabricated_qualifier_is_rejected(self) -> None:
        extractor = self.extractor()
        record = relationship_record(valid_time_text="1 April 2027")
        document = FakeDocument(
            [
                FakeExtraction(
                    "relationship", record["extraction_text"], record["attributes"]
                )
            ]
        )
        records, report = extractor._collect(document, RELATIONSHIP_PASS, SOURCE)
        self.assertEqual(records, [])
        self.assertIn(
            "valid_time_text is not verbatim in the source", report.reasons
        )

    def test_quoted_qualifier_present_in_source_is_kept(self) -> None:
        extractor = self.extractor()
        record = relationship_record(valid_time_text="12 March 2026")
        document = FakeDocument(
            [
                FakeExtraction(
                    "relationship", record["extraction_text"], record["attributes"]
                )
            ]
        )
        records, _ = extractor._collect(document, RELATIONSHIP_PASS, SOURCE)
        self.assertEqual(len(records), 1)

    def test_projection_reifies_the_assertion(self) -> None:
        extractor = self.extractor()
        entities = [
            {
                "extraction_class": "entity",
                "extraction_text": "Component C-4",
                "attributes": {
                    "type_id": "core:TargetSystemComponent",
                    "review_action": "EXTRACT_AS_REPORTED",
                },
                "char_start": 0,
                "char_end": 13,
            }
        ]
        graph = extractor._project(entities, [relationship_record()], "demo.txt")
        kinds = [data.get("record_kind") for _, data in graph.nodes(data=True)]
        self.assertEqual(kinds.count("SOURCE_ASSERTION"), 1)
        self.assertEqual(kinds.count("MENTION"), 2)
        # The assertion holds the claim; the two structural edges only anchor it.
        self.assertEqual(graph.number_of_edges(), 2)
        for _, _, data in graph.edges(data=True):
            self.assertTrue(data["structural"])

    def test_model_predictions_project_as_predictions(self) -> None:
        extractor = self.extractor()
        graph = extractor._project(
            [], [relationship_record(modality="MODEL_PREDICTED")], "demo.txt"
        )
        kinds = {data.get("record_kind") for _, data in graph.nodes(data=True)}
        self.assertIn("PREDICTION", kinds)

    def test_projection_imports_into_the_store(self) -> None:
        from kg_backend import GraphStore

        extractor = self.extractor()
        graph = extractor._project([], [relationship_record()], "demo.txt")
        data = GraphStore.validate_graph_data(extractor.to_node_link(graph))
        self.assertTrue(data["nodes"])
        self.assertTrue(all(node["id"] for node in data["nodes"]))

    def test_character_offset_zero_survives(self) -> None:
        extractor = self.extractor()
        entities = [
            {
                "extraction_class": "entity",
                "extraction_text": "Target System S-1",
                "attributes": {
                    "type_id": "core:TargetSystem",
                    "review_action": "EXTRACT_AS_REPORTED",
                },
                "char_start": 0,
                "char_end": 17,
            }
        ]
        graph = extractor._project(entities, [], "demo.txt")
        node = next(data for _, data in graph.nodes(data=True))
        self.assertEqual(node["char_start"], 0)


@unittest.skipUnless(NETWORKX_AVAILABLE, "networkx is not installed")
class SourceDocumentTests(SchemaMixin):
    def extractor(self):
        from building_kg.schema_extractor import SchemaGuidedExtractor

        return SchemaGuidedExtractor(
            schema=self.schema,
            selection_request={"module_ids": [TP3], "entity_type_ids": TP3_TYPES},
            verbose=False,
        )

    def document(self, **overrides):
        from building_kg.schema_extractor import SourceDocument

        fields = {
            "filename": "note.txt",
            "media_type": "text",
            "sha256": "a" * 64,
            "byte_size": len(SOURCE),
            "character_count": len(SOURCE),
        }
        fields.update(overrides)
        return SourceDocument(**fields)

    def test_identity_is_the_content_hash(self) -> None:
        first = self.document(filename="one.txt")
        second = self.document(filename="two.txt")
        self.assertEqual(first.node_id, second.node_id)
        self.assertNotEqual(first.node_id, self.document(sha256="b" * 64).node_id)

    def test_pages_resolve_from_marker_offsets(self) -> None:
        document = self.document(page_offsets=[(0, 1), (100, 2), (250, 3)])
        self.assertEqual(document.page_for(0), 1)
        self.assertEqual(document.page_for(99), 1)
        self.assertEqual(document.page_for(100), 2)
        self.assertEqual(document.page_for(400), 3)
        self.assertEqual(document.page_for(None), 0)

    def test_text_document_has_no_pages(self) -> None:
        self.assertEqual(self.document().page_for(10), 0)

    def test_read_document_describes_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stored-as-this.txt"
            path.write_text(SOURCE, encoding="utf-8")
            _, document = self.extractor().read_document(path, "analyst-name.txt")
        # The name the analyst supplied wins over the name on disk.
        self.assertEqual(document.filename, "analyst-name.txt")
        self.assertEqual(document.character_count, len(SOURCE))
        self.assertTrue(document.ingested_at)

    def test_every_record_carries_its_document(self) -> None:
        extractor = self.extractor()
        entities = [
            {
                "extraction_class": "entity",
                "extraction_text": "Component C-4",
                "attributes": {
                    "type_id": "core:TargetSystemComponent",
                    "review_action": "EXTRACT_AS_REPORTED",
                },
                "char_start": SOURCE.find("Component C-4"),
                "char_end": SOURCE.find("Component C-4") + 13,
            }
        ]
        graph = extractor._project(
            entities,
            [relationship_record()],
            "note.txt",
            document=self.document(),
            source_text=SOURCE,
        )
        reviewable = [
            data
            for _, data in graph.nodes(data=True)
            if data.get("record_kind") in {"MENTION", "SOURCE_ASSERTION"}
        ]
        self.assertTrue(reviewable)
        for data in reviewable:
            self.assertEqual(data.get("source_document"), "note.txt")
            self.assertTrue(data.get("document_id"))
            self.assertTrue(data.get("source_excerpt"))

    def test_document_node_and_provenance_edges_are_created(self) -> None:
        from building_kg.schema_extractor import (
            ASSERTION_SOURCE_RELATION,
            DOCUMENT_MENTION_RELATION,
        )

        extractor = self.extractor()
        graph = extractor._project(
            [], [relationship_record()], "note.txt", document=self.document(), source_text=SOURCE
        )
        documents = [
            node_id
            for node_id, data in graph.nodes(data=True)
            if data.get("record_kind") == "SOURCE_DOCUMENT"
        ]
        self.assertEqual(len(documents), 1)
        relations = Counter(data["relation"] for _, _, data in graph.edges(data=True))
        self.assertEqual(relations[ASSERTION_SOURCE_RELATION], 1)
        self.assertEqual(relations[DOCUMENT_MENTION_RELATION], 2)

    def test_provenance_edges_satisfy_their_declared_domain_and_range(self) -> None:
        """The host's own edges must obey the schema it enforces on the model."""
        from building_kg.schema_extractor import (
            ASSERTION_SOURCE_RELATION,
            DOCUMENT_MENTION_RELATION,
            DOCUMENT_RECORD_TYPE,
            MENTION_RECORD_TYPE,
        )

        has_mention = self.schema.relationships[DOCUMENT_MENTION_RELATION]
        self.assertTrue(
            self.schema.satisfies(DOCUMENT_RECORD_TYPE, has_mention["domain_type_ids"])
        )
        self.assertTrue(
            self.schema.satisfies(MENTION_RECORD_TYPE, has_mention["range_type_ids"])
        )
        has_source = self.schema.relationships[ASSERTION_SOURCE_RELATION]
        self.assertTrue(
            self.schema.satisfies("core:SourceAssertion", has_source["domain_type_ids"])
        )
        self.assertTrue(
            self.schema.satisfies(DOCUMENT_RECORD_TYPE, has_source["range_type_ids"])
        )

    def test_host_created_records_are_marked(self) -> None:
        extractor = self.extractor()
        graph = extractor._project(
            [], [relationship_record()], "note.txt", document=self.document(), source_text=SOURCE
        )
        document_node = next(
            data
            for _, data in graph.nodes(data=True)
            if data.get("record_kind") == "SOURCE_DOCUMENT"
        )
        self.assertTrue(document_node["host_derived"])
        for _, _, data in graph.edges(data=True):
            self.assertTrue(data.get("host_derived"), data.get("relation"))

    def test_excerpt_widens_to_whole_words(self) -> None:
        extractor = self.extractor()
        start = SOURCE.find("Component C-4")
        excerpt = extractor._excerpt(SOURCE, start, start + 13)
        self.assertIn("Component C-4", excerpt)
        self.assertNotIn("  ", excerpt)

    def test_excerpt_is_empty_without_offsets(self) -> None:
        self.assertEqual(self.extractor()._excerpt(SOURCE, None, None), "")

    def test_unaligned_span_recovers_its_page_from_its_own_text(self) -> None:
        # LangExtract does not always return a character interval; the text is
        # verbatim by then, so the location can be recovered by searching.
        document = self.document(page_offsets=[(0, 1), (SOURCE.find("Component C-4") - 5, 2)])
        provenance = self.extractor()._provenance(
            document, SOURCE, None, None, "Component C-4"
        )
        self.assertEqual(provenance["source_page"], 2)
        self.assertIn("Component C-4", provenance["source_excerpt"])

    def test_projection_without_a_document_still_works(self) -> None:
        graph = self.extractor()._project([], [relationship_record()], "note.txt")
        kinds = {data.get("record_kind") for _, data in graph.nodes(data=True)}
        self.assertNotIn("SOURCE_DOCUMENT", kinds)


class SchemaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.service = SchemaService(Path(self._directory.name))

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_selection_round_trips_to_disk(self) -> None:
        self.service.save({"module_ids": [TP3], "entity_type_ids": TP3_TYPES})
        reloaded = SchemaService(Path(self._directory.name))
        self.assertEqual(
            sorted(reloaded.current_request()["entity_type_ids"]), sorted(TP3_TYPES)
        )

    def test_empty_selection_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save({"module_ids": [TP3], "entity_type_ids": ["core:Nope"]})

    def test_reset_returns_to_schema_defaults(self) -> None:
        self.service.save({"module_ids": [TP3], "entity_type_ids": TP3_TYPES})
        after = self.service.reset()
        self.assertGreater(after["counts"]["entity_types"], len(TP3_TYPES))
        self.assertFalse((Path(self._directory.name) / "extraction_selection.json").exists())

    def test_preview_does_not_save(self) -> None:
        before = self.service.current_request()
        self.service.preview({"module_ids": [TP3], "entity_type_ids": TP3_TYPES})
        self.assertEqual(self.service.current_request(), before)

    def test_narrow_selection_is_reported_ready(self) -> None:
        payload = self.service.preview({"module_ids": [TP3], "entity_type_ids": TP3_TYPES})
        self.assertTrue(payload["ready"])

    def test_all_modules_is_not_reported_ready(self) -> None:
        self.assertFalse(self.service.preview({})["ready"])

    def test_bad_pass_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.prompt_text("BOTH")

    def test_topic_suggestion_is_reviewable_and_not_saved(self) -> None:
        before = self.service.current_request()
        suggestion = self.service.suggest(
            "system components and dependencies", max_entity_types=12
        )
        self.assertFalse(suggestion["saved"])
        self.assertEqual(suggestion["strategy"], "deterministic lexical match")
        self.assertIn(TP3, suggestion["suggestion"]["module_ids"])
        self.assertTrue(suggestion["matches"]["entity_types"])
        self.assertEqual(self.service.current_request(), before)

    def test_unmatched_topic_never_falls_back_to_full_schema(self) -> None:
        suggestion = self.service.suggest("zzzxxyyqqq")
        self.assertFalse(suggestion["ready"])
        self.assertEqual(suggestion["suggestion"]["entity_type_ids"], [])


if __name__ == "__main__":
    unittest.main()
