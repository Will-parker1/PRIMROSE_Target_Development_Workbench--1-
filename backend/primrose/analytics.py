"""Deterministic, read-only graph diagnostics used by the PRIMROSE adapter.

These functions intentionally use only the Python standard library.  They
operate on a copied graph export and never write attributes or review state
back into :class:`kg_backend.store.GraphStore`.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Any, Iterable


DEPENDENCY_RELATIONS = {
    "DEPENDS_ON",
    "SUPPLIES",
    "FEEDS",
    "OPERATES",
    "OWNS",
    "MAINTAINS",
    "HAS_EQUIPMENT",
    "CONTRACTING_AUTHORITY_FOR",
}

RELATION_LAYERS = {
    "DEPENDS_ON": "logistics",
    "SUPPLIES": "logistics",
    "FEEDS": "physical",
    "OPERATES": "organisational",
    "OWNS": "financial",
    "MAINTAINS": "production",
    "HAS_EQUIPMENT": "physical",
    "LOCATED_IN": "physical",
    "REGISTERED_IN": "organisational",
    "BOARD_MEMBER_OF": "organisational",
    "CEO_OF": "organisational",
    "CFO_OF": "financial",
    "HAS_OFFICER": "organisational",
    "CONTRACTING_AUTHORITY_FOR": "financial",
    "COMMANDS": "command",
    "REPORTS_TO": "command",
    "COMMUNICATES_WITH": "communications",
    "CONNECTS_TO": "communications",
    "CYBER_DEPENDS_ON": "cyber",
    "INFLUENCES": "influence",
    "TRANSPORTS": "transportation",
    "ROUTES_TO": "transportation",
}

CRITICALITY_MAP = {
    "VERY LOW": 0.15,
    "LOW": 0.30,
    "MEDIUM": 0.55,
    "HIGH": 0.78,
    "VERY HIGH": 0.92,
    "CRITICAL": 1.0,
}

_CACHE_LIMIT = 8
_BETWEENNESS_CACHE: dict[str, dict[str, float]] = {}
_BRIDGE_CACHE: dict[str, set[tuple[str, str]]] = {}
_CORE_CACHE: dict[str, dict[str, int]] = {}
_LAYER_ADJACENCY_CACHE: dict[
    tuple[str, str | None, str | None], dict[str, tuple[str, ...]]
] = {}
_LAYER_BRIDGE_CACHE: dict[tuple[str, str], set[tuple[str, str]]] = {}
_ROLE_SIGNATURE_CACHE: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
_COMPONENT_CACHE: dict[str, tuple[tuple[str, ...], ...]] = {}
_BRIDGE_FREE_COMPONENT_CACHE: dict[str, tuple[tuple[str, ...], ...]] = {}


def _remember(cache: dict[Any, Any], key: Any, value: Any) -> None:
    limit = 4096 if cache is _ROLE_SIGNATURE_CACHE else _CACHE_LIMIT * 12
    if key not in cache and len(cache) >= limit:
        cache.pop(next(iter(cache)))
    cache[key] = value


@dataclass(frozen=True)
class GraphProjection:
    node_ids: tuple[str, ...]
    nodes: dict[str, dict[str, Any]]
    links: tuple[dict[str, Any], ...]
    outgoing: dict[str, tuple[str, ...]]
    incoming: dict[str, tuple[str, ...]]
    undirected: dict[str, tuple[str, ...]]
    incident: dict[str, tuple[dict[str, Any], ...]]
    snapshot_id: str
    statuses: tuple[str, ...]


def _status(record: dict[str, Any]) -> str:
    return str(record.get("review_status", record.get("status", "unreviewed"))).casefold()


def _is_prediction(record: dict[str, Any]) -> bool:
    values = {
        str(record.get(key, "")).strip().upper()
        for key in ("record_kind", "modality", "epistemic_state", "type")
    }
    return bool(values & {"PREDICTION", "MODEL_PREDICTED", "MODEL_GENERATED_HYPOTHESIS"})


def build_projection(
    graph: dict[str, Any],
    *,
    statuses: Iterable[str] = ("accepted", "unreviewed"),
) -> GraphProjection:
    """Create a stable diagnostic projection from an exported graph copy.

    Model predictions and rejected records are excluded irrespective of the
    generic review status.  Repeated direct edges are fused for topology so
    duplicate records cannot multiply centrality.
    """

    allowed = tuple(sorted({str(value).casefold() for value in statuses}))
    nodes = {
        str(node["id"]): dict(node)
        for node in graph.get("nodes", [])
        if _status(node) in allowed and not _is_prediction(node)
    }
    fused: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for position, raw in enumerate(graph.get("links", [])):
        link = dict(raw)
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        if source not in nodes or target not in nodes:
            continue
        if _status(link) not in allowed or _is_prediction(link):
            continue
        relation = str(link.get("relation", link.get("type", "RELATED_TO"))).upper()
        key = (
            source,
            relation,
            target,
            str(link.get("polarity", "")),
            str(link.get("valid_from", "")) + "/" + str(link.get("valid_to", "")),
        )
        identifier = str(
            link.get("assertion_id")
            or link.get("id")
            or "edge-" + sha256(f"{key}:{position}".encode()).hexdigest()[:16]
        )
        if key not in fused:
            fused[key] = {
                **link,
                "id": identifier,
                "source": source,
                "target": target,
                "relation": relation,
                "assertion_ids": [identifier],
            }
        else:
            fused[key]["assertion_ids"].append(identifier)

    links = tuple(
        sorted(
            fused.values(),
            key=lambda item: (item["source"], item["relation"], item["target"], item["id"]),
        )
    )
    outgoing_sets = {node_id: set() for node_id in nodes}
    incoming_sets = {node_id: set() for node_id in nodes}
    undirected_sets = {node_id: set() for node_id in nodes}
    incident_lists: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    for link in links:
        source, target = link["source"], link["target"]
        outgoing_sets[source].add(target)
        incoming_sets[target].add(source)
        undirected_sets[source].add(target)
        undirected_sets[target].add(source)
        incident_lists[source].append(link)
        incident_lists[target].append(link)

    canonical = {
        "statuses": allowed,
        "nodes": [
            {
                "id": identifier,
                "type": str(nodes[identifier].get("type", "Entity")),
                "review_status": _status(nodes[identifier]),
            }
            for identifier in sorted(nodes)
        ],
        "links": [
            {
                "source": link["source"],
                "relation": link["relation"],
                "target": link["target"],
                "polarity": str(link.get("polarity", "")),
                "valid_from": str(link.get("valid_from", "")),
                "valid_to": str(link.get("valid_to", "")),
            }
            for link in links
        ],
    }
    snapshot = sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return GraphProjection(
        node_ids=tuple(sorted(nodes)),
        nodes=nodes,
        links=links,
        outgoing={key: tuple(sorted(value)) for key, value in outgoing_sets.items()},
        incoming={key: tuple(sorted(value)) for key, value in incoming_sets.items()},
        undirected={key: tuple(sorted(value)) for key, value in undirected_sets.items()},
        incident={
            key: tuple(
                sorted(value, key=lambda item: (item["source"], item["relation"], item["target"]))
            )
            for key, value in incident_lists.items()
        },
        snapshot_id=snapshot,
        statuses=allowed,
    )


def pagerank(
    projection: GraphProjection,
    *,
    seeds: Iterable[str] = (),
    damping: float = 0.85,
    tolerance: float = 1e-12,
    maximum_iterations: int = 100,
) -> dict[str, float]:
    identifiers = projection.node_ids
    if not identifiers:
        return {}
    count = len(identifiers)
    selected = tuple(sorted({seed for seed in seeds if seed in projection.nodes}))
    restart = {
        identifier: (1.0 / len(selected) if identifier in selected else 0.0)
        if selected
        else 1.0 / count
        for identifier in identifiers
    }
    rank = {identifier: 1.0 / count for identifier in identifiers}
    for _ in range(maximum_iterations):
        dangling = sum(rank[node_id] for node_id in identifiers if not projection.outgoing[node_id])
        updated = {
            node_id: (1.0 - damping) * restart[node_id] + damping * dangling * restart[node_id]
            for node_id in identifiers
        }
        for source in identifiers:
            neighbours = projection.outgoing[source]
            if not neighbours:
                continue
            contribution = damping * rank[source] / len(neighbours)
            for target in neighbours:
                updated[target] += contribution
        delta = sum(abs(updated[node_id] - rank[node_id]) for node_id in identifiers)
        rank = updated
        if delta < tolerance:
            break
    total = sum(rank.values()) or 1.0
    return {node_id: rank[node_id] / total for node_id in identifiers}


def reachable(
    adjacency: dict[str, tuple[str, ...]],
    start: str,
    *,
    depth: int | None = None,
    excluded_node: str = "",
    excluded_edge: tuple[str, str] | None = None,
) -> set[str]:
    if start == excluded_node or start not in adjacency:
        return set()
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        current, distance = queue.popleft()
        if depth is not None and distance >= depth:
            continue
        for neighbour in adjacency[current]:
            if neighbour == excluded_node:
                continue
            if excluded_edge and {current, neighbour} == set(excluded_edge):
                continue
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, distance + 1))
    seen.discard(start)
    return seen


def articulation_points(projection: GraphProjection) -> set[str]:
    """Return cut vertices using deterministic Tarjan low-link traversal."""

    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    result: set[str] = set()
    clock = 0

    def visit(node_id: str) -> None:
        nonlocal clock
        clock += 1
        discovery[node_id] = low[node_id] = clock
        child_count = 0
        for neighbour in projection.undirected[node_id]:
            if neighbour not in discovery:
                parent[neighbour] = node_id
                child_count += 1
                visit(neighbour)
                low[node_id] = min(low[node_id], low[neighbour])
                if parent[node_id] is None and child_count > 1:
                    result.add(node_id)
                if parent[node_id] is not None and low[neighbour] >= discovery[node_id]:
                    result.add(node_id)
            elif neighbour != parent[node_id]:
                low[node_id] = min(low[node_id], discovery[neighbour])

    for identifier in projection.node_ids:
        if identifier not in discovery:
            parent[identifier] = None
            visit(identifier)
    return result


def _bridge_pairs(adjacency: dict[str, tuple[str, ...]]) -> set[tuple[str, str]]:
    """Return canonical endpoint pairs that are bridges in a simple graph."""

    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    result: set[tuple[str, str]] = set()
    clock = 0

    def visit(node_id: str) -> None:
        nonlocal clock
        clock += 1
        discovery[node_id] = low[node_id] = clock
        for neighbour in adjacency.get(node_id, ()):
            if neighbour not in discovery:
                parent[neighbour] = node_id
                visit(neighbour)
                low[node_id] = min(low[node_id], low[neighbour])
                if low[neighbour] > discovery[node_id]:
                    result.add(tuple(sorted((node_id, neighbour))))
            elif neighbour != parent[node_id]:
                low[node_id] = min(low[node_id], discovery[neighbour])

    for identifier in sorted(adjacency):
        if identifier not in discovery:
            parent[identifier] = None
            visit(identifier)
    return result


def bridge_edges(projection: GraphProjection) -> set[tuple[str, str]]:
    """Return bridge endpoint pairs on the undirected structural projection.

    This is deliberately an endpoint-pair diagnostic.  It does not claim that
    removal of one assertion, physical link or capacity unit causes failure.
    """

    cached = _BRIDGE_CACHE.get(projection.snapshot_id)
    if cached is None:
        cached = _bridge_pairs(projection.undirected)
        _remember(_BRIDGE_CACHE, projection.snapshot_id, cached)
    return set(cached)


def connected_components(
    adjacency: dict[str, tuple[str, ...]],
    *,
    excluded_node: str = "",
    excluded_edges: set[tuple[str, str]] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """Return stable connected components under explicit exclusions."""

    excluded_edges = excluded_edges or set()
    remaining = {node_id for node_id in adjacency if node_id != excluded_node}
    components: list[tuple[str, ...]] = []
    while remaining:
        start = min(remaining)
        seen = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbour in adjacency.get(current, ()):
                if neighbour == excluded_node:
                    continue
                if tuple(sorted((current, neighbour))) in excluded_edges:
                    continue
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        remaining.difference_update(seen)
        components.append(tuple(sorted(seen)))
    return tuple(sorted(components, key=lambda values: (values[0], len(values))))


def betweenness_centrality(projection: GraphProjection) -> dict[str, float]:
    """Normalised Brandes betweenness on the dependency-semantic projection.

    The result is a display diagnostic.  It represents shortest paths only;
    callers must not describe it as traffic, material flow or influence unless
    the selected relationship semantics separately justify that assumption.
    """

    cached = _BETWEENNESS_CACHE.get(projection.snapshot_id)
    if cached is not None:
        return dict(cached)
    identifiers = projection.node_ids
    scores = {node_id: 0.0 for node_id in identifiers}
    adjacency_sets = {node_id: set() for node_id in identifiers}
    for link in projection.links:
        if link["relation"] not in DEPENDENCY_RELATIONS:
            continue
        adjacency_sets[link["source"]].add(link["target"])
        adjacency_sets[link["target"]].add(link["source"])
    adjacency = {node_id: tuple(sorted(values)) for node_id, values in adjacency_sets.items()}
    for source in identifiers:
        stack: list[str] = []
        predecessors: dict[str, list[str]] = {node_id: [] for node_id in identifiers}
        paths = {node_id: 0.0 for node_id in identifiers}
        paths[source] = 1.0
        distance = {node_id: -1 for node_id in identifiers}
        distance[source] = 0
        queue = deque([source])
        while queue:
            node_id = queue.popleft()
            stack.append(node_id)
            for neighbour in adjacency[node_id]:
                if distance[neighbour] < 0:
                    queue.append(neighbour)
                    distance[neighbour] = distance[node_id] + 1
                if distance[neighbour] == distance[node_id] + 1:
                    paths[neighbour] += paths[node_id]
                    predecessors[neighbour].append(node_id)
        dependency = {node_id: 0.0 for node_id in identifiers}
        while stack:
            node_id = stack.pop()
            if paths[node_id]:
                coefficient = (1.0 + dependency[node_id]) / paths[node_id]
                for predecessor in predecessors[node_id]:
                    dependency[predecessor] += paths[predecessor] * coefficient
            if node_id != source:
                scores[node_id] += dependency[node_id]
    if len(identifiers) <= 2:
        _remember(_BETWEENNESS_CACHE, projection.snapshot_id, dict(scores))
        return scores
    scale = 1.0 / ((len(identifiers) - 1) * (len(identifiers) - 2))
    result = {node_id: scores[node_id] * scale for node_id in identifiers}
    _remember(_BETWEENNESS_CACHE, projection.snapshot_id, dict(result))
    return result


def core_numbers(projection: GraphProjection) -> dict[str, int]:
    """Return deterministic undirected core numbers without pruning the graph."""

    cached = _CORE_CACHE.get(projection.snapshot_id)
    if cached is not None:
        return dict(cached)
    remaining = set(projection.node_ids)
    result: dict[str, int] = {}
    current_core = 0
    while remaining:
        node_id = min(
            remaining,
            key=lambda value: (
                sum(1 for neighbour in projection.undirected[value] if neighbour in remaining),
                value,
            ),
        )
        degree = sum(1 for neighbour in projection.undirected[node_id] if neighbour in remaining)
        current_core = max(current_core, degree)
        result[node_id] = current_core
        remaining.remove(node_id)
    _remember(_CORE_CACHE, projection.snapshot_id, dict(result))
    return result


def harmonic_proximity(projection: GraphProjection, node_id: str) -> float:
    if node_id not in projection.nodes or len(projection.node_ids) <= 1:
        return 0.0
    distances = {node_id: 0}
    queue = deque([node_id])
    while queue:
        current = queue.popleft()
        for neighbour in projection.undirected[current]:
            if neighbour not in distances:
                distances[neighbour] = distances[current] + 1
                queue.append(neighbour)
    return sum(1.0 / distance for key, distance in distances.items() if key != node_id) / (
        len(projection.node_ids) - 1
    )


def _normalise_fixed(value: float, cap: float) -> float:
    return max(0.0, min(1.0, value / cap)) if cap > 0 else 0.0


def _band(value: float) -> str:
    if value >= 0.8:
        return "very high"
    if value >= 0.6:
        return "high"
    if value >= 0.4:
        return "moderate"
    if value >= 0.2:
        return "low"
    return "very low"


def _value_observation(
    method_id: str,
    raw: Any,
    normalised: float | None,
    trace: list[str],
    *,
    label: str = "",
) -> dict[str, Any]:
    return {
        "method_id": method_id,
        "applicability": "available",
        "raw_value": raw,
        "normalised_value": round(normalised, 6) if normalised is not None else None,
        "band": _band(normalised) if normalised is not None else label or None,
        "reason": None,
        "trace": trace,
        "algorithm_version": "1.0.0",
    }


def unavailable(method_id: str, reason: str, reason_code: str) -> dict[str, Any]:
    return {
        "method_id": method_id,
        "applicability": "unavailable",
        "raw_value": None,
        "normalised_value": None,
        "band": None,
        "reason": reason,
        "reason_code": reason_code,
        "trace": [],
        "algorithm_version": "1.0.0",
    }


def _role_signatures(projection: GraphProjection, node_id: str) -> set[tuple[str, str, str]]:
    cache_key = (projection.snapshot_id, node_id)
    cached = _ROLE_SIGNATURE_CACHE.get(cache_key)
    if cached is not None:
        return set(cached)
    signatures: set[tuple[str, str, str]] = set()
    for link in projection.incident[node_id]:
        if link["source"] == node_id:
            signatures.add(("out", link["relation"], link["target"]))
        else:
            signatures.add(("in", link["relation"], link["source"]))
    _remember(_ROLE_SIGNATURE_CACHE, cache_key, set(signatures))
    return signatures


def _substitute_candidates(projection: GraphProjection, node_id: str) -> tuple[str, ...]:
    """Find explicit-function or shared-role structural substitute candidates."""

    node = projection.nodes[node_id]
    node_type = str(node.get("type", "")).casefold()
    explicit_function = str(
        node.get("function", node.get("kind", node.get("sector", "")))
    ).strip().casefold()
    signatures = _role_signatures(projection, node_id)
    candidates: list[str] = []
    for candidate_id, candidate in projection.nodes.items():
        if candidate_id == node_id:
            continue
        if str(candidate.get("type", "")).casefold() != node_type:
            continue
        candidate_function = str(
            candidate.get("function", candidate.get("kind", candidate.get("sector", "")))
        ).strip().casefold()
        explicit_match = bool(explicit_function and candidate_function == explicit_function)
        shared_role = bool(signatures & _role_signatures(projection, candidate_id))
        if explicit_match or shared_role:
            candidates.append(candidate_id)
    return tuple(sorted(candidates))


def _substitute_diversity(projection: GraphProjection, candidates: tuple[str, ...]) -> float:
    """Average typed-neighbourhood dissimilarity across substitute candidates."""

    if len(candidates) < 2:
        return 0.0
    values: list[float] = []
    for index, left in enumerate(candidates):
        left_signatures = _role_signatures(projection, left)
        for right in candidates[index + 1:]:
            right_signatures = _role_signatures(projection, right)
            union = left_signatures | right_signatures
            values.append(
                1.0 - len(left_signatures & right_signatures) / len(union)
                if union else 0.0
            )
    return sum(values) / len(values)


def _adjacency_for_links(
    projection: GraphProjection,
    *,
    included_layer: str | None = None,
    excluded_layer: str | None = None,
) -> dict[str, tuple[str, ...]]:
    cache_key = (projection.snapshot_id, included_layer, excluded_layer)
    cached = _LAYER_ADJACENCY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    adjacency_sets = {node_id: set() for node_id in projection.node_ids}
    for link in projection.links:
        layer = RELATION_LAYERS.get(link["relation"])
        if included_layer is not None and layer != included_layer:
            continue
        if excluded_layer is not None and layer == excluded_layer:
            continue
        adjacency_sets[link["source"]].add(link["target"])
        adjacency_sets[link["target"]].add(link["source"])
    result = {node_id: tuple(sorted(values)) for node_id, values in adjacency_sets.items()}
    _remember(_LAYER_ADJACENCY_CACHE, cache_key, result)
    return result


def _layer_bridges(projection: GraphProjection, layer: str) -> set[tuple[str, str]]:
    cache_key = (projection.snapshot_id, layer)
    cached = _LAYER_BRIDGE_CACHE.get(cache_key)
    if cached is None:
        cached = _bridge_pairs(_adjacency_for_links(projection, included_layer=layer))
        _remember(_LAYER_BRIDGE_CACHE, cache_key, cached)
    return cached


def _cut_exposure(projection: GraphProjection, node_id: str) -> tuple[float, list[int]]:
    components = _COMPONENT_CACHE.get(projection.snapshot_id)
    if components is None:
        components = connected_components(projection.undirected)
        _remember(_COMPONENT_CACHE, projection.snapshot_id, components)
    containing = next(
        (set(component) for component in components if node_id in component),
        {node_id},
    )
    residual_sizes = [
        len(set(component) & (containing - {node_id}))
        for component in connected_components(projection.undirected, excluded_node=node_id)
        if set(component) & containing
    ]
    remaining_count = max(0, len(containing) - 1)
    possible_pairs = remaining_count * (remaining_count - 1) // 2
    connected_pairs = sum(size * (size - 1) // 2 for size in residual_sizes)
    exposure = (possible_pairs - connected_pairs) / possible_pairs if possible_pairs else 0.0
    return exposure, sorted(residual_sizes, reverse=True)


def node_observations(
    projection: GraphProjection,
    node_id: str,
    *,
    seeds: Iterable[str] = (),
    page_rank: dict[str, float] | None = None,
    seeded_rank: dict[str, float] | None = None,
    articulation: set[str] | None = None,
) -> list[dict[str, Any]]:
    if node_id not in projection.nodes:
        raise KeyError(node_id)
    seeds = tuple(sorted({seed for seed in seeds if seed in projection.nodes}))
    node = projection.nodes[node_id]
    metadata = {key.casefold(): value for key, value in node.items()}
    incident = projection.incident[node_id]
    degree = len(projection.outgoing[node_id]) + len(projection.incoming[node_id])
    weighted_degree = sum(1.0 if link["relation"] in DEPENDENCY_RELATIONS else 0.5 for link in incident)
    local = reachable(projection.undirected, node_id, depth=2)
    page_rank = page_rank or pagerank(projection)
    seeded_rank = seeded_rank or pagerank(projection, seeds=seeds)
    articulation = articulation if articulation is not None else articulation_points(projection)
    betweenness = betweenness_centrality(projection)
    all_bridges = bridge_edges(projection)
    cores = core_numbers(projection)

    layers = sorted({RELATION_LAYERS.get(link["relation"]) for link in incident} - {None})
    dependency_edges = [link for link in incident if link["relation"] in DEPENDENCY_RELATIONS]
    direct_dependents = [
        link for link in dependency_edges if link["source"] == node_id and link["relation"] != "DEPENDS_ON"
    ] + [link for link in dependency_edges if link["target"] == node_id and link["relation"] == "DEPENDS_ON"]
    dependency_value = 1.0 - math.exp(-(len(direct_dependents) + 0.15 * len(local)) / 10.0)

    substitute_ids = _substitute_candidates(projection, node_id)
    substitute_count = len(substitute_ids)
    substitute_diversity = _substitute_diversity(projection, substitute_ids)
    non_substitutability = 1.0 / (1.0 + substitute_count)

    bypasses = 0
    unique_neighbours = projection.undirected[node_id]
    for neighbour in unique_neighbours:
        if neighbour in reachable(
            projection.undirected,
            node_id,
            excluded_edge=(node_id, neighbour),
        ):
            bypasses += 1
    alternate_path_loss = 1.0 - (bypasses / len(unique_neighbours)) if unique_neighbours else 0.0
    incident_bridge_count = sum(
        1 for neighbour in unique_neighbours
        if tuple(sorted((node_id, neighbour))) in all_bridges
    )
    bridge_edge_exposure = (
        incident_bridge_count / len(unique_neighbours) if unique_neighbours else 0.0
    )
    cut_exposure, residual_component_sizes = _cut_exposure(projection, node_id)
    components = _COMPONENT_CACHE.get(projection.snapshot_id)
    if components is None:
        components = connected_components(projection.undirected)
        _remember(_COMPONENT_CACHE, projection.snapshot_id, components)
    component = next((component for component in components if node_id in component), (node_id,))
    component_reach = (
        (len(component) - 1) / max(1, len(projection.node_ids) - 1)
        if len(projection.node_ids) > 1 else 0.0
    )
    bridge_free_components = _BRIDGE_FREE_COMPONENT_CACHE.get(projection.snapshot_id)
    if bridge_free_components is None:
        bridge_free_components = connected_components(
            projection.undirected, excluded_edges=all_bridges
        )
        _remember(
            _BRIDGE_FREE_COMPONENT_CACHE,
            projection.snapshot_id,
            bridge_free_components,
        )
    bridge_free_component = next(
        (component for component in bridge_free_components if node_id in component), (node_id,)
    )
    path_diversity = (
        (len(bridge_free_component) - 1) / max(1, len(component) - 1)
        if len(component) > 1 else 0.0
    )
    participation = 0.0
    if incident:
        layer_counts: dict[str, int] = defaultdict(int)
        for link in incident:
            layer_counts[RELATION_LAYERS.get(link["relation"], "unmapped")] += 1
        participation = 1.0 - sum((count / len(incident)) ** 2 for count in layer_counts.values())
    is_articulation = node_id in articulation
    bridge = 0.35 * participation + 0.30 * float(is_articulation) + 0.35 * alternate_path_loss
    path_coverage = 1.0 - alternate_path_loss
    substitute_coverage = _normalise_fixed(substitute_count, 4)
    structural_coverage = 0.6 * path_coverage + 0.4 * substitute_coverage
    single_point_proxy = bool(
        unique_neighbours
        and substitute_count == 0
        and (is_articulation or bridge_edge_exposure >= 1.0)
    )
    fragility = (
        0.4 * alternate_path_loss
        + 0.3 * non_substitutability
        + 0.3 * float(single_point_proxy)
    )
    role_counts: dict[str, int] = defaultdict(int)
    for link in incident:
        direction = "out" if link["source"] == node_id else "in"
        role_counts[f"{direction}:{link['relation']}"] += 1

    layer_counts: dict[str, int] = defaultdict(int)
    for link in incident:
        layer_counts[RELATION_LAYERS.get(link["relation"], "unmapped")] += 1
    mapped_incident_count = sum(
        count for layer, count in layer_counts.items() if layer != "unmapped"
    )
    mapped_incident_pairs = {
        (
            RELATION_LAYERS[link["relation"]],
            tuple(sorted((link["source"], link["target"]))),
        )
        for link in incident
        if link["relation"] in RELATION_LAYERS
    }
    layer_bridge_pairs: set[tuple[str, tuple[str, str]]] = set()
    for layer in layers:
        layer_bridges = _layer_bridges(projection, layer)
        layer_bridge_pairs.update(
            (mapped_layer, pair)
            for mapped_layer, pair in mapped_incident_pairs
            if mapped_layer == layer and pair in layer_bridges
        )
    layer_bridge_count = len(layer_bridge_pairs)
    layer_bridge_exposure = (
        layer_bridge_count / len(mapped_incident_pairs) if mapped_incident_pairs else 0.0
    )
    baseline_reach = reachable(projection.undirected, node_id)
    layer_losses: dict[str, float] = {}
    for layer in layers:
        remaining_reach = reachable(
            _adjacency_for_links(projection, excluded_layer=layer), node_id
        )
        layer_losses[layer] = (
            len(baseline_reach - remaining_reach) / len(baseline_reach)
            if baseline_reach else 0.0
        )
    layer_removal_sensitivity = max(layer_losses.values(), default=0.0)
    layer_reach: dict[str, set[str]] = defaultdict(set)
    layer_sequences: dict[tuple[str, str], set[str]] = defaultdict(set)
    for first in incident:
        first_layer = RELATION_LAYERS.get(first["relation"])
        if not first_layer:
            continue
        other = first["target"] if first["source"] == node_id else first["source"]
        for second in projection.incident[other]:
            second_layer = RELATION_LAYERS.get(second["relation"])
            if not second_layer:
                continue
            destination = second["target"] if second["source"] == other else second["source"]
            if destination == node_id:
                continue
            layer_reach[first_layer].add(destination)
            layer_sequences[(first_layer, second_layer)].add(destination)
    layer_reached_nodes = set().union(*layer_reach.values()) if layer_reach else set()
    multilayer_path_diversity = _normalise_fixed(len(layer_sequences), 20)

    valid_seeds = set(seeds)
    baseline_seed_reach: set[str] = set(valid_seeds)
    for seed in valid_seeds:
        baseline_seed_reach.update(reachable(projection.undirected, seed))
    stressed_seed_reach: set[str] = set()
    for seed in valid_seeds - {node_id}:
        stressed_seed_reach.add(seed)
        stressed_seed_reach.update(
            reachable(projection.undirected, seed, excluded_node=node_id)
        )
    seed_scope_after_removal = baseline_seed_reach - {node_id}
    lost_seed_reach = seed_scope_after_removal - stressed_seed_reach
    robustness_loss = (
        len(lost_seed_reach) / len(seed_scope_after_removal)
        if seed_scope_after_removal else 0.0
    )
    criticality_text = str(metadata.get("criticality", "")).upper()
    mission_value = CRITICALITY_MAP.get(criticality_text)

    observations = [
        _value_observation(
            "centrality.typed_weighted_degree", weighted_degree, _normalise_fixed(weighted_degree, 24),
            [f"{len(incident)} fused incident relationships", "dependency relations weight 1.0; other types weight 0.5", "fixed reference cap 24"],
        ),
        _value_observation(
            "centrality.local_reach", len(local), _normalise_fixed(len(local), 60),
            [f"{len(local)} distinct objects within two undirected structural hops", "fixed reference cap 60"],
        ),
        _value_observation(
            "centrality.pagerank", page_rank.get(node_id, 0.0), _normalise_fixed(page_rank.get(node_id, 0.0), 0.03),
            ["direction-sensitive PageRank", "damping 0.85", "fixed mass reference 0.03"],
        ),
        _value_observation(
            "centrality.personalized_pagerank", seeded_rank.get(node_id, 0.0), _normalise_fixed(seeded_rank.get(node_id, 0.0), 0.08),
            [f"restart seeds: {', '.join(sorted(set(seeds))) or 'none'}", "damping 0.85", "fixed mass reference 0.08"],
        ) if tuple(seeds) else unavailable(
            "centrality.personalized_pagerank", "No mission seed is present in the decision context.", "missing_mission_seed"
        ),
        _value_observation(
            "centrality.harmonic_proximity", harmonic_proximity(projection, node_id), harmonic_proximity(projection, node_id),
            ["reciprocal shortest-path distance", "unreachable nodes contribute zero"],
        ),
        _value_observation(
            "centrality.betweenness_structural", betweenness[node_id], betweenness[node_id],
            ["normalised unweighted Brandes betweenness", "undirected projection restricted to configured dependency predicates", "display diagnostic only: no flow, traffic or influence semantics are assumed"],
        ),
        _value_observation(
            "centrality.core_membership", cores[node_id],
            cores[node_id] / max(1, max(cores.values(), default=1)),
            [f"core number {cores[node_id]}", "graph-relative display diagnostic only", "no node was pruned or excluded"],
        ),
        unavailable(
            "centrality.within_community_role", "No frozen community assignment is available for within-community degree.", "missing_community_assignment"
        ),
        unavailable(
            "centrality.ranking_stability", "No documented graph-perturbation or bootstrap runs are available.", "missing_stability_runs"
        ),
        _value_observation(
            "functional.role", dict(sorted(role_counts.items())), _normalise_fixed(len(role_counts), 12),
            [f"{len(role_counts)} distinct directed relation roles", "roles are derived from typed incident relationships", "fixed display cap 12"],
        ),
        _value_observation(
            "functional.dependency", round(dependency_value, 6), dependency_value,
            [f"{len(direct_dependents)} directionally relevant dependency records", f"{len(local)} two-hop structural neighbours", "structural proxy; no capacity or causal model"],
        ),
        _value_observation(
            "resilience.non_substitutability", substitute_count, non_substitutability,
            [f"{substitute_count} comparable same-type/function records", "inverse fixed transform 1/(1+s)", "collection completeness is unknown"],
        ),
        _value_observation(
            "resilience.substitute_count", substitute_count, substitute_coverage,
            [f"candidate ids: {', '.join(substitute_ids) or 'none'}", "same object type plus explicit function match or shared typed role", "fixed display cap 4"],
        ),
        _value_observation(
            "resilience.substitute_diversity", round(substitute_diversity, 6), substitute_diversity,
            ["average Jaccard dissimilarity of substitute typed-neighbourhood signatures", "structural proxy; capacity and interchangeability are not asserted"],
        ),
        _value_observation(
            "resilience.alternative_coverage", round(structural_coverage, 6), structural_coverage,
            [f"structural path coverage 60% = {path_coverage:.3f}", f"recorded substitute coverage 40% = {substitute_coverage:.3f}", "not a capacity or reliability calculation"],
        ),
        _value_observation(
            "resilience.dependency_exposure", len(dependency_edges), _normalise_fixed(len(dependency_edges), 12),
            [f"{len(dependency_edges)} incident relationships in the configured dependency predicate set", "fixed display cap 12", "exposure is structural, not loss probability"],
        ),
        _value_observation(
            "multilayer.cross_layer_dependency", len(layers), _normalise_fixed(len(layers), 10),
            [f"mapped layers: {', '.join(layers) or 'none'}", "ten-layer fixed reference"],
        ),
        _value_observation(
            "multilayer.cross_layer_participation", round(participation, 6), participation,
            ["incident relationships grouped by the documented relation-to-layer mapping", "one minus squared layer-degree shares", "unmapped relations remain a visible separate group"],
        ),
        _value_observation(
            "multilayer.per_layer_centrality", dict(sorted(layer_counts.items())),
            _normalise_fixed(mapped_incident_count, 24),
            ["incident relationship counts by documented relation-to-layer mapping", f"{mapped_incident_count} mapped relationships", "unmapped relations remain visible and are not promoted to layers"],
        ),
        _value_observation(
            "multilayer.layer_bridge_exposure", layer_bridge_count, layer_bridge_exposure,
            [f"{layer_bridge_count} of {len(mapped_incident_pairs)} mapped incident layer/endpoint pairs are bridges inside their layer", "each layer uses an undirected structural projection", "not a physical failure claim"],
        ),
        _value_observation(
            "multilayer.layer_to_layer_reach",
            {layer: len(values) for layer, values in sorted(layer_reach.items())},
            len(layer_reached_nodes) / max(1, len(projection.node_ids) - 1),
            [f"{len(layer_reached_nodes)} distinct nodes reached by mapped two-hop layer transitions", "two-hop undirected structural walk", "relation-to-layer mapping is explicit"],
        ),
        _value_observation(
            "multilayer.path_diversity",
            {f"{left}->{right}": len(values) for (left, right), values in sorted(layer_sequences.items())},
            multilayer_path_diversity,
            [f"{len(layer_sequences)} distinct mapped two-hop layer sequences", "fixed display cap 20", "sequence diversity does not imply route capacity"],
        ),
        _value_observation(
            "multilayer.layer_removal_sensitivity", dict(sorted(layer_losses.items())),
            layer_removal_sensitivity,
            ["maximum fractional reach loss after removing all edges in one mapped layer", "static structural stress test", "not a causal cascade prediction"],
        ),
        _value_observation(
            "structure.participation_coefficient", round(participation, 6), participation,
            ["incident relationships grouped by configured layer mapping", f"{len(layers)} mapped layers plus any unmapped group"],
        ),
        _value_observation(
            "structure.articulation", is_articulation, float(is_articulation),
            ["Tarjan cut-vertex search on the undirected structural projection", "structural property; not a causal cascade"],
            label="yes" if is_articulation else "no",
        ),
        _value_observation(
            "structure.bridge_edge_exposure", incident_bridge_count, bridge_edge_exposure,
            [f"{incident_bridge_count} of {len(unique_neighbours)} incident endpoint pairs are bridges", "Tarjan bridge search on the undirected structural projection", "parallel assertion count is fused and no failure causality is inferred"],
        ),
        _value_observation(
            "structure.cut_set_exposure", residual_component_sizes, cut_exposure,
            [f"residual component sizes after removing the node: {residual_component_sizes}", "fraction of previously connected remaining-node pairs separated", "single-node structural cut only"],
        ),
        _value_observation(
            "structure.path_diversity", len(bridge_free_component) - 1, path_diversity,
            [f"{max(0, len(bridge_free_component) - 1)} of {max(0, len(component) - 1)} same-component nodes share a bridge-free component", "equivalent to at least two edge-disjoint paths in the simple undirected projection", "does not measure capacity, delay or node-disjointness"],
        ),
        _value_observation(
            "structure.component_reachability", len(component), component_reach,
            [f"component size {len(component)} across {len(components)} graph component(s)", "normalised by all other nodes", "disconnected objects contribute no reach"],
        ),
        _value_observation(
            "resilience.alternate_path_loss", round(alternate_path_loss, 6), alternate_path_loss,
            [f"{bypasses} of {len(unique_neighbours)} neighbour connections have a structural bypass", "direct edge excluded during each reachability test"],
        ),
        _value_observation(
            "structure.bridge_participation", round(bridge, 6), bridge,
            [f"participation 35% = {participation:.3f}", f"articulation 30% = {float(is_articulation):.3f}", f"alternate-path loss 35% = {alternate_path_loss:.3f}"],
        ),
        _value_observation(
            "resilience.redundancy", round(structural_coverage, 6), structural_coverage,
            ["60% alternate-path coverage plus 40% recorded substitute coverage", "structural proxy only", "capacity and recovery data unavailable"],
        ),
        _value_observation(
            "resilience.single_point_failure", single_point_proxy, float(single_point_proxy),
            [f"articulation={is_articulation}", f"incident bridge exposure={bridge_edge_exposure:.3f}", f"recorded substitutes={substitute_count}", "structural single-point proxy only; no causal failure model"],
            label="structural proxy: yes" if single_point_proxy else "structural proxy: no",
        ),
        _value_observation(
            "resilience.fragility_band", round(fragility, 6), fragility,
            [f"alternate-path loss 40% = {alternate_path_loss:.3f}", f"non-substitutability 30% = {non_substitutability:.3f}", f"single-point proxy 30% = {float(single_point_proxy):.3f}", "display band; not a probability"],
        ),
        _value_observation(
            "resilience.robustness_loss", len(lost_seed_reach), robustness_loss,
            [f"versioned seeds: {', '.join(seeds)}", f"{len(lost_seed_reach)} seed-scope nodes lose structural reach after node removal", "static structural deletion test; not a causal cascade"],
        ) if seeds else unavailable(
            "resilience.robustness_loss", "A versioned seed reachability scope has not been configured.", "missing_seed_scope"
        ),
        _value_observation(
            "resilience.structural_stress_reach", sorted(lost_seed_reach), robustness_loss,
            [f"nodes losing reach: {', '.join(sorted(lost_seed_reach)) or 'none'}", "node-removal stress test within the explicit mission-seed scope", "capacity, delay and recovery are not modelled"],
        ) if seeds else unavailable(
            "resilience.structural_stress_reach", "A versioned seed reachability scope has not been configured.", "missing_seed_scope"
        ),
        unavailable(
            "resilience.recovery_difficulty", "No explicit recovery time, resource, substitution or restoration assessment is recorded.", "missing_recovery_model"
        ),
        unavailable(
            "temporal.urgency", "No decision horizon or reliable observation-time series is configured.", "missing_decision_horizon"
        ),
        unavailable(
            "mission.strategic_consequence", "No analyst-entered strategic consequence is recorded in this decision context.", "missing_analyst_assessment"
        ),
        unavailable(
            "evidence.provenance_completeness", "Assertion-level source, passage, lineage, time and derivation fields are not available on this node projection.", "missing_assertion_provenance"
        ),
        unavailable(
            "evidence.independent_corroboration", "Source lineage is not recorded; document count is not independence.", "missing_source_lineage"
        ),
        unavailable(
            "evidence.extraction_resolution_certainty", "Both extraction certainty and reviewed entity-resolution certainty are required.", "missing_resolution_certainty"
        ),
        unavailable(
            "evidence.source_reliability", "No explicit source reliability assessment is present.", "missing_source_reliability"
        ),
        unavailable(
            "evidence.information_credibility", "No explicit information credibility and directness assessments are present.", "missing_information_credibility"
        ),
        unavailable(
            "evidence.temporal_validity", "Valid time cannot be evaluated against an absent decision horizon.", "missing_decision_horizon"
        ),
        unavailable(
            "evidence.directness", "No assertion-level directness assessment is recorded.", "missing_evidence_directness"
        ),
        unavailable(
            "evidence.extraction_certainty", "No grounded extraction certainty is available at node level.", "missing_extraction_certainty"
        ),
        unavailable(
            "evidence.entity_resolution_certainty", "No reviewed entity-resolution certainty is available at node level.", "missing_resolution_certainty"
        ),
        unavailable(
            "evidence.supporting_strength", "No lineage-aware supporting evidence aggregation is available for this node and context.", "missing_supporting_evidence"
        ),
        unavailable(
            "evidence.disconfirming_strength", "No compatible disconfirming evidence aggregation is available for this node and context.", "missing_disconfirming_evidence"
        ),
        unavailable(
            "evidence.source_lineage_diversity", "Origin and derivation lineages are not recorded for the supporting assertion set.", "missing_source_lineage"
        ),
        unavailable(
            "evidence.unresolved_status", "No explicit evidence requirement or adjudication state is linked to this node.", "missing_evidence_requirement"
        ),
        unavailable(
            "evidence.confidence_band", "The complete evidential-confidence profile is unavailable; missing contributors are not treated as zero.", "incomplete_evidence_profile"
        ),
        unavailable(
            "contradiction.severity", "No compatible supporting and disconfirming assertion pair is recorded.", "missing_contradiction_pair"
        ),
        unavailable(
            "collection.decision_sensitivity", "Decision alternatives and sensitivity runs are not configured.", "missing_decision_model"
        ),
        unavailable(
            "structural.component", "Mission seeds and perturbation-based community-role stability are required for the full structural component.", "missing_structural_assurance"
        ),
        unavailable(
            "collection.assessment_change_likelihood", "No leakage-controlled history of collection outcomes and assessment changes is available.", "missing_collection_outcomes"
        ),
        unavailable(
            "collection.unresolved_consequence", "No specific unresolved question has an explicit context-bound consequence assessment.", "missing_unresolved_consequence"
        ),
        unavailable(
            "collection.feasibility", "No specific collection option, deadline and constraint assessment is configured.", "missing_collection_option"
        ),
        unavailable(
            "collection.estimated_latency", "No collection option with an estimated completion-time distribution is configured.", "missing_collection_option"
        ),
        unavailable(
            "collection.estimated_cost", "No authorised cost estimate is attached to a specific collection option.", "missing_collection_cost"
        ),
        unavailable(
            "collection.value_band", "The required decision-sensitivity, consequence, change-likelihood, urgency and feasibility inputs are incomplete.", "incomplete_collection_profile"
        ),
        unavailable(
            "collection.expected_value_information", "No explicit alternatives, utilities, prior or observation model is configured.", "missing_decision_model"
        ),
        unavailable(
            "hypothesis.mission_consequence_if_true", "This node is not a model-only relationship hypothesis with a conditional consequence assessment.", "not_relationship_hypothesis"
        ),
        unavailable(
            "hypothesis.calibrated_plausibility", "No relation-specific, leakage-controlled calibrator is configured.", "missing_hypothesis_calibration"
        ),
        unavailable(
            "hypothesis.independent_agreement", "No lineage-distinct hypothesis generators have produced a comparable candidate set.", "missing_independent_hypothesis_methods"
        ),
        unavailable(
            "hypothesis.candidate_rank", "This accepted node is not a model-only relationship candidate in a frozen comparison set.", "not_relationship_hypothesis"
        ),
        unavailable(
            "hypothesis.model_abstention", "This accepted node has no relation-specific candidate-model abstention record.", "not_relationship_hypothesis"
        ),
        unavailable(
            "hypothesis.prediction_set_size", "No calibrated or conformal relation-specific prediction set is available.", "missing_hypothesis_calibration"
        ),
        unavailable(
            "hypothesis.structural_path_support", "This accepted node is not a candidate triple requiring bounded structural-path support.", "not_relationship_hypothesis"
        ),
        unavailable(
            "hypothesis.counterfactual_sensitivity", "No candidate edge and frozen model run exist for a counterfactual sensitivity test.", "not_relationship_hypothesis"
        ),
        unavailable(
            "hypothesis.symbolic_rule_support", "No audited symbolic-rule runtime and matching candidate are available.", "missing_rule_engine"
        ),
        unavailable(
            "hypothesis.text_evidence_support", "No candidate triple has been matched to direct text evidence with lineage.", "missing_candidate_evidence"
        ),
        unavailable(
            "hypothesis.time_sensitivity", "No candidate valid-time horizon or event-time forecast is available.", "missing_temporal_hypothesis"
        ),
        unavailable(
            "hypothesis.review_feasibility", "No candidate-specific review action, deadline or constraint assessment is configured.", "missing_review_option"
        ),
        unavailable(
            "hypothesis.review_priority", "This node is not a model-only relationship hypothesis and the review-profile inputs are incomplete.", "not_relationship_hypothesis"
        ),
        unavailable(
            "hypothesis.novelty", "Novelty is defined only inside a frozen candidate comparison set and is never treated as evidence.", "not_relationship_hypothesis"
        ),
        unavailable(
            "collection.expected_information_gain", "No calibrated prior or observation model is configured.", "missing_probabilistic_model"
        ),
        unavailable(
            "contradiction.unresolved_consequence", "No compatible contradiction object has a context-bound unresolved consequence assessment.", "missing_contradiction"
        ),
        unavailable(
            "contradiction.lineage_independence", "No compatible contradiction pair with recorded source lineage is available.", "missing_contradiction_lineage"
        ),
        unavailable(
            "contradiction.resolution_likelihood", "No specific resolution action or adjudicated outcome model is configured.", "missing_resolution_model"
        ),
        unavailable(
            "temporal.activity_change", "A single graph snapshot cannot establish temporal change.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.new_relationships", "A single graph snapshot cannot establish which relationships are new.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.expired_relationships", "No prior snapshot and complete valid-time intervals are available.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.relationship_state_change", "No versioned assertion history exists for polarity or status comparison.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.activity_rate", "No event-time series and observation window are configured.", "missing_event_time"
        ),
        unavailable(
            "temporal.centrality_change", "No comparable typed projections exist across two valid-time windows.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.community_role_change", "No stable community assignments exist across comparable time windows.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.contradiction_change", "No versioned compatible contradiction sets exist across time windows.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.evidence_confidence_change", "No versioned evidence profiles exist across comparable decision times.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.recurrence", "No event-centric history with repeated comparable events is available.", "missing_event_time"
        ),
        unavailable(
            "temporal.burst", "No collection-normalised event-time series is available for burst detection.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.change_point", "No collection-normalised event-time series is available.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.stability", "No repeated comparable observations exist for a temporal stability estimate.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.time_decayed_relevance", "No defensible event or observation time and decay policy are configured.", "missing_event_time"
        ),
        unavailable(
            "temporal.emerging_node", "No collection-normalised baseline window exists for an emerging-node indicator.", "missing_temporal_history"
        ),
        unavailable(
            "temporal.emerging_relationship", "This is a node observation and no relationship history is configured.", "missing_temporal_history"
        ),
        unavailable(
            "structure.typed_temporal_motifs", "The static projection lacks event-time sequences and a documented typed-motif catalogue.", "missing_temporal_history"
        ),
        unavailable(
            "community.leiden", "The optional Leiden runtime and frozen assignments are not installed.", "dependency_not_installed"
        ),
        unavailable(
            "community.role_stability", "No seeded community perturbation runs have completed.", "missing_stability_runs"
        ),
        unavailable(
            "resilience.capacity_flow", "No capacities, demands, thresholds or flow-conservation model are configured.", "missing_capacity_model"
        ),
        unavailable(
            "resilience.fault_tree", "No audited failure events, gates and base-event probabilities are configured.", "missing_fault_tree"
        ),
        unavailable(
            "resilience.bayesian_network", "No validated conditional probability tables or Bayesian network structure are configured.", "missing_bayesian_model"
        ),
        unavailable(
            "resilience.reliability_model", "No component reliability, repair, delay or recovery distributions are configured.", "missing_reliability_model"
        ),
    ]
    if mission_value is None:
        observations.insert(
            0,
            unavailable(
                "mission.consequence",
                "No explicit mission consequence assessment exists for this object in this context.",
                "missing_mission_assessment",
            ),
        )
    else:
        observations.insert(
            0,
            _value_observation(
                "mission.consequence", criticality_text, mission_value,
                [f"graph criticality metadata = {criticality_text}", "fixed categorical mapping", "source: graph record, not a calculated truth"],
            ),
        )
    computed_at = datetime.now(timezone.utc).isoformat()
    for observation in observations:
        observation["computation_timestamp"] = computed_at
    return observations


def score_result(
    profile: dict[str, Any], observations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply a profile without silently reweighting unavailable contributors."""

    by_id = {item["method_id"]: item for item in observations}
    contributions: list[dict[str, Any]] = []
    available_weight = 0.0
    total = 0.0
    missing: list[str] = []
    for method_id, weight in profile.get("weights", {}).items():
        observation = by_id.get(method_id)
        value = observation.get("normalised_value") if observation else None
        if observation is None or observation.get("applicability") != "available" or value is None:
            missing.append(method_id)
            contributions.append(
                {
                    "method_id": method_id,
                    "weight": weight,
                    "value": None,
                    "contribution": None,
                    "status": "unavailable",
                }
            )
            continue
        contribution = float(weight) * float(value)
        available_weight += float(weight)
        total += contribution
        contributions.append(
            {
                "method_id": method_id,
                "weight": weight,
                "value": value,
                "contribution": round(contribution, 6),
                "status": "available",
            }
        )
    return {
        "profile_id": profile["id"],
        "value": round(total, 6) if not missing else None,
        "available_weight": round(available_weight, 6),
        "missing_required": missing,
        "status": "available" if not missing else "unavailable",
        "reason": None if not missing else "Required contributors are unavailable; remaining weights were not renormalised.",
        "contributions": contributions,
    }
