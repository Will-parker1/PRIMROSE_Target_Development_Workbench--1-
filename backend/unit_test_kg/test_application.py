"""Regression tests for the local knowledge-graph application."""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest import mock

from kg_backend import (
    ExtractionJobs,
    GraphRAGEngine,
    GraphStore,
    GraphValidationError,
    QueryEngine,
)
from projects import ProjectRegistry
from server import Application, WEB_ROOT, make_handler

LANGCHAIN_AVAILABLE = importlib.util.find_spec("langchain_core") is not None
RAPIDFUZZ_AVAILABLE = importlib.util.find_spec("rapidfuzz") is not None


def fixture_graph() -> dict:
    return {
        "directed": True,
        "multigraph": True,
        "graph": {"name": "Test graph"},
        "nodes": [
            {"id": "alpha", "label": "Alpha", "type": "Organisation"},
            {"id": "bravo", "label": "Bravo", "type": "Capability"},
            {"id": "charlie", "label": "Charlie", "type": "Location"},
        ],
        "links": [
            {
                "assertion_id": "a-1",
                "source": "alpha",
                "target": "bravo",
                "relation": "ENABLES",
                "source_text": "Alpha enables Bravo.",
            },
            {
                "assertion_id": "a-2",
                "source": "alpha",
                "target": "bravo",
                "relation": "ENABLES",
                "source_text": "A second source corroborates the relationship.",
            },
            {
                "assertion_id": "a-3",
                "source": "bravo",
                "target": "charlie",
                "relation": "LOCATED_AT",
                "source_text": "Bravo is located at Charlie.",
            },
        ],
    }


class TemporaryStoreMixin:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.graph_path = self.root / "graph.json"
        self.state_path = self.root / "runtime" / "review_state.json"
        self.graph_path.write_text(json.dumps(fixture_graph()), encoding="utf-8")
        self.store = GraphStore(self.graph_path, self.state_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()


class GraphStoreTests(TemporaryStoreMixin, unittest.TestCase):
    def test_summary_items_search_and_parallel_assertions(self) -> None:
        summary = self.store.summary()
        self.assertEqual(summary["nodes"], 3)
        self.assertEqual(summary["relationships"], 3)
        self.assertEqual(len(self.store._edges), 3)
        self.assertEqual(self.store.search("alpha")[0]["id"], "alpha")
        self.assertEqual(self.store.item("node", "alpha")["degree"], 2)
        self.assertTrue(self.store.graph_slice(limit=5)["links"])

    @unittest.skipUnless(RAPIDFUZZ_AVAILABLE, "rapidfuzz is not installed.")
    def test_resolve_node_falls_back_to_fuzzy_matching(self) -> None:
        self.assertEqual(self.store.resolve_node("Bravvo"), "bravo")
        self.assertEqual(self.store.resolve_node("Carlie"), "charlie")

    def test_resolve_node_without_fuzzy_match_returns_none(self) -> None:
        self.assertIsNone(self.store.resolve_node("Zulu"))

    def test_resolve_relation_matches_exact_normalised_and_substring(self) -> None:
        self.assertEqual(self.store.resolve_relation("ENABLES"), "ENABLES")
        self.assertEqual(self.store.resolve_relation("located at"), "LOCATED_AT")
        self.assertEqual(self.store.resolve_relation("enable"), "ENABLES")

    @unittest.skipUnless(RAPIDFUZZ_AVAILABLE, "rapidfuzz is not installed.")
    def test_resolve_relation_falls_back_to_fuzzy_matching(self) -> None:
        self.assertEqual(self.store.resolve_relation("ENABLLES"), "ENABLES")

    def test_relationship_neighbourhood_finds_the_nearest_hop(self) -> None:
        result = self.store.relationship_neighbourhood("Alpha", "LOCATED_AT")
        self.assertIsNotNone(result)
        self.assertEqual(result["origin"]["id"], "alpha")
        self.assertEqual(result["hops"], 2)
        self.assertEqual(
            {(m["source"]["id"], m["target"]["id"]) for m in result["matches"]},
            {("bravo", "charlie")},
        )

    def test_relationship_neighbourhood_returns_none_when_unreachable(self) -> None:
        self.assertIsNone(self.store.relationship_neighbourhood("Alpha", "NO_SUCH_RELATION"))
        self.assertIsNone(self.store.relationship_neighbourhood("Zulu", "ENABLES"))

    def test_relationship_neighbourhood_excludes_rejected_records(self) -> None:
        self.store.decide("edge", next(
            edge_id for edge_id, edge in self.store._edges.items()
            if edge["assertion_id"] == "a-3"
        ), "rejected", "Unsupported.")
        self.assertIsNone(self.store.relationship_neighbourhood("Alpha", "LOCATED_AT"))

    def test_validation_rejects_duplicates_and_unknown_endpoints(self) -> None:
        duplicate = fixture_graph()
        duplicate["nodes"].append({"id": "alpha"})
        with self.assertRaises(GraphValidationError):
            GraphStore.validate_graph_data(duplicate)
        unknown = fixture_graph()
        unknown["links"][0]["target"] = "missing"
        with self.assertRaises(GraphValidationError):
            GraphStore.validate_graph_data(unknown)

    def test_review_persists_and_rejection_requires_reason(self) -> None:
        with self.assertRaises(ValueError):
            self.store.decide("node", "alpha", "rejected")
        result = self.store.decide("node", "alpha", "rejected", "Wrong entity.")
        self.assertEqual(result["status"], "rejected")
        reloaded = GraphStore(self.graph_path, self.state_path)
        self.assertEqual(reloaded.item("node", "alpha")["status"], "rejected")
        self.assertEqual(reloaded.audit(1)[0]["next"], "rejected")
        reloaded.decide("node", "alpha", "unreviewed", "Returned for review.")
        self.assertNotIn("node:alpha", reloaded.export_reviews()["reviews"])

    def test_rejected_records_are_excluded_from_neighbourhoods_and_paths(self) -> None:
        self.store.decide("node", "bravo", "rejected", "Not supported.")
        graph = self.store.graph_slice(focus="alpha", depth=2)
        self.assertEqual([node["id"] for node in graph["nodes"]], ["alpha"])
        self.assertIsNone(self.store.shortest_path("Alpha", "Charlie"))
        self.assertEqual(self.store.item("node", "alpha")["neighbours"], [])

    def test_merge_retains_distinct_evidence_and_deduplicates_exact_assertions(self) -> None:
        incoming = {
            "directed": True,
            "multigraph": True,
            "nodes": [
                {"id": "alpha", "label": "Alpha updated"},
                {"id": "delta", "label": "Delta"},
            ],
            "links": [
                {
                    "assertion_id": "a-4",
                    "source": "alpha",
                    "target": "delta",
                    "relation": "DEPENDS_ON",
                    "source_text": "Distinct evidence.",
                },
                {
                    "assertion_id": "a-4",
                    "source": "alpha",
                    "target": "delta",
                    "relation": "DEPENDS_ON",
                    "source_text": "Duplicate representation.",
                },
            ],
        }
        summary = self.store.import_graph(incoming, mode="merge")
        self.assertEqual(summary["nodes"], 4)
        self.assertEqual(summary["relationships"], 4)
        self.assertEqual(self.store.item("node", "alpha")["label"], "Alpha updated")

    def test_changed_node_merge_invalidates_its_review(self) -> None:
        self.store.decide("node", "alpha", "accepted", "Verified original record.")
        incoming = {
            "nodes": [{"id": "alpha", "label": "Alpha changed", "type": "Organisation"}],
            "links": [],
        }

        self.store.import_graph(incoming, mode="merge")

        self.assertEqual(self.store.item("node", "alpha")["status"], "unreviewed")
        self.assertNotIn("node:alpha", self.store.export_reviews()["reviews"])
        invalidation = self.store.audit(1)[0]
        self.assertEqual(invalidation["action"], "review_invalidated")
        self.assertEqual(invalidation["item_id"], "alpha")
        self.assertEqual(invalidation["previous"], "accepted")

    def test_unchanged_node_merge_retains_its_review(self) -> None:
        self.store.decide("node", "alpha", "accepted", "Verified original record.")
        incoming = {"nodes": [fixture_graph()["nodes"][0]], "links": []}

        self.store.import_graph(incoming, mode="merge")

        self.assertEqual(self.store.item("node", "alpha")["status"], "accepted")

    def test_client_import_cannot_assert_review_or_host_trust(self) -> None:
        incoming = fixture_graph()
        incoming["nodes"][0].update(
            {
                "host_derived": True,
                "review_status": "accepted",
                "review_reason": "Client supplied",
            }
        )
        incoming["links"][0].update(
            {
                "host_derived": True,
                "review_status": "accepted",
                "review_reason": "Client supplied",
            }
        )

        self.store.import_graph(incoming, mode="replace")

        node = self.store.item("node", "alpha")
        edge_id = next(
            edge_id
            for edge_id, edge in self.store._edges.items()
            if edge.get("assertion_id") == "a-1"
        )
        edge = self.store.item("edge", edge_id)
        self.assertEqual(node["status"], "unreviewed")
        self.assertFalse(node["host_derived"])
        self.assertNotIn("review_status", node["metadata"])
        self.assertNotIn("review_reason", node["metadata"])
        self.assertEqual(edge["status"], "unreviewed")
        self.assertFalse(edge["host_derived"])
        self.assertNotIn("review_status", edge["metadata"])
        self.assertNotIn("review_reason", edge["metadata"])

    def test_trusted_extraction_import_may_retain_host_provenance_only(self) -> None:
        incoming = fixture_graph()
        incoming["nodes"][0].update(
            {"host_derived": True, "review_status": "rejected", "review_reason": "supplied"}
        )

        self.store.import_graph(
            incoming, mode="replace", allow_host_derived=True
        )

        item = self.store.item("node", "alpha")
        self.assertTrue(item["host_derived"])
        self.assertEqual(item["status"], "accepted")
        self.assertNotIn("review_status", item["metadata"])
        self.assertNotIn("review_reason", item["metadata"])

    def test_replace_resets_decisions_and_uses_unique_backups(self) -> None:
        self.store.decide("node", "alpha", "accepted", "Verified.")
        replacement = fixture_graph()
        replacement["graph"]["name"] = "Replacement"
        self.store.import_graph(replacement, mode="replace")
        self.store.import_graph(replacement, mode="replace")
        self.assertEqual(self.store.item("node", "alpha")["status"], "unreviewed")
        backups = list((self.state_path.parent / "backups").glob("*.json"))
        self.assertEqual(len(backups), 2)

    def test_invalid_state_is_sanitised(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "reviews": {
                        "node:alpha": None,
                        "node:bravo": {"decision": "bogus"},
                        "edge:missing": {"decision": "accepted"},
                    },
                    "audit": [None, {"action": "valid"}],
                }
            ),
            encoding="utf-8",
        )
        reloaded = GraphStore(self.graph_path, self.state_path)
        self.assertEqual(reloaded.summary()["review"]["unreviewed"], 6)
        self.assertEqual(reloaded.export_reviews()["reviews"], {})


class ExtractionJobPersistenceTests(TemporaryStoreMixin, unittest.TestCase):
    def test_completed_job_history_survives_restart(self) -> None:
        job_dir = self.root / "runtime" / "extraction-jobs"
        job_dir.mkdir(parents=True)
        record = {
            "id": "persisted-job",
            "filename": "packet.pdf",
            "status": "completed",
            "created_at": "2026-08-13T12:00:00+00:00",
            "updated_at": "2026-08-13T12:05:00+00:00",
            "message": "Completed.",
            "result": {"nodes_added_or_updated": 4},
        }
        (job_dir / "persisted-job.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        jobs = ExtractionJobs(self.store, self.root / "runtime")
        self.addCleanup(jobs.close)
        self.assertEqual(jobs.get("persisted-job")["result"], record["result"])

    def test_interrupted_job_fails_explicitly_after_restart(self) -> None:
        job_dir = self.root / "runtime" / "extraction-jobs"
        job_dir.mkdir(parents=True)
        record = {
            "id": "interrupted-job",
            "filename": "packet.pdf",
            "status": "running",
            "created_at": "2026-08-13T12:00:00+00:00",
            "updated_at": "2026-08-13T12:01:00+00:00",
            "message": "Running.",
            "result": None,
        }
        path = job_dir / "interrupted-job.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        jobs = ExtractionJobs(self.store, self.root / "runtime")
        self.addCleanup(jobs.close)
        restored = jobs.get("interrupted-job")
        self.assertEqual(restored["status"], "failed")
        self.assertIn("restarted", restored["message"])
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["status"], "failed"
        )


def provenance_graph() -> dict:
    """A graph carrying source documents the way the extraction pipelines do."""
    return {
        "directed": True,
        "multigraph": True,
        "graph": {"name": "Provenance graph"},
        "nodes": [
            {
                "id": "document-1",
                "label": "alpha-report.pdf",
                "type": "source document",
                "record_kind": "SOURCE_DOCUMENT",
                "source_document": "alpha-report.pdf",
                "document_id": "document-1",
                "media_type": "pdf",
                "page_count": 12,
                "ingested_at": "2026-08-13T09:00:00+00:00",
                "host_derived": True,
            },
            {
                "id": "mention-1",
                "label": "Substation Alpha",
                "type": "Target-system component",
                "record_kind": "MENTION",
                "source_document": "alpha-report.pdf",
                "source_page": 4,
                "source_excerpt": "Substation Alpha performs the distribution function.",
            },
            # A node that names its documents through the list form instead.
            {
                "id": "mention-2",
                "label": "Control Centre Bravo",
                "type": "Target-system component",
                "record_kind": "MENTION",
                "sources": ["alpha-report.pdf", "bravo-note.txt"],
            },
        ],
        "links": [
            {
                "source": "document-1",
                "target": "mention-1",
                "relation": "core_rel:hasMention",
                "host_derived": True,
                "source_document": "alpha-report.pdf",
            },
            {
                "source": "mention-1",
                "target": "mention-2",
                "relation": "DEPENDS_ON",
                "source_document": "alpha-report.pdf",
            },
        ],
    }


class SourceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        graph_path = root / "graph.json"
        graph_path.write_text(json.dumps(provenance_graph()), encoding="utf-8")
        self.store = GraphStore(graph_path, root / "runtime" / "review_state.json")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_documents_are_listed_with_counts(self) -> None:
        documents = {row["name"]: row for row in self.store.documents()}
        self.assertEqual(set(documents), {"alpha-report.pdf", "bravo-note.txt"})
        alpha = documents["alpha-report.pdf"]
        self.assertEqual(alpha["nodes"], 3)
        self.assertEqual(alpha["relationships"], 2)
        self.assertEqual(alpha["media_type"], "pdf")
        self.assertEqual(alpha["page_count"], 12)

    def test_both_provenance_shapes_are_read(self) -> None:
        # source_document on one record, a sources list on another.
        names = {row["name"] for row in self.store.documents()}
        self.assertIn("bravo-note.txt", names)

    def test_review_queue_scopes_to_a_document(self) -> None:
        scoped = self.store.review_queue(document="bravo-note.txt", status="unreviewed")
        self.assertEqual([row["id"] for row in scoped["items"]], ["mention-2"])

    def test_document_scope_is_case_insensitive(self) -> None:
        scoped = self.store.review_queue(document="ALPHA-REPORT.PDF", status="unreviewed")
        self.assertTrue(scoped["total"])

    def test_host_derived_records_are_not_queued_for_review(self) -> None:
        queued = {row["id"] for row in self.store.review_queue(status="unreviewed")["items"]}
        self.assertNotIn("document-1", queued)
        self.assertIn("mention-1", queued)

    def test_host_derived_records_count_as_accepted(self) -> None:
        self.assertEqual(self.store.item("node", "document-1")["status"], "accepted")
        self.assertTrue(self.store.item("node", "document-1")["host_derived"])

    def test_accepted_export_retains_host_provenance(self) -> None:
        self.store.decide("node", "mention-1", "accepted", "test")
        exported = self.store.export_graph(statuses={"accepted"})
        node_ids = {node["id"] for node in exported["nodes"]}
        self.assertIn("document-1", node_ids)
        self.assertTrue(
            all(
                link["source"] in node_ids and link["target"] in node_ids
                for link in exported["links"]
            )
        )

    def test_item_reports_its_source_documents_and_excerpt(self) -> None:
        item = self.store.item("node", "mention-1")
        self.assertEqual(item["source_documents"], ["alpha-report.pdf"])
        self.assertIn("performs the distribution function", item["evidence"])

    def test_summary_includes_documents(self) -> None:
        self.assertTrue(self.store.summary()["documents"])


class QueryEngineTests(TemporaryStoreMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = QueryEngine(self.store)

    def test_counts_types_connectivity_and_path(self) -> None:
        self.assertIn("3 nodes", self.engine.ask("How many nodes are there?")["answer"])
        self.assertIn("ENABLES", self.engine.ask("What relationship types exist?")["answer"])
        self.assertIn("not criticality", self.engine.ask("Which nodes are most connected?")["answer"])
        path = self.engine.ask("How is Alpha connected to Charlie?")
        self.assertIn("Alpha → Bravo → Charlie", path["answer"])
        self.assertEqual(len(path["edges"]), 2)

    def test_relationship_type_multihop_fallback_when_no_second_entity_resolves(self) -> None:
        # "LOCATED_AT" is a relationship type, not a node, so the two-node path
        # lookup fails and the engine should fall back to a multi-hop search
        # for the nearest LOCATED_AT relationship reachable from Alpha.
        response = self.engine.ask("How is Alpha connected to LOCATED_AT?")
        self.assertIn("LOCATED_AT", response["answer"])
        self.assertIn("2 hop", response["answer"])
        self.assertEqual(len(response["edges"]), 1)
        self.assertEqual(response["edges"][0]["source"]["id"], "bravo")
        self.assertEqual(response["edges"][0]["target"]["id"], "charlie")

    def test_rejected_evidence_is_not_returned(self) -> None:
        edge_id = next(
            edge_id
            for edge_id, edge in self.store._edges.items()
            if edge["assertion_id"] == "a-3"
        )
        self.store.decide("edge", edge_id, "rejected", "Unsupported.")
        response = self.engine.ask('What is connected to "Bravo"?')
        self.assertNotIn("Charlie", response["answer"])
        self.assertTrue(all(edge["id"] != edge_id for edge in response["edges"]))

    def test_empty_and_unknown_questions(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.ask("")
        self.assertIn("could not resolve", self.engine.ask("Unrecognised subject")["answer"])


class GraphRAGRetrievalTests(TemporaryStoreMixin, unittest.TestCase):
    """Retrieval is deterministic and runs without the optional LangChain stack."""

    def setUp(self) -> None:
        super().setUp()
        self.engine = GraphRAGEngine(self.store)

    def test_focus_resolution_and_bounded_context(self) -> None:
        self.assertEqual(self.engine.resolve_focus("What does Alpha enable?")[0], "alpha")
        self.assertEqual(
            self.engine.resolve_focus('How is "Alpha" connected to "Charlie"?')[:2],
            ["alpha", "charlie"],
        )
        retrieval = self.engine.retrieve('How is "Alpha" connected to "Charlie"?')
        self.assertEqual(retrieval["path_labels"], ["Alpha", "Bravo", "Charlie"])
        context = self.engine._context(retrieval)
        self.assertIn("(Alpha) -[ENABLES]-> (Bravo)", context)
        self.assertIn("Alpha enables Bravo.", context)
        self.assertIn("status=unreviewed", context)

    def test_relation_hop_is_retrieved_for_a_single_focus_entity(self) -> None:
        # Only "Alpha" resolves as an entity; "LOCATED_AT" names a relationship
        # type that sits two hops out (Alpha -ENABLES-> Bravo -LOCATED_AT->
        # Charlie), not on Alpha's own edges, so this exercises the multi-hop
        # relation search rather than the plain depth-based neighbourhood.
        retrieval = self.engine.retrieve("Alpha LOCATED_AT")
        self.assertIsNotNone(retrieval["relation_hop"])
        self.assertEqual(retrieval["relation_hop"]["relation"], "LOCATED_AT")
        self.assertEqual(retrieval["relation_hop"]["hops"], 2)
        self.assertTrue(
            any(
                triple["source"]["id"] == "bravo" and triple["target"]["id"] == "charlie"
                for triple in retrieval["triples"]
            )
        )
        self.assertIn("-[LOCATED_AT]->", self.engine._context(retrieval))

    def test_rejected_records_never_reach_the_prompt(self) -> None:
        edge_id = next(
            edge_id
            for edge_id, edge in self.store._edges.items()
            if edge["assertion_id"] == "a-3"
        )
        self.store.decide("edge", edge_id, "rejected", "Unsupported.")
        retrieval = self.engine.retrieve('What is connected to "Bravo"?')
        self.assertTrue(all(row["id"] != edge_id for row in retrieval["triples"]))
        self.assertNotIn("LOCATED_AT", self.engine._context(retrieval))

        self.store.decide("node", "charlie", "rejected", "Out of scope.")
        self.assertEqual(self.engine.resolve_focus("What is Charlie linked to?"), [])
        self.assertNotIn("Charlie", self.engine._context(self.engine.retrieve("Charlie")))

    def test_context_respects_the_character_budget(self) -> None:
        retrieval = self.engine.retrieve("Alpha")
        self.assertGreater(len(retrieval["triples"]), 1)
        context = self.engine._context(retrieval, max_chars=160)
        self.assertLessEqual(len(context), 160)
        self.assertLess(len(context.splitlines()), len(retrieval["triples"]))
        self.assertTrue(retrieval["truncated"])

    def test_empty_question_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.ask("   ")

    def test_missing_langchain_is_reported_as_optional_setup(self) -> None:
        status = self.engine.dependency_status()
        self.assertIn("langchain_openai", status["modules"])
        self.assertEqual(status["default_mode"], "graphrag")
        with mock.patch.dict(os.environ, {"KG_QUERY_MODE": "deterministic"}):
            self.assertEqual(self.engine.default_mode(), "deterministic")
        with mock.patch.dict(os.environ, {"KG_QUERY_MODE": "nonsense"}):
            self.assertEqual(self.engine.default_mode(), "graphrag")


@unittest.skipUnless(LANGCHAIN_AVAILABLE, "The optional LangChain toolchain is not installed.")
class GraphRAGGenerationTests(TemporaryStoreMixin, unittest.TestCase):
    """Generation is exercised against a stub chat model, never a live endpoint."""

    def setUp(self) -> None:
        super().setUp()
        from langchain_core.runnables import RunnableLambda

        self.prompts: list[str] = []

        def respond(prompt_value):
            self.prompts.append(prompt_value.to_string())
            return "Alpha enables Bravo."

        self.engine = GraphRAGEngine(
            self.store, llm_factory=lambda: RunnableLambda(respond)
        )

    def test_answer_carries_evidence_and_a_trace(self) -> None:
        response = self.engine.ask('What does "Alpha" enable?')
        self.assertEqual(response["mode"], "graphrag")
        self.assertEqual(response["answer"], "Alpha enables Bravo.")
        self.assertIn("GraphRAG:", response["trace"])
        self.assertGreater(response["retrieval"]["relationships"], 0)
        self.assertTrue(any(node["id"] == "alpha" for node in response["nodes"]))
        prompt = self.prompts[0]
        self.assertIn("<graph-data>", prompt)
        self.assertIn("untrusted data", prompt)
        self.assertIn("-[ENABLES]->", prompt)

    def test_provider_failure_becomes_a_runtime_error(self) -> None:
        def failing_factory():
            raise ConnectionError("connection refused")

        engine = GraphRAGEngine(self.store, llm_factory=failing_factory)
        with self.assertRaises(RuntimeError):
            engine.ask("What does Alpha enable?")


class HTTPApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        graph_path = cls.root / "graph.json"
        graph_path.write_text(json.dumps(fixture_graph()), encoding="utf-8")
        cls.application = Application(graph_path, cls.root / "runtime" / "state.json")
        cls.registry = ProjectRegistry(
            cls.application,
            "Default",
            cls.root / "runtime" / "projects",
            None,
            application_factory=Application,
        )
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(cls.registry, WEB_ROOT)
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.registry.close_all()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        result = response.status, {k.lower(): v for k, v in response.getheaders()}, data
        connection.close()
        return result

    def json_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ):
        status, headers, body = self.request(method, path, payload, headers)
        return status, headers, json.loads(body.decode("utf-8"))

    def test_health_summary_review_search_graph_and_query_routes(self) -> None:
        for path in (
            "/api/health",
            "/api/summary",
            "/api/review",
            "/api/search?q=Alpha",
            "/api/graph?focus=alpha",
            "/api/audit",
        ):
            status, _, _ = self.json_request("GET", path)
            self.assertEqual(status, 200, path)
        status, _, answer = self.json_request(
            "POST",
            "/api/query",
            {"question": "How many entities are there?", "mode": "deterministic"},
        )
        self.assertEqual(status, 200)
        self.assertIn("3 nodes", answer["answer"])

    def test_query_mode_routing_and_reasoning_status(self) -> None:
        _, _, health = self.json_request("GET", "/api/health")
        self.assertIn(health["reasoning"]["default_mode"], health["reasoning"]["modes"])
        status, _, _ = self.json_request(
            "POST", "/api/query", {"question": "How many nodes?", "mode": "nonsense"}
        )
        self.assertEqual(status, 400)
        status, _, answer = self.json_request(
            "POST", "/api/query", {"question": "How many nodes?", "mode": "deterministic"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(answer["mode"], "deterministic")

        # Point the local endpoint at a closed port so routing is asserted without
        # depending on whether a model server happens to be running.
        with mock.patch.dict(os.environ, {"KG_LM_STUDIO_URL": "http://127.0.0.1:1/v1"}):
            _, _, offline = self.json_request("GET", "/api/health")
            self.assertFalse(offline["reasoning"]["endpoint_reachable"])
            status, _, answer = self.json_request(
                "POST", "/api/query", {"question": "How many nodes?", "mode": "auto"}
            )
            self.assertEqual(status, 200, "auto must fall back rather than fail")
            self.assertEqual(answer["mode"], "deterministic")
            self.assertIn("deterministic engine answered instead", answer["trace"])
            status, _, error = self.json_request(
                "POST", "/api/query", {"question": "How many nodes?", "mode": "graphrag"}
            )
            self.assertEqual(status, 503)
            expected = (
                "langchain"
                if not offline["reasoning"]["available"]
                else "no local model endpoint"
            )
            self.assertIn(expected, error["detail"].lower())

    def test_schema_topic_suggestion_route_is_review_only(self) -> None:
        before = self.application.schema.current_request()

        status, _, suggestion = self.json_request(
            "POST",
            "/api/schema/suggest",
            {
                "topic": "system components and dependencies",
                "max_entity_types": 12,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(suggestion["strategy"], "deterministic lexical match")
        self.assertFalse(suggestion["saved"])
        self.assertLessEqual(len(suggestion["matches"]["entity_types"]), 12)
        self.assertTrue(suggestion["suggestion"]["module_ids"])
        self.assertEqual(self.application.schema.current_request(), before)

        status, _, catalogue = self.json_request("GET", "/api")
        self.assertEqual(status, 200)
        self.assertIn("POST /api/schema/suggest", catalogue["endpoints"])

        status, _, error = self.json_request(
            "POST", "/api/schema/suggest", {"topic": " "}
        )
        self.assertEqual(status, 400)
        self.assertIn("two characters", error["detail"])

    def test_target_development_meta_and_job_routes(self) -> None:
        fake_jobs = mock.Mock()
        fake_jobs.status.return_value = {
            "status": "ready",
            "available": True,
            "mode": "asynchronous-model-generation",
            "model_id": "fake-model",
            "base_url": "https://model.invalid/v1",
            "provider_contract": "OpenAI-compatible chat completions",
        }
        queued = {
            "id": "job-123",
            "target_id": "alpha",
            "phase": "basic",
            "status": "queued",
            "result": None,
        }
        fake_jobs.submit.return_value = queued
        fake_jobs.list.return_value = [queued]
        fake_jobs.get.side_effect = lambda job_id: (
            queued if job_id == "job-123" else (_ for _ in ()).throw(KeyError(job_id))
        )

        with mock.patch.object(
            self.application.workbench, "target_jobs", fake_jobs
        ):
            status, _, meta = self.json_request(
                "GET", "/api/target-development/meta"
            )
            self.assertEqual(status, 200)
            self.assertEqual(meta["model_generation"]["status"], "ready")
            self.assertEqual(
                meta["model_generation"]["mode"],
                "asynchronous-model-generation",
            )

            status, _, submitted = self.json_request(
                "POST",
                "/api/target-development/jobs",
                {"target_id": "alpha", "phase": "basic", "depth": 3, "limit": 44},
            )
            self.assertEqual(status, 202)
            self.assertEqual(submitted, queued)
            fake_jobs.submit.assert_called_once_with(
                "alpha", phase="basic", depth=3, limit=44
            )

            status, _, listed = self.json_request(
                "GET", "/api/target-development/jobs?limit=2"
            )
            self.assertEqual(status, 200)
            self.assertEqual(listed["items"], [queued])
            fake_jobs.list.assert_called_once_with(2)

            status, _, fetched = self.json_request(
                "GET", "/api/target-development/jobs/job-123"
            )
            self.assertEqual(status, 200)
            self.assertEqual(fetched, queued)
            fake_jobs.get.assert_called_with("job-123")

            status, _, error = self.json_request(
                "GET", "/api/target-development/jobs/missing"
            )
            self.assertEqual(status, 404)
            self.assertIn("missing", error["detail"])

    def test_backend_service_token_protects_all_api_methods(self) -> None:
        token = "test-worker-to-backend-secret"
        authorised = {"Authorization": f"Bearer {token}"}
        with mock.patch.dict(
            os.environ, {"PRIMROSE_BACKEND_TOKEN": token}, clear=False
        ):
            status, _, error = self.json_request("GET", "/api/health")
            self.assertEqual(status, 403)
            self.assertIn("authentication failed", error["detail"].lower())

            status, _, error = self.json_request(
                "POST",
                "/api/query",
                {"question": "How many nodes?", "mode": "deterministic"},
            )
            self.assertEqual(status, 403)
            self.assertIn("authentication failed", error["detail"].lower())

            status, _, _ = self.json_request(
                "GET",
                "/api/health",
                headers={"Authorization": "Bearer wrong-secret"},
            )
            self.assertEqual(status, 403)

            status, _, health = self.json_request(
                "GET", "/api/health", headers=authorised
            )
            self.assertEqual(status, 200)
            self.assertEqual(health["status"], "ok")

            status, _, answer = self.json_request(
                "POST",
                "/api/query",
                {"question": "How many nodes?", "mode": "deterministic"},
                headers=authorised,
            )
            self.assertEqual(status, 200)
            self.assertEqual(answer["mode"], "deterministic")

            # Authentication applies to API routes, not the static local UI.
            status, _, _ = self.request("GET", "/")
            self.assertEqual(status, 200)

    def test_review_validation_item_and_exports(self) -> None:
        status, _, error = self.json_request(
            "POST",
            "/api/review",
            {"kind": "node", "id": "alpha", "decision": "rejected", "reason": ""},
        )
        self.assertEqual(status, 400)
        self.assertIn("reason", error["detail"].lower())
        status, _, item = self.json_request(
            "POST",
            "/api/review",
            {"kind": "node", "id": "alpha", "decision": "accepted", "reason": "Verified"},
        )
        self.assertEqual(status, 200)
        status, _, fetched = self.json_request("GET", "/api/items/node/alpha")
        self.assertEqual(fetched["status"], "accepted")
        status, headers, export = self.json_request(
            "GET", "/api/export/graph?status=accepted"
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        self.assertEqual([node["id"] for node in export["nodes"]], ["alpha"])

    def test_import_errors_origin_check_and_missing_routes(self) -> None:
        status, _, _ = self.json_request(
            "POST", "/api/graph/import?mode=invalid", {"graph": fixture_graph()}
        )
        self.assertEqual(status, 400)
        status, _, _ = self.json_request("GET", "/api/items/node/missing")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/missing.js")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/%2e%2e/server.py")
        self.assertEqual(status, 403)
        status, _, _ = self.json_request(
            "POST",
            "/api/query",
            {"question": "How many nodes?", "mode": "deterministic"},
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "POST",
            "/api/query",
            {"question": "How many nodes?", "mode": "deterministic"},
            headers={"Content-Type": "application/json", "Origin": "https://example.test"},
        )
        self.assertEqual(status, 403)

    def test_static_assets_head_and_security_headers(self) -> None:
        for path, expected_type in (
            ("/", "text/html"),
            ("/styles.css", "text/css"),
            ("/app.js", "javascript"),
        ):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected_type, headers["content-type"])
            self.assertIn("content-security-policy", headers)
            self.assertTrue(body)
        status, _, body = self.request("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
