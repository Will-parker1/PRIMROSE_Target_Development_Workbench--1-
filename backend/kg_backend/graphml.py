"""GraphML reader producing the node-link structure the store already validates.

The core application deliberately has no third-party runtime dependency, so this
uses the standard library rather than networkx. GraphML is a small format: a set
of <key> declarations that name and type the attributes, then <node> and <edge>
elements carrying <data key="..."> values.

Only reading is supported. A GraphML file is treated as a read-only source: the
store writes merges and imports to a JSON sidecar so the original is never
rewritten in a different format.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

NS = "{http://graphml.graphdrawing.org/xmlns}"

# Attribute names that become first-class fields on a node or link rather than
# being left as extra data. "type" on an edge is the relation in every GraphML
# writer this application has seen, including networkx's own.
_NODE_LABEL_KEYS = ("label", "name")
_EDGE_RELATION_KEYS = ("type", "relation", "label", "predicate")


class GraphMLError(ValueError):
    """The file is not GraphML this reader can use."""


def _coerce(value: str | None, attr_type: str) -> Any:
    """Apply the type each <key> declares. GraphML is typed; honouring it keeps
    stake_pct an integer and lat a float rather than strings in the UI."""
    text = (value or "").strip()
    if not text:
        return ""
    try:
        if attr_type == "boolean":
            return text.lower() in {"true", "1", "yes"}
        if attr_type in {"int", "long"}:
            return int(text)
        if attr_type in {"float", "double"}:
            return float(text)
    except ValueError:
        return text
    return text


def _read_data(element: ET.Element, keys: dict[str, tuple[str, str]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for data in element.findall(f"{NS}data"):
        declared = keys.get(data.get("key", ""))
        if not declared:
            continue
        name, attr_type = declared
        value = _coerce(data.text, attr_type)
        if value == "":   # an empty <data/> records nothing; keep it out of the UI
            continue
        values[name] = value
    return values


def read_graphml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    # ElementTree expands internal entities, so refuse documents that declare any.
    # A local analyst file has no reason to carry a DOCTYPE.
    if "<!DOCTYPE" in raw or "<!ENTITY" in raw:
        raise GraphMLError("GraphML with a DOCTYPE or entity declaration is not accepted.")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise GraphMLError(f"{path.name} is not well-formed XML: {exc}") from exc

    if not root.tag.endswith("graphml"):
        raise GraphMLError(f"{path.name} has no <graphml> root element.")

    # key id -> (attribute name, declared type)
    keys = {
        key.get("id", ""): (
            key.get("attr.name", key.get("id", "")),
            (key.get("attr.type") or "string").lower(),
        )
        for key in root.findall(f"{NS}key")
    }

    graph = root.find(f"{NS}graph")
    if graph is None:
        raise GraphMLError(f"{path.name} contains no <graph> element.")

    nodes: list[dict[str, Any]] = []
    for element in graph.findall(f"{NS}node"):
        node_id = (element.get("id") or "").strip()
        if not node_id:
            continue
        node: dict[str, Any] = {"id": node_id}
        node.update(_read_data(element, keys))
        for candidate in _NODE_LABEL_KEYS:
            if node.get(candidate):
                node["label"] = node[candidate]
                break
        node.setdefault("label", node_id)
        nodes.append(node)

    known = {node["id"] for node in nodes}
    links: list[dict[str, Any]] = []
    for element in graph.findall(f"{NS}edge"):
        source = (element.get("source") or "").strip()
        target = (element.get("target") or "").strip()
        if source not in known or target not in known:
            continue   # dangling endpoint; the store would reject the whole graph
        # GraphML edge ids are optional and often not unique; the store derives
        # its own stable edge identity, so any id here is dropped.
        data = _read_data(element, keys)
        link: dict[str, Any] = {"source": source, "target": target}
        link.update(data)
        for candidate in _EDGE_RELATION_KEYS:
            if data.get(candidate):
                link["relation"] = data[candidate]
                break
        link.setdefault("relation", "RELATED_TO")
        link.setdefault("label", link["relation"])
        links.append(link)

    return {
        "directed": (graph.get("edgedefault") or "directed") == "directed",
        "multigraph": True,
        "graph": {"source_file": path.name},
        "nodes": nodes,
        "links": links,
    }
