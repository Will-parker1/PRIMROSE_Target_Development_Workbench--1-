"""Thread-safe graph, review-state and audit storage.

The source graph remains a NetworkX node-link JSON document. Analyst decisions
are held in a small sidecar file so a review never silently rewrites source
attributes or discards a rejected candidate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .graphml import read_graphml

try:  # Optional: the slim install works with exact/substring matching alone.
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - exercised via the slim install path
    fuzz = None
    process = None

GRAPHML_SUFFIXES = {".graphml", ".xml"}


VALID_DECISIONS = {"accepted", "rejected", "unreviewed"}
VALID_KINDS = {"node", "edge"}
MAX_AUDIT_EVENTS = 100_000
FUZZY_SCORE_CUTOFF = 70


class GraphValidationError(ValueError):
    """Raised when graph input does not match node-link JSON expectations."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text or fallback


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


STOPWORDS = {
    "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "been", "between",
    "both", "but", "by", "can", "connected", "connection", "connections", "do", "does",
    "each", "entities", "entity", "find", "for", "from", "give", "graph", "has", "have",
    "how", "in", "is", "it", "its", "link", "linked", "links", "list", "many", "me",
    "most", "node", "nodes", "of", "on", "or", "path", "relationship", "relationships",
    "show", "that", "the", "their", "there", "these", "they", "this", "to", "type",
    "types", "what", "when", "where", "which", "who", "why", "with",
}


def candidate_phrases(question: str, limit: int = 40) -> list[str]:
    """Rank question fragments most-specific first, without an NLP dependency.

    Shared by the deterministic and GraphRAG query engines so both resolve
    entity and relationship-type mentions from free text the same way.
    """
    phrases: list[str] = re.findall(r"[\"']([^\"']{2,100})[\"']", question)
    words = re.findall(r"[\w'&.\-]+", question)
    for size in (4, 3, 2, 1):
        for index in range(len(words) - size + 1):
            window = words[index : index + size]
            if window[0].casefold() in STOPWORDS or window[-1].casefold() in STOPWORDS:
                continue
            phrase = " ".join(window)
            if len(phrase) >= 3 and phrase not in phrases:
                phrases.append(phrase)
    return phrases[:limit]


class GraphStore:
    """Load, query and review a node-link JSON or GraphML graph.

    A GraphML source is read-only: merges and imports are written to a JSON
    sidecar beside it (``graph.graphml`` -> ``graph.json``) so an extraction
    upload can never rewrite the source file in a different format. Once the
    sidecar exists it becomes the live graph and the GraphML is left alone.
    """

    def __init__(self, graph_path: str | Path, state_path: str | Path):
        self.graph_path = Path(graph_path).resolve()
        self.state_path = Path(state_path).resolve()
        self.source_path = self.graph_path
        if self.graph_path.suffix.lower() in GRAPHML_SUFFIXES:
            self.graph_path = self.graph_path.with_suffix(".json")
        self._lock = threading.RLock()
        self._graph: dict[str, Any] = {}
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[str, dict[str, Any]] = {}
        self._adjacency: dict[str, list[tuple[str, str]]] = {}
        self._state: dict[str, Any] = {"version": 1, "reviews": {}, "audit": []}
        self.reload()

    def live_path(self) -> Path:
        """The file the current graph was actually read from."""
        return self.graph_path if self.graph_path.exists() else self.source_path

    @staticmethod
    def validate_graph_data(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise GraphValidationError("Graph JSON must be an object.")
        nodes = data.get("nodes")
        links = data.get("links", data.get("edges"))
        if not isinstance(nodes, list) or not isinstance(links, list):
            raise GraphValidationError("Graph JSON must contain nodes[] and links[].")
        if len(nodes) > 250_000 or len(links) > 1_000_000:
            raise GraphValidationError("Graph exceeds the local application safety limit.")

        seen: set[str] = set()
        cleaned_nodes: list[dict[str, Any]] = []
        for index, raw in enumerate(nodes):
            if not isinstance(raw, dict):
                raise GraphValidationError(f"Node {index} is not an object.")
            node_id = clean_text(raw.get("id", raw.get("name", raw.get("label"))))
            if not node_id:
                raise GraphValidationError(f"Node {index} has no id, name or label.")
            if node_id in seen:
                raise GraphValidationError(f"Duplicate node id: {node_id}")
            seen.add(node_id)
            node = dict(raw)
            node["id"] = node_id
            node["label"] = clean_text(node.get("label", node.get("name")), node_id)
            cleaned_nodes.append(node)

        cleaned_links: list[dict[str, Any]] = []
        for index, raw in enumerate(links):
            if not isinstance(raw, dict):
                raise GraphValidationError(f"Link {index} is not an object.")
            source = raw.get("source")
            target = raw.get("target")
            if isinstance(source, dict):
                source = source.get("id")
            if isinstance(target, dict):
                target = target.get("id")
            source, target = clean_text(source), clean_text(target)
            if source not in seen or target not in seen:
                raise GraphValidationError(
                    f"Link {index} references an unknown endpoint: {source!r} -> {target!r}."
                )
            link = dict(raw)
            link["source"] = source
            link["target"] = target
            link["relation"] = clean_text(
                link.get("relation", link.get("label", link.get("predicate"))),
                "RELATED_TO",
            )
            cleaned_links.append(link)

        return {
            "directed": bool(data.get("directed", True)),
            "multigraph": bool(data.get("multigraph", False)),
            "graph": data.get("graph") if isinstance(data.get("graph"), dict) else {},
            "nodes": cleaned_nodes,
            "links": cleaned_links,
        }

    @staticmethod
    def _edge_identity(link: dict[str, Any]) -> str:
        """Return a stable assertion identity, retaining provenance distinctions."""
        explicit = clean_text(
            link.get("assertion_id", link.get("id", link.get("key")))
        )
        if explicit:
            identity: Any = {
                "source": link.get("source"),
                "relation": link.get("relation"),
                "target": link.get("target"),
                "explicit_id": explicit,
            }
        else:
            ignored = {"id", "review_status", "review_reason"}
            identity = {
                key: value
                for key, value in link.items()
                if key not in ignored
            }
        return json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _edge_identifier(cls, link: dict[str, Any], occurrence: int) -> str:
        material = json.dumps(
            [cls._edge_identity(link), occurrence],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "edge-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def reload(self) -> None:
        with self._lock:
            # The JSON sidecar wins once it exists: it holds everything merged
            # into the graph since the GraphML was first loaded.
            if self.graph_path.exists():
                raw = json.loads(self.graph_path.read_text(encoding="utf-8"))
            elif self.source_path.exists():
                raw = read_graphml(self.source_path)
            else:
                raise FileNotFoundError(f"Graph file not found: {self.source_path}")
            self._graph = self.validate_graph_data(raw)
            self._reindex()
            self._load_state()

    def _reindex(self) -> None:
        self._nodes = {node["id"]: node for node in self._graph["nodes"]}
        self._edges = {}
        self._adjacency = {node_id: [] for node_id in self._nodes}
        occurrences: Counter[str] = Counter()
        for link in self._graph["links"]:
            identity = self._edge_identity(link)
            occurrence = occurrences[identity]
            occurrences[identity] += 1
            edge_id = self._edge_identifier(link, occurrence)
            indexed = dict(link)
            indexed["id"] = edge_id
            self._edges[edge_id] = indexed
            self._adjacency[link["source"]].append((link["target"], edge_id))
            self._adjacency[link["target"]].append((link["source"], edge_id))

    def _load_state(self) -> None:
        if not self.state_path.exists():
            self._state = {"version": 1, "reviews": {}, "audit": []}
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        raw_reviews = state.get("reviews") if isinstance(state.get("reviews"), dict) else {}
        reviews: dict[str, dict[str, Any]] = {}
        for key, record in raw_reviews.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            kind, separator, item_id = key.partition(":")
            decision = clean_text(record.get("decision")).lower()
            if (
                not separator
                or kind not in VALID_KINDS
                or not self._item_exists(kind, item_id)
                or decision not in {"accepted", "rejected"}
            ):
                continue
            reviews[key] = {
                "decision": decision,
                "reason": clean_text(record.get("reason")),
                "analyst": clean_text(record.get("analyst"), "Local analyst"),
                "updated_at": record.get("updated_at"),
            }
        audit = state.get("audit") if isinstance(state.get("audit"), list) else []
        audit = [row for row in audit if isinstance(row, dict)]
        self._state = {
            "version": 1,
            "reviews": reviews,
            "audit": audit[-MAX_AUDIT_EVENTS:],
        }

    def _save_state(self) -> None:
        atomic_json_write(self.state_path, self._state)

    def _review_key(self, kind: str, item_id: str) -> str:
        if kind not in VALID_KINDS:
            raise KeyError(f"Unknown review kind: {kind}")
        return f"{kind}:{item_id}"

    def review_record(self, kind: str, item_id: str) -> dict[str, Any]:
        key = self._review_key(kind, item_id)
        return self._state["reviews"].get(
            key,
            {"decision": "unreviewed", "reason": "", "updated_at": None},
        )

    def _item_exists(self, kind: str, item_id: str) -> bool:
        return item_id in (self._nodes if kind == "node" else self._edges)

    def decide(
        self,
        kind: str,
        item_id: str,
        decision: str,
        reason: str = "",
        analyst: str = "Local analyst",
    ) -> dict[str, Any]:
        decision = clean_text(decision).lower()
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Decision must be one of {sorted(VALID_DECISIONS)}.")
        reason = clean_text(reason)
        if decision == "rejected" and not reason:
            raise ValueError("A reason is required when rejecting a candidate.")
        with self._lock:
            if kind not in VALID_KINDS or not self._item_exists(kind, item_id):
                raise KeyError(f"Unknown {kind} item: {item_id}")
            key = self._review_key(kind, item_id)
            previous = self.review_record(kind, item_id)
            current = {
                "decision": decision,
                "reason": reason,
                "analyst": clean_text(analyst, "Local analyst"),
                "updated_at": utc_now(),
            }
            if decision == "unreviewed":
                self._state["reviews"].pop(key, None)
            else:
                self._state["reviews"][key] = current
            self._state["audit"].append(
                {
                    "timestamp": current["updated_at"],
                    "action": "review_decision",
                    "kind": kind,
                    "item_id": item_id,
                    "previous": previous.get("decision", "unreviewed"),
                    "next": decision,
                    "reason": current["reason"],
                    "analyst": current["analyst"],
                }
            )
            self._state["audit"] = self._state["audit"][-MAX_AUDIT_EVENTS:]
            self._save_state()
            return self.item(kind, item_id)

    @staticmethod
    def _infer_type(node: dict[str, Any]) -> str:
        explicit = clean_text(node.get("type", node.get("entity_type")))
        if explicit:
            return explicit
        title = clean_text(node.get("title"))
        if ":" in title:
            return clean_text(title.split(":", 1)[0], "Entity")
        return "Entity"

    @staticmethod
    def _is_host_derived(record: dict[str, Any]) -> bool:
        """Provenance the host created, rather than a candidate from a model.

        Source-document records and the scaffolding edges of a reified
        assertion are in this class: the host knows what it read and what it
        built, so there is no analyst judgement to make.
        """
        value = record.get("host_derived")
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() == "true"

    def _status(self, kind: str, item_id: str) -> str:
        decision = self.review_record(kind, item_id)["decision"]
        if decision != "unreviewed":
            return decision
        raw = (self._nodes if kind == "node" else self._edges).get(item_id)
        # Host-created provenance is accepted on arrival, so it stays out of the
        # review queue, keeps exports connected and does not hold review
        # completion below 100%. An analyst may still override it explicitly.
        if raw is not None and self._is_host_derived(raw):
            return "accepted"
        return decision

    def item(self, kind: str, item_id: str) -> dict[str, Any]:
        with self._lock:
            if kind == "node":
                raw = self._nodes.get(item_id)
                if raw is None:
                    raise KeyError(item_id)
                record = self.review_record(kind, item_id)
                neighbours = []
                retained_connections = [
                    (neighbour_id, edge_id)
                    for neighbour_id, edge_id in self._adjacency.get(item_id, [])
                    if self._status("edge", edge_id) != "rejected"
                    and self._status("node", neighbour_id) != "rejected"
                ]
                for neighbour_id, edge_id in retained_connections[:20]:
                    edge = self._edges[edge_id]
                    neighbours.append(
                        {
                            "id": neighbour_id,
                            "label": self._nodes[neighbour_id]["label"],
                            "relation": edge["relation"],
                            "edge_id": edge_id,
                        }
                    )
                return {
                    "kind": "node",
                    "id": item_id,
                    "label": raw["label"],
                    "type": self._infer_type(raw),
                    "status": self._status("node", item_id),
                    "host_derived": self._is_host_derived(raw),
                    "reason": record.get("reason", ""),
                    "updated_at": record.get("updated_at"),
                    "degree": len(retained_connections),
                    # source_excerpt is the schema-guided pipeline's window of
                    # surrounding source; it is preferred over the node title,
                    # which is only a type label.
                    "evidence": clean_text(
                        raw.get(
                            "source_excerpt",
                            raw.get("excerpt", raw.get("source_text", raw.get("title"))),
                        ),
                        "No source excerpt was supplied with this node.",
                    ),
                    "source_documents": self._document_names(raw),
                    "metadata": copy.deepcopy(raw),
                    "neighbours": neighbours,
                }
            if kind == "edge":
                raw = self._edges.get(item_id)
                if raw is None:
                    raise KeyError(item_id)
                record = self.review_record(kind, item_id)
                source_label = self._nodes[raw["source"]]["label"]
                target_label = self._nodes[raw["target"]]["label"]
                return {
                    "kind": "edge",
                    "id": item_id,
                    "label": f"{source_label} — {raw['relation']} — {target_label}",
                    "type": raw["relation"],
                    "source": {"id": raw["source"], "label": source_label},
                    "target": {"id": raw["target"], "label": target_label},
                    "relation": raw["relation"],
                    "status": self._status("edge", item_id),
                    "host_derived": self._is_host_derived(raw),
                    "reason": record.get("reason", ""),
                    "updated_at": record.get("updated_at"),
                    "evidence": clean_text(
                        raw.get(
                            "source_excerpt",
                            raw.get("excerpt", raw.get("source_text", raw.get("title"))),
                        ),
                        "No source excerpt was supplied with this relationship.",
                    ),
                    "source_documents": self._document_names(raw),
                    "confidence": raw.get(
                        "relationshipConfidence", raw.get("confidence")
                    ),
                    "metadata": {k: copy.deepcopy(v) for k, v in raw.items() if k != "id"},
                }
            raise KeyError(kind)

    def _iter_item_ids(self, kind: str) -> Iterable[tuple[str, str]]:
        if kind in {"all", "node"}:
            yield from (("node", item_id) for item_id in self._nodes)
        if kind in {"all", "edge"}:
            yield from (("edge", item_id) for item_id in self._edges)

    def review_queue(
        self,
        *,
        kind: str = "all",
        status: str = "unreviewed",
        query: str = "",
        document: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if kind not in {"all", *VALID_KINDS}:
            raise ValueError("kind must be all, node or edge")
        statuses = {part.strip().lower() for part in status.split(",") if part.strip()}
        if not statuses:
            statuses = set(VALID_DECISIONS)
        if not statuses <= VALID_DECISIONS:
            raise ValueError("Unknown review status")
        needle = clean_text(query).casefold()
        # Reviewing one document at a time is the natural unit of work: an
        # analyst judges a batch of candidates against the source they came from.
        wanted_document = clean_text(document).casefold()
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))

        with self._lock:
            matches: list[tuple[str, str]] = []
            for item_kind, item_id in self._iter_item_ids(kind):
                if self._status(item_kind, item_id) not in statuses:
                    continue
                raw = (self._nodes if item_kind == "node" else self._edges)[item_id]
                if wanted_document and wanted_document not in {
                    name.casefold() for name in self._document_names(raw)
                }:
                    continue
                if needle:
                    payload = self.item(item_kind, item_id)
                    searchable = f"{payload['label']} {payload['type']}".casefold()
                    if needle not in searchable:
                        continue
                matches.append((item_kind, item_id))

            rows = [self.item(k, i) for k, i in matches[offset : offset + limit]]
            return {
                "items": rows,
                "total": len(matches),
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < len(matches),
            }

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        needle = clean_text(query).casefold()
        if len(needle) < 2:
            return []
        limit = max(1, min(int(limit), 50))
        matches: list[tuple[int, dict[str, Any]]] = []
        with self._lock:
            for node_id, node in self._nodes.items():
                label = node["label"]
                haystack = f"{label} {self._infer_type(node)}".casefold()
                if needle in haystack:
                    rank = 0 if label.casefold().startswith(needle) else 1
                    matches.append(
                        (
                            rank,
                            {
                                "kind": "node",
                                "id": node_id,
                                "label": label,
                                "type": self._infer_type(node),
                                "status": self._status("node", node_id),
                            },
                        )
                    )
            for edge_id, edge in self._edges.items():
                relation = edge["relation"]
                if needle in relation.casefold():
                    matches.append((2, self.item("edge", edge_id)))
        matches.sort(key=lambda row: (row[0], row[1]["label"].casefold()))
        return [row[1] for row in matches[:limit]]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            node_types = Counter(self._infer_type(node) for node in self._nodes.values())
            relations = Counter(edge["relation"] for edge in self._edges.values())
            retained_relations = Counter(
                edge["relation"]
                for edge_id, edge in self._edges.items()
                if self._status("edge", edge_id) != "rejected"
                and self._status("node", edge["source"]) != "rejected"
                and self._status("node", edge["target"]) != "rejected"
            )
            decisions = Counter(
                self._status(kind, item_id) for kind, item_id in self._iter_item_ids("all")
            )
            total = len(self._nodes) + len(self._edges)
            reviewed = decisions["accepted"] + decisions["rejected"]
            top = sorted(
                (
                    {
                        "id": node_id,
                        "label": node["label"],
                        "type": self._infer_type(node),
                        "degree": sum(
                            1
                            for neighbour_id, edge_id in self._adjacency.get(node_id, [])
                            if self._status("edge", edge_id) != "rejected"
                            and self._status("node", neighbour_id) != "rejected"
                        ),
                        "status": self._status("node", node_id),
                    }
                    for node_id, node in self._nodes.items()
                    if self._status("node", node_id) != "rejected"
                ),
                key=lambda row: (-row["degree"], row["label"].casefold()),
            )[:8]
            return {
                "graph_name": clean_text(
                    self._graph.get("graph", {}).get("name"),
                    self.source_path.stem.replace("_", " ").title(),
                ),
                "nodes": len(self._nodes),
                "relationships": len(self._edges),
                "review": {
                    "total": total,
                    "accepted": decisions["accepted"],
                    "rejected": decisions["rejected"],
                    "unreviewed": decisions["unreviewed"],
                    "completion": round((reviewed / total * 100) if total else 100, 1),
                },
                "entity_types": [
                    {"name": name, "count": count}
                    for name, count in node_types.most_common(10)
                ],
                "relationship_types": [
                    {"name": name, "count": count}
                    for name, count in relations.most_common(12)
                ],
                "retained_relationship_types": [
                    {"name": name, "count": count}
                    for name, count in retained_relations.most_common(12)
                ],
                "top_connected": top,
                "documents": self.documents(limit=8),
                "updated_at": datetime.fromtimestamp(
                    self.live_path().stat().st_mtime, tz=timezone.utc
                ).replace(microsecond=0).isoformat(),
            }

    @staticmethod
    def _document_names(record: dict[str, Any]) -> list[str]:
        """Every source document a node or edge attributes itself to.

        Records carry either a single `source_document` or a `sources` list
        depending on which pipeline produced them, and a merged node can hold
        both. Reading all of them keeps the document registry complete.
        """
        names: list[str] = []
        single = clean_text(record.get("source_document", record.get("doc_id")))
        if single:
            names.append(single)
        many = record.get("sources")
        if isinstance(many, list):
            names.extend(clean_text(name) for name in many if clean_text(name))
        elif isinstance(many, str) and clean_text(many):
            names.append(clean_text(many))
        return list(dict.fromkeys(names))

    def documents(self, limit: int = 200) -> list[dict[str, Any]]:
        """The source documents the current graph was built from.

        Derived from the records rather than kept in a side table, so imported
        graphs and graphs built before document nodes existed still list their
        documents.
        """
        with self._lock:
            rows: dict[str, dict[str, Any]] = {}

            def touch(name: str) -> dict[str, Any]:
                if name not in rows:
                    rows[name] = {
                        "name": name,
                        "nodes": 0,
                        "relationships": 0,
                        "accepted": 0,
                        "rejected": 0,
                        "unreviewed": 0,
                        "document_id": "",
                        "media_type": "",
                        "page_count": 0,
                        "ingested_at": "",
                    }
                return rows[name]

            for node_id, node in self._nodes.items():
                status = self._status("node", node_id)
                for name in self._document_names(node):
                    row = touch(name)
                    row["nodes"] += 1
                    row[status] += 1
                    if node.get("record_kind") == "SOURCE_DOCUMENT":
                        # The document's own node knows more than the records
                        # that merely cite it.
                        row["document_id"] = clean_text(node.get("document_id"))
                        row["media_type"] = clean_text(node.get("media_type"))
                        row["page_count"] = node.get("page_count") or 0
                        row["ingested_at"] = clean_text(node.get("ingested_at"))
            for edge_id, edge in self._edges.items():
                status = self._status("edge", edge_id)
                for name in self._document_names(edge):
                    row = touch(name)
                    row["relationships"] += 1
                    row[status] += 1

            return sorted(
                rows.values(),
                key=lambda row: (-(row["nodes"] + row["relationships"]), row["name"].casefold()),
            )[: max(1, min(limit, 1000))]

    def graph_slice(
        self,
        *,
        focus: str = "",
        depth: int = 1,
        limit: int = 60,
        statuses: str = "accepted,unreviewed",
    ) -> dict[str, Any]:
        depth = max(1, min(int(depth), 3))
        # 150 was a neighbourhood-sized bound; the Explore overview now asks
        # for the whole graph on open, so the ceiling only needs to stop a
        # pathological request value, not the normal case.
        limit = max(5, min(int(limit), 5000))
        allowed = {part.strip().lower() for part in statuses.split(",") if part.strip()}
        allowed &= VALID_DECISIONS
        if not allowed:
            allowed = set(VALID_DECISIONS)
        with self._lock:
            focus_id = self.resolve_node(focus) if focus else None
            if focus_id and self._status("node", focus_id) not in allowed:
                focus_id = None
            selected: list[str] = []
            if focus_id:
                queue: deque[tuple[str, int]] = deque([(focus_id, 0)])
                visited = {focus_id}
                while queue and len(selected) < limit:
                    node_id, distance = queue.popleft()
                    if self._status("node", node_id) in allowed:
                        selected.append(node_id)
                    if distance >= depth:
                        continue
                    neighbours = sorted(
                        self._adjacency.get(node_id, []),
                        key=lambda row: -len(self._adjacency.get(row[0], [])),
                    )
                    for neighbour, edge_id in neighbours:
                        if (
                            neighbour in visited
                            or self._status("edge", edge_id) not in allowed
                            or self._status("node", neighbour) not in allowed
                        ):
                            continue
                        visited.add(neighbour)
                        queue.append((neighbour, distance + 1))
            else:
                seeds = [
                    node_id
                    for node_id, _ in sorted(
                        self._nodes.items(),
                        key=lambda row: -len(self._adjacency.get(row[0], [])),
                    )
                    if self._status("node", node_id) in allowed
                ]
                visited: set[str] = set()
                for seed in seeds:
                    if len(selected) >= limit:
                        break
                    if seed not in visited:
                        selected.append(seed)
                        visited.add(seed)
                    neighbours = sorted(
                        self._adjacency.get(seed, []),
                        key=lambda row: -len(self._adjacency.get(row[0], [])),
                    )
                    for neighbour, edge_id in neighbours:
                        if len(selected) >= limit:
                            break
                        if (
                            neighbour in visited
                            or self._status("edge", edge_id) not in allowed
                            or self._status("node", neighbour) not in allowed
                        ):
                            continue
                        selected.append(neighbour)
                        visited.add(neighbour)

            selected_set = set(selected)
            links = [
                edge
                for edge_id, edge in self._edges.items()
                if edge["source"] in selected_set
                and edge["target"] in selected_set
                and self._status("edge", edge_id) in allowed
            ]
            return {
                "focus_id": focus_id,
                "nodes": [
                    {
                        "id": node_id,
                        "label": self._nodes[node_id]["label"],
                        "type": self._infer_type(self._nodes[node_id]),
                        "status": self._status("node", node_id),
                        "degree": len(self._adjacency.get(node_id, [])),
                    }
                    for node_id in selected
                ],
                "links": [
                    {
                        "id": edge["id"],
                        "source": edge["source"],
                        "target": edge["target"],
                        "relation": edge["relation"],
                        "status": self._status("edge", edge["id"]),
                    }
                    for edge in links
                ],
            }

    def resolve_node(self, value: str) -> str | None:
        with self._lock:
            text = clean_text(value)
            if not text:
                return None
            if text in self._nodes:
                return text
            folded = text.casefold()
            exact = [
                node_id
                for node_id, node in self._nodes.items()
                if node["label"].casefold() == folded
            ]
            if exact:
                return exact[0]
            contains = [
                node_id
                for node_id, node in self._nodes.items()
                if folded in node["label"].casefold()
            ]
            if contains:
                return min(
                    contains, key=lambda node_id: len(self._nodes[node_id]["label"])
                )
            # Fuzzy only as a last resort, so a typo still resolves without
            # rapidfuzz being a hard requirement of the slim install.
            if fuzz is not None and len(folded) >= 3 and self._nodes:
                # token_sort_ratio is case-sensitive; without a processor,
                # "alfa" vs "Alpha" scores as if every letter mismatched.
                match = process.extractOne(
                    folded,
                    {node_id: node["label"] for node_id, node in self._nodes.items()},
                    scorer=fuzz.token_sort_ratio,
                    processor=str.lower,
                    score_cutoff=FUZZY_SCORE_CUTOFF,
                )
                if match:
                    return match[2]
            return None

    def resolve_relation(self, value: str) -> str | None:
        """Resolve free text to a relationship type actually present in the graph.

        Tried in the same order as :meth:`resolve_node`: an exact label, a
        SCREAMING_SNAKE_CASE-vs-plain-English spelling difference, a
        substring, then a fuzzy match -- so "director" reaches a ``DIRECTED``
        edge type without the caller needing to know the graph's vocabulary.
        """
        with self._lock:
            text = clean_text(value)
            if not text:
                return None
            relations = sorted({edge["relation"] for edge in self._edges.values()})
            if not relations:
                return None
            folded = text.casefold()
            for relation in relations:
                if relation.casefold() == folded:
                    return relation
            normalised = folded.replace("_", " ").replace("-", " ")
            for relation in relations:
                if relation.casefold().replace("_", " ").replace("-", " ") == normalised:
                    return relation
            contains = [relation for relation in relations if folded in relation.casefold()]
            if contains:
                return min(contains, key=len)
            if fuzz is not None and len(folded) >= 3:
                match = process.extractOne(
                    folded,
                    relations,
                    scorer=fuzz.token_sort_ratio,
                    processor=str.lower,
                    score_cutoff=FUZZY_SCORE_CUTOFF,
                )
                if match:
                    return match[0]
            return None

    def node_mentioned_in(self, text: str) -> str | None:
        folded = clean_text(text).casefold()
        with self._lock:
            candidates = [
                (len(node["label"]), node_id)
                for node_id, node in self._nodes.items()
                if node["label"].casefold() in folded
                and self._status("node", node_id) != "rejected"
            ]
        return max(candidates)[1] if candidates else None

    def shortest_path(
        self, start: str, end: str, max_depth: int = 6
    ) -> dict[str, Any] | None:
        start_id, end_id = self.resolve_node(start), self.resolve_node(end)
        if not start_id or not end_id:
            return None
        if (
            self._status("node", start_id) == "rejected"
            or self._status("node", end_id) == "rejected"
        ):
            return None
        max_depth = max(1, min(int(max_depth), 10))
        queue: deque[str] = deque([start_id])
        parent: dict[str, tuple[str, str] | None] = {start_id: None}
        while queue:
            node_id = queue.popleft()
            if node_id == end_id:
                break
            distance = 0
            cursor = node_id
            while parent[cursor] is not None:
                distance += 1
                cursor = parent[cursor][0]
            if distance >= max_depth:
                continue
            for neighbour, edge_id in self._adjacency.get(node_id, []):
                if (
                    neighbour in parent
                    or self._status("edge", edge_id) == "rejected"
                    or self._status("node", neighbour) == "rejected"
                ):
                    continue
                parent[neighbour] = (node_id, edge_id)
                queue.append(neighbour)
        if end_id not in parent:
            return None
        node_ids: list[str] = []
        edge_ids: list[str] = []
        cursor = end_id
        while cursor != start_id:
            node_ids.append(cursor)
            previous, edge_id = parent[cursor]  # type: ignore[misc]
            edge_ids.append(edge_id)
            cursor = previous
        node_ids.append(start_id)
        node_ids.reverse()
        edge_ids.reverse()
        return {
            "nodes": [self.item("node", node_id) for node_id in node_ids],
            "edges": [self.item("edge", edge_id) for edge_id in edge_ids],
        }

    def relationship_neighbourhood(
        self,
        origin: str,
        relation: str,
        *,
        ceiling: int = 6,
        statuses: str = "accepted,unreviewed",
    ) -> dict[str, Any] | None:
        """Multi-hop search from an origin node for the nearest relationships
        of a named type, reached through any intermediate relationship.

        "Who directed Brad Pitt?" has no DIRECTED edge on Brad Pitt himself:
        the path runs Brad Pitt -ACTED_IN-> Movie <-DIRECTED- Director.
        Rather than guess a fixed hop budget, this expands the neighbourhood
        one hop at a time and stops as soon as the requested relationship
        type appears, so the answer is always the closest match rather than
        an arbitrary window.
        """
        ceiling = max(1, min(int(ceiling), 10))
        allowed = {part.strip().lower() for part in statuses.split(",") if part.strip()}
        allowed &= VALID_DECISIONS
        if not allowed:
            allowed = set(VALID_DECISIONS)
        with self._lock:
            origin_id = self.resolve_node(origin)
            if not origin_id or self._status("node", origin_id) not in allowed:
                return None
            relation_id = self.resolve_relation(relation)
            if not relation_id:
                return None

            visited = {origin_id}
            frontier = [origin_id]
            collected_edges: dict[str, dict[str, Any]] = {}
            found_depth: int | None = None
            depth = 0
            while frontier and depth < ceiling:
                depth += 1
                next_frontier: list[str] = []
                for node_id in frontier:
                    for neighbour, edge_id in self._adjacency.get(node_id, []):
                        if (
                            self._status("edge", edge_id) not in allowed
                            or self._status("node", neighbour) not in allowed
                        ):
                            continue
                        collected_edges.setdefault(edge_id, self._edges[edge_id])
                        if neighbour not in visited:
                            visited.add(neighbour)
                            next_frontier.append(neighbour)
                if any(
                    edge["relation"].casefold() == relation_id.casefold()
                    for edge in collected_edges.values()
                ):
                    found_depth = depth
                    break
                frontier = next_frontier

            if found_depth is None:
                return None

            matches = [
                self.item("edge", edge["id"])
                for edge in collected_edges.values()
                if edge["relation"].casefold() == relation_id.casefold()
            ]
            return {
                "origin": self.item("node", origin_id),
                "relation": relation_id,
                "hops": found_depth,
                "matches": matches,
                "nodes": [self.item("node", node_id) for node_id in visited],
            }

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        return list(reversed(self._state["audit"][-limit:]))

    def export_graph(
        self,
        include_review: bool = True,
        statuses: set[str] | None = None,
    ) -> dict[str, Any]:
        if statuses is not None and not statuses <= VALID_DECISIONS:
            raise ValueError("Unknown export review status.")
        with self._lock:
            output = copy.deepcopy(self._graph)
            if include_review:
                for node in output["nodes"]:
                    record = self.review_record("node", node["id"])
                    # _status, not the raw decision: host-created provenance is
                    # accepted on arrival and the export must say so, or the
                    # annotation contradicts the accepted-only filter below.
                    node["review_status"] = self._status("node", node["id"])
                    if record.get("reason"):
                        node["review_reason"] = record["reason"]
                occurrences: Counter[str] = Counter()
                for link in output["links"]:
                    identity = self._edge_identity(link)
                    edge_id = self._edge_identifier(link, occurrences[identity])
                    occurrences[identity] += 1
                    record = self.review_record("edge", edge_id)
                    link["review_status"] = self._status("edge", edge_id)
                    if record.get("reason"):
                        link["review_reason"] = record["reason"]
            if statuses is not None:
                retained_nodes = {
                    node["id"]
                    for node in output["nodes"]
                    if self._status("node", node["id"]) in statuses
                }
                output["nodes"] = [
                    node for node in output["nodes"] if node["id"] in retained_nodes
                ]
                occurrence_counts: Counter[str] = Counter()
                retained_links = []
                for link in output["links"]:
                    identity = self._edge_identity(link)
                    edge_id = self._edge_identifier(link, occurrence_counts[identity])
                    occurrence_counts[identity] += 1
                    if (
                        link["source"] in retained_nodes
                        and link["target"] in retained_nodes
                        and self._status("edge", edge_id) in statuses
                    ):
                        retained_links.append(link)
                output["links"] = retained_links
            return output

    def export_reviews(self) -> dict[str, Any]:
        return copy.deepcopy(self._state)

    @staticmethod
    def _strip_managed_annotations(
        graph: dict[str, Any], *, allow_host_derived: bool
    ) -> None:
        """Remove state fields that an imported graph is not allowed to assert.

        Review decisions live in the sidecar managed by :class:`GraphStore`; a
        node-link payload can never import them.  ``host_derived`` is also a
        trust decision because those records bypass the review queue.  Only the
        in-process extraction adapter may preserve that flag for provenance
        scaffolding it created itself.
        """
        managed = {"review_status", "review_reason"}
        if not allow_host_derived:
            managed.add("host_derived")
        for record in [*graph["nodes"], *graph["links"]]:
            for field in managed:
                record.pop(field, None)

    def import_graph(
        self,
        data: Any,
        mode: str = "replace",
        *,
        allow_host_derived: bool = False,
    ) -> dict[str, Any]:
        """Validate and import a graph.

        ``allow_host_derived`` is deliberately keyword-only and false by
        default.  It is reserved for the trusted, in-process extraction path;
        API and other callers must not let a supplied payload bypass review.
        """
        cleaned = self.validate_graph_data(data)
        self._strip_managed_annotations(
            cleaned, allow_host_derived=allow_host_derived
        )
        if mode not in {"replace", "merge"}:
            raise ValueError("Import mode must be replace or merge.")
        with self._lock:
            invalidated_reviews: list[tuple[str, dict[str, Any]]] = []
            if mode == "merge":
                nodes = {node["id"]: node for node in self._graph["nodes"]}
                for node in cleaned["nodes"]:
                    previous = nodes.get(node["id"])
                    review_key = self._review_key("node", node["id"])
                    if (
                        previous is not None
                        and previous != node
                        and review_key in self._state["reviews"]
                    ):
                        invalidated_reviews.append(
                            (node["id"], dict(self._state["reviews"][review_key]))
                        )
                nodes.update({node["id"]: node for node in cleaned["nodes"]})
                signatures = {
                    self._edge_identity(link)
                    for link in self._graph["links"]
                }
                links = list(self._graph["links"])
                for link in cleaned["links"]:
                    signature = self._edge_identity(link)
                    if signature in signatures:
                        continue
                    links.append(link)
                    signatures.add(signature)
                cleaned = {
                    "directed": self._graph["directed"] or cleaned["directed"],
                    "multigraph": self._graph["multigraph"] or cleaned["multigraph"],
                    "graph": self._graph.get("graph", {}),
                    "nodes": list(nodes.values()),
                    "links": links,
                }
            backup_dir = self.state_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = backup_dir / f"knowledge_graph_{stamp}.json"
            if self.graph_path.exists():
                backup.write_bytes(self.graph_path.read_bytes())
            atomic_json_write(self.graph_path, cleaned)
            self._graph = cleaned
            self._reindex()
            if mode == "replace":
                # Decisions belong to the replaced dataset, even if a new graph
                # happens to reuse an identifier.
                self._state["reviews"] = {}
            else:
                # Do this only after the graph write succeeds: an I/O failure
                # must not invalidate a decision while leaving the old record
                # live in memory.
                for node_id, _previous in invalidated_reviews:
                    self._state["reviews"].pop(
                        self._review_key("node", node_id), None
                    )
            self._state["audit"].append(
                {
                    "timestamp": utc_now(),
                    "action": "graph_import",
                    "kind": "graph",
                    "item_id": self.graph_path.name,
                    "previous": "existing",
                    "next": mode,
                    "reason": f"Imported {len(cleaned['nodes'])} nodes and {len(cleaned['links'])} relationships",
                    "analyst": "Local analyst",
                }
            )
            for node_id, previous in invalidated_reviews:
                self._state["audit"].append(
                    {
                        "timestamp": utc_now(),
                        "action": "review_invalidated",
                        "kind": "node",
                        "item_id": node_id,
                        "previous": previous.get("decision", "unreviewed"),
                        "next": "unreviewed",
                        "reason": "The record payload changed during graph merge and requires a new review.",
                        "analyst": "System integrity control",
                    }
                )
            self._state["audit"] = self._state["audit"][-MAX_AUDIT_EVENTS:]
            self._save_state()
            return self.summary()
