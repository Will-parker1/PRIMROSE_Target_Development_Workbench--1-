"""Deterministic, evidence-returning graph questions for the local UI."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .store import GraphStore, candidate_phrases, clean_text


class QueryEngine:
    """Answer common graph questions without requiring an external LLM."""

    def __init__(self, store: GraphStore):
        self.store = store

    def ask(self, question: str) -> dict[str, Any]:
        question = clean_text(question)
        if not question:
            raise ValueError("A question is required.")
        folded = question.casefold()
        summary = self.store.summary()

        if any(phrase in folded for phrase in ("how many nodes", "how many entities")):
            return self._response(
                f"The graph contains {summary['nodes']:,} nodes.",
                "Counted all node records in the loaded graph.",
            )
        if any(
            phrase in folded
            for phrase in ("how many relationships", "how many edges", "how many links")
        ):
            return self._response(
                f"The graph contains {summary['relationships']:,} relationships.",
                "Counted all relationship records in the loaded graph.",
            )
        if "relationship type" in folded or "predicate" in folded:
            rows = summary.get("retained_relationship_types", summary["relationship_types"])[:10]
            answer = "; ".join(f"{row['name']} ({row['count']})" for row in rows)
            return self._response(
                f"The most frequent relationship types are: {answer}.",
                "Grouped relationships by their stored relation label.",
            )
        if any(phrase in folded for phrase in ("most connected", "highest degree", "central nodes")):
            rows = summary["top_connected"][:8]
            answer = "; ".join(f"{row['label']} ({row['degree']})" for row in rows)
            return self._response(
                f"The highest-degree nodes are: {answer}. Degree indicates connectivity, not criticality.",
                "Ranked nodes by undirected degree; no criticality judgement was inferred.",
                nodes=rows,
            )

        path_terms = self._path_terms(question)
        if path_terms:
            path = self.store.shortest_path(path_terms[0], path_terms[1])
            if path:
                labels = [node["label"] for node in path["nodes"]]
                return self._response(
                    "The shortest retained path is: " + " → ".join(labels) + ".",
                    "Breadth-first search over accepted and unreviewed relationships; rejected relationships were excluded.",
                    nodes=path["nodes"],
                    edges=path["edges"],
                )
            neighbourhood = self._relationship_neighbourhood(*path_terms)
            if neighbourhood:
                sample = "; ".join(
                    f"{edge['source']['label']} → {edge['target']['label']}"
                    for edge in neighbourhood["matches"][:8]
                )
                return self._response(
                    f"{neighbourhood['origin']['label']} reaches a '{neighbourhood['relation']}' "
                    f"relationship within {neighbourhood['hops']} hop(s): {sample}.",
                    f"Multi-hop search from {neighbourhood['origin']['label']!r}, expanding one "
                    f"hop at a time through accepted and unreviewed relationships until a "
                    f"'{neighbourhood['relation']}' relationship was reached.",
                    nodes=[neighbourhood["origin"]],
                    edges=neighbourhood["matches"][:12],
                )
            return self._response(
                "No path was found within the configured search depth, or one of the named nodes was not resolved.",
                f"Attempted an undirected path search between {path_terms[0]!r} and {path_terms[1]!r} while excluding rejected relationships.",
            )

        node_id = self._node_mentioned(question)
        if node_id:
            item = self.store.item("node", node_id)
            neighbours = item["neighbours"]
            relation_counts = Counter(row["relation"] for row in neighbours)
            relation_text = ", ".join(
                f"{name} ({count})" for name, count in relation_counts.most_common(6)
            )
            names = ", ".join(row["label"] for row in neighbours[:8])
            answer = (
                f"{item['label']} has {item['degree']} recorded connection(s). "
                f"The displayed neighbourhood includes {names or 'no retained neighbours'}"
            )
            if relation_text:
                answer += f". Relationship types include {relation_text}"
            answer += "."
            edges = [self.store.item("edge", row["edge_id"]) for row in neighbours[:12]]
            nodes = [item]
            nodes.extend(
                self.store.item("node", row["id"]) for row in neighbours[:12]
            )
            return self._response(
                answer,
                "Resolved the longest node label present in the question and returned its immediate neighbourhood.",
                nodes=nodes,
                edges=edges,
            )

        results = self.store.search(question, 5)
        if results:
            labels = ", ".join(row["label"] for row in results)
            return self._response(
                f"I found possible graph matches: {labels}. Select one and ask about its connections.",
                "Used case-insensitive label and relationship search.",
                nodes=[row for row in results if row.get("kind") == "node"],
            )
        return self._response(
            "I could not resolve that question to the current graph. Try naming a node, asking for counts, relationship types, the most connected nodes, or a shortest path.",
            "No exact or substring node-label match was found.",
        )

    @staticmethod
    def _response(
        answer: str,
        trace: str,
        *,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "answer": answer,
            "trace": trace,
            "nodes": nodes or [],
            "edges": edges or [],
            "notice": "Connectivity and model output are analytical aids, not accepted graph truth or criticality determinations.",
            "mode": "deterministic",
        }

    def _node_mentioned(self, question: str) -> str | None:
        mentioned = self.store.node_mentioned_in(question)
        if mentioned:
            return mentioned
        quoted = re.findall(r"[\"']([^\"']{2,100})[\"']", question)
        for value in quoted:
            resolved = self.store.resolve_node(value)
            if resolved:
                return resolved
        return None

    def _relationship_neighbourhood(self, term_a: str, term_b: str) -> dict[str, Any] | None:
        """Fall back to an entity-plus-relationship-type reading when two terms
        pulled from a "connected to"/"path between" question do not both name
        nodes: "How is Alpha connected to a LOCATED_AT relationship?" names one
        entity and a relationship type, not two entities.
        """
        for origin_phrase, relation_phrase in ((term_a, term_b), (term_b, term_a)):
            origin_id = self.store.resolve_node(origin_phrase)
            if not origin_id:
                continue
            for relation_candidate in (relation_phrase, *candidate_phrases(relation_phrase)):
                result = self.store.relationship_neighbourhood(origin_id, relation_candidate)
                if result:
                    return result
        return None

    @staticmethod
    def _path_terms(question: str) -> tuple[str, str] | None:
        if not any(
            phrase in question.casefold()
            for phrase in ("shortest path", "path between", "connected to", "connection between")
        ):
            return None
        quoted = re.findall(r"[\"']([^\"']{2,100})[\"']", question)
        if len(quoted) >= 2:
            return quoted[0], quoted[1]
        match = re.search(
            r"(?:between|from)\s+(.+?)\s+(?:and|to)\s+(.+?)(?:\?|$)",
            question,
            flags=re.IGNORECASE,
        )
        if match:
            return clean_text(match.group(1)), clean_text(match.group(2))
        match = re.search(
            r"(?:how\s+is\s+)?(.+?)\s+connected\s+to\s+(.+?)(?:\?|$)",
            question,
            flags=re.IGNORECASE,
        )
        if match:
            return clean_text(match.group(1)), clean_text(match.group(2))
        return None
