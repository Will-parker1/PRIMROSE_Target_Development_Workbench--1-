"""Render a graph as a single self-contained interactive HTML document.

    python graph_to_html.py                          # graph.graphml -> graph.html
    python graph_to_html.py --graph osint_knowledge_graph.json -o osint.html
    python graph_to_html.py --focus "Bereznyi Hydroelectric Plant" --depth 2

Reads GraphML through ``kg_backend.graphml`` or node-link JSON directly, then writes one
HTML file with the data embedded. Nothing is fetched at runtime: no CDN, no external
stylesheet, no fonts. The file can be opened from disk, attached to an email or dropped
on a share, and it will render.

The document gives a force-directed canvas view with:

  * colour by entity type, matching the palette used by the web application
  * click-to-toggle type and relationship filters
  * search with focus, and neighbourhood highlighting on hover
  * a details panel listing every attribute the source graph holds for a selection
  * drag to reposition, scroll to zoom, double-click to isolate a neighbourhood

Canvas rather than SVG because a few hundred nodes with 900-odd links repaint far more
smoothly, and the layout is a plain Fruchterman-Reingold style simulation so there is no
layout library to load.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from kg_backend.graphml import read_graphml  # noqa: E402

DEFAULT_GRAPH = ROOT / "graph.graphml"
DEFAULT_OUTPUT = ROOT / "graph.html"

# Mirrors TYPE_COLOURS in web/app.js and building_kg/pdf_to_kg.py. A node that carries its
# own "color" attribute keeps it; this is the fallback for graphs that do not.
TYPE_COLOURS = {
    "actor": "#ff4b4b",
    "force": "#ff7043",
    "organisation": "#ffb84b",
    "programme": "#d4a017",
    "capability": "#4b9bff",
    "materiel": "#5c6bc0",
    "infrastructure": "#4bff9b",
    "industry": "#26a69a",
    "location": "#b84bff",
    "condition": "#8d6e63",
    "metric": "#78909c",
    "company": "#29b6f6",
    "person": "#ec407a",
    "site": "#66bb6a",
    "equipment": "#7e57c2",
    "jurisdiction": "#ab47bc",
    "sector": "#c0ca33",
    "document": "#a1887f",
    "event": "#ffa726",
    "unknown": "#888888",
}

TYPE_ALIASES = {
    "state / polity": "actor",
    "military force / org": "force",
    "location / infrastructure": "infrastructure",
    "concept / capability": "capability",
    "system / equipment": "materiel",
    "facility": "site",
    "entity": "unknown",
}


def type_colour(entity_type: str) -> str:
    key = str(entity_type or "").strip().lower()
    return TYPE_COLOURS.get(key) or TYPE_COLOURS.get(TYPE_ALIASES.get(key, ""), TYPE_COLOURS["unknown"])


def load_graph(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.suffix.lower() in {".graphml", ".xml"}:
        return read_graphml(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    links = data.get("links", data.get("edges", []))
    return {
        "directed": bool(data.get("directed", True)),
        "graph": data.get("graph", {}),
        "nodes": data.get("nodes", []),
        "links": links,
    }


def _endpoint(value: Any) -> str:
    return str(value.get("id") if isinstance(value, dict) else value)


def prepare(
    graph: dict[str, Any],
    *,
    focus: str = "",
    depth: int = 2,
) -> dict[str, Any]:
    """Normalise to what the page needs, optionally cut to a neighbourhood."""
    nodes = {}
    for raw in graph["nodes"]:
        node_id = str(raw.get("id") or raw.get("label") or "")
        if not node_id:
            continue
        attributes = {
            key: value
            for key, value in raw.items()
            if key not in {"id", "label", "color", "size", "font", "title"}
            and value not in (None, "")
        }
        nodes[node_id] = {
            "id": node_id,
            "label": str(raw.get("label") or node_id),
            "type": str(raw.get("type") or "unknown"),
            "colour": raw.get("color") or type_colour(raw.get("type")),
            "attributes": attributes,
        }

    links = []
    for raw in graph["links"]:
        source, target = _endpoint(raw.get("source")), _endpoint(raw.get("target"))
        if source not in nodes or target not in nodes:
            continue
        links.append(
            {
                "source": source,
                "target": target,
                "relation": str(raw.get("relation") or raw.get("label") or "RELATED_TO"),
                "attributes": {
                    key: value
                    for key, value in raw.items()
                    if key not in {"source", "target", "relation", "label", "key", "id", "title"}
                    and value not in (None, "")
                },
            }
        )

    if focus:
        keep = _neighbourhood(nodes, links, focus, depth)
        nodes = {node_id: node for node_id, node in nodes.items() if node_id in keep}
        links = [l for l in links if l["source"] in keep and l["target"] in keep]

    degree: Counter[str] = Counter()
    for link in links:
        degree[link["source"]] += 1
        degree[link["target"]] += 1
    for node_id, node in nodes.items():
        node["degree"] = degree.get(node_id, 0)

    return {"nodes": list(nodes.values()), "links": links}


def _neighbourhood(
    nodes: dict[str, Any], links: list[dict[str, Any]], focus: str, depth: int
) -> set[str]:
    folded = focus.strip().casefold()
    start = next(
        (
            node_id
            for node_id, node in nodes.items()
            if node_id.casefold() == folded or node["label"].casefold() == folded
        ),
        None,
    ) or next(
        (
            node_id
            for node_id, node in nodes.items()
            if folded in node["label"].casefold()
        ),
        None,
    )
    if not start:
        raise SystemExit(f"No entity matches {focus!r}.")

    adjacency: dict[str, set[str]] = {}
    for link in links:
        adjacency.setdefault(link["source"], set()).add(link["target"])
        adjacency.setdefault(link["target"], set()).add(link["source"])

    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        node_id, distance = queue.popleft()
        if distance >= depth:
            continue
        for neighbour in adjacency.get(node_id, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, distance + 1))
    return seen


# ------------------------------------------------------------------------------- page

PAGE = """<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --ink: #cdddea; --ink-soft: #7e909e; --paper: #0f151c; --canvas: #080c11;
  --line: #202b34; --line-strong: #304250; --accent: #3d9bfd;
  --mono: ui-monospace, SFMono-Regular, Menlo, "Cascadia Mono", monospace;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  display: flex; flex-direction: column; background: var(--canvas); color: var(--ink);
  font: 13px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  overflow: hidden;
}
header {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 12px 18px; background: var(--paper); border-bottom: 1px solid var(--line);
}
header h1 { margin: 0; font-size: 15px; letter-spacing: .01em; }
header .stats { color: var(--ink-soft); font-family: var(--mono); font-size: 11px; }
header .spacer { flex: 1; }
input[type=search], button {
  background: var(--canvas); color: var(--ink); border: 1px solid var(--line-strong);
  border-radius: 8px; padding: 7px 11px; font: inherit;
}
input[type=search] { min-width: 230px; }
input[type=search]:focus, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
button { cursor: pointer; }
button:hover { border-color: var(--accent); color: #eaf3ff; }
main { flex: 1; display: flex; min-height: 0; }
aside {
  width: 288px; flex: none; overflow-y: auto; padding: 14px;
  background: var(--paper); border-right: 1px solid var(--line);
}
aside.details { border-right: 0; border-left: 1px solid var(--line); width: 330px; }
aside h2 {
  margin: 0 0 9px; font-size: 10px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--ink-soft);
}
aside section + section { margin-top: 20px; }
.filter {
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 5px 7px;
  background: none; border: 0; border-radius: 6px; color: var(--ink);
  text-align: left; cursor: pointer; font: inherit;
}
.filter:hover { background: rgba(61,155,253,.09); }
.filter.off { opacity: .35; }
.filter .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.filter .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.filter .count { font-family: var(--mono); font-size: 11px; color: var(--ink-soft); }
#canvasWrap { position: relative; flex: 1; min-width: 0; }
canvas { display: block; width: 100%; height: 100%; cursor: grab; }
canvas.dragging { cursor: grabbing; }
#hint {
  position: absolute; left: 14px; bottom: 12px; padding: 7px 10px;
  background: rgba(8,12,17,.86); border: 1px solid var(--line); border-radius: 8px;
  color: var(--ink-soft); font-size: 11px; font-family: var(--mono); pointer-events: none;
}
#tip {
  position: absolute; padding: 6px 9px; background: rgba(8,12,17,.95);
  border: 1px solid var(--line-strong); border-radius: 7px; font-size: 12px;
  pointer-events: none; opacity: 0; transition: opacity .12s; max-width: 300px;
}
.empty { color: var(--ink-soft); font-style: italic; }
.row {
  display: flex; justify-content: space-between; gap: 14px; padding: 6px 0;
  border-bottom: 1px solid var(--line);
}
.row span { color: var(--ink-soft); flex: none; }
.row strong { font-family: var(--mono); font-weight: 500; text-align: right; word-break: break-word; }
.pill {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px;
  border-radius: 999px; background: rgba(61,155,253,.13); color: var(--accent);
  font-size: 11px; font-family: var(--mono);
}
.rel {
  display: block; width: 100%; padding: 7px 8px; margin-bottom: 4px; text-align: left;
  background: var(--canvas); border: 1px solid var(--line); border-radius: 7px;
  color: var(--ink); cursor: pointer; font: inherit;
}
.rel:hover { border-color: var(--accent); }
.rel .verb { font-family: var(--mono); font-size: 10.5px; color: var(--accent); }
.rel .dir { color: var(--ink-soft); }
footer {
  padding: 7px 18px; background: var(--paper); border-top: 1px solid var(--line);
  color: var(--ink-soft); font-size: 11px; font-family: var(--mono);
}
@media (max-width: 950px) {
  main { flex-direction: column; }
  aside, aside.details { width: auto; max-height: 190px; border: 0; border-top: 1px solid var(--line); }
}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="stats" id="stats"></span>
  <span class="spacer"></span>
  <input type="search" id="search" placeholder="Search entities" aria-label="Search entities">
  <button id="fitView">Fit</button>
  <button id="resetView">Reset all</button>
  <button id="restart">Re-run layout</button>
</header>
<main>
  <aside>
    <section>
      <h2>Entity types</h2>
      <div id="typeFilters"></div>
    </section>
    <section>
      <h2>Relationships</h2>
      <div id="relFilters"></div>
    </section>
  </aside>
  <div id="canvasWrap">
    <canvas id="view"></canvas>
    <div id="tip"></div>
    <div id="hint">drag to move · scroll to zoom · double-click to isolate · click background to clear</div>
  </div>
  <aside class="details">
    <section>
      <h2>Selection</h2>
      <div id="details"><p class="empty">Select an entity to see everything the graph records about it.</p></div>
    </section>
  </aside>
</main>
<footer id="footer"></footer>
<script id="graph-data" type="application/json">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("graph-data").textContent);
const nodes = DATA.nodes;
const links = DATA.links;
const byId = new Map(nodes.map(n => [n.id, n]));
links.forEach(l => { l.s = byId.get(l.source); l.t = byId.get(l.target); });

const neighbours = new Map(nodes.map(n => [n.id, new Set()]));
const incident = new Map(nodes.map(n => [n.id, []]));
for (const l of links) {
  neighbours.get(l.source).add(l.target);
  neighbours.get(l.target).add(l.source);
  incident.get(l.source).push(l);
  incident.get(l.target).push(l);
}

const canvas = document.getElementById("view");
const ctx = canvas.getContext("2d");
const tip = document.getElementById("tip");
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let view = { x: 0, y: 0, k: 1 };
let hovered = null, selected = null, dragging = null, panning = null;
const hiddenTypes = new Set(), hiddenRels = new Set();
let query = "";

/* ---------------------------------------------------------------- layout */
// Fruchterman-Reingold with a cooling schedule: repulsion between every pair,
// attraction along links, recentred each tick. A few hundred nodes is well within
// what an O(n^2) pass can do inside a frame.
const AREA = 1100;
const GRAVITY = 0.018;
let temperature = AREA / 8;
let autoFitted = false;
const k = Math.sqrt((AREA * AREA) / Math.max(nodes.length, 1));

const LABEL_RANKS = Math.min(28, Math.ceil(nodes.length * 0.09));

nodes.forEach((n, i) => {
  const angle = i * 2.399963;                       // golden angle, avoids initial overlap
  const radius = AREA * 0.42 * Math.sqrt(i / nodes.length);
  n.x = Math.cos(angle) * radius;
  n.y = Math.sin(angle) * radius;
  n.dx = 0; n.dy = 0;
  n.r = Math.max(4.5, Math.min(15, 4.5 + Math.sqrt(n.degree) * 2.1));
});
// Rank by connectivity purely to decide which labels are always drawn. This is a
// legibility device, not a claim about importance.
[...nodes].sort((a, b) => b.degree - a.degree).forEach((n, i) => { n.rank = i; });

function step() {
  if (temperature < 0.6) return false;
  for (const n of nodes) { n.dx = 0; n.dy = 0; }
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.01; }
      const force = (k * k) / d2;
      a.dx += dx * force; a.dy += dy * force;
      b.dx -= dx * force; b.dy -= dy * force;
    }
  }
  for (const l of links) {
    const dx = l.s.x - l.t.x, dy = l.s.y - l.t.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const force = (d * d) / k / d;
    l.s.dx -= dx * force; l.s.dy -= dy * force;
    l.t.dx += dx * force; l.t.dy += dy * force;
  }
  let cx = 0, cy = 0;
  for (const n of nodes) {
    if (n === dragging) continue;
    const d = Math.sqrt(n.dx * n.dx + n.dy * n.dy) || 1;
    n.x += (n.dx / d) * Math.min(d, temperature);
    n.y += (n.dy / d) * Math.min(d, temperature);
    // Plain Fruchterman-Reingold has nothing holding the drawing together, and
    // repulsion across a few hundred nodes pushes the outer ring a long way out.
    // A weak pull towards the origin bounds the layout without visibly distorting it.
    n.x -= n.x * GRAVITY;
    n.y -= n.y * GRAVITY;
    cx += n.x; cy += n.y;
  }
  cx /= nodes.length; cy /= nodes.length;
  for (const n of nodes) { n.x -= cx; n.y -= cy; }
  temperature *= 0.985;
  return true;
}

function fitView() {
  const shown = nodes.filter(visibleNode);
  if (!shown.length) return;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of shown) {
    if (n.x < minX) minX = n.x;
    if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.y > maxY) maxY = n.y;
  }
  const spanX = Math.max(maxX - minX, 1), spanY = Math.max(maxY - minY, 1);
  view.k = Math.max(0.05, Math.min(3, Math.min(
    (canvas.clientWidth - 130) / spanX,
    (canvas.clientHeight - 90) / spanY
  )));
  view.x = -((minX + maxX) / 2) * view.k;
  view.y = -((minY + maxY) / 2) * view.k;
  draw();
}

/* ------------------------------------------------------------- visibility */
let isolated = null;                    // set by double-click, cleared by search or reset
const visibleNode = n => !hiddenTypes.has(n.type) &&
  (!isolated || isolated.has(n.id)) &&
  (!query || n.label.toLowerCase().includes(query) || n.id.toLowerCase().includes(query));
const visibleLink = l => !hiddenRels.has(l.relation) && visibleNode(l.s) && visibleNode(l.t);

function related(id) {
  if (!id) return null;
  const set = new Set(neighbours.get(id));
  set.add(id);
  return set;
}

/* ---------------------------------------------------------------- drawing */
function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

const toScreen = n => ({
  x: n.x * view.k + view.x + canvas.clientWidth / 2,
  y: n.y * view.k + view.y + canvas.clientHeight / 2,
});

function draw() {
  ctx.save();
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  const focus = related(selected ? selected.id : (hovered ? hovered.id : null));

  ctx.lineWidth = 1;
  for (const l of links) {
    if (!visibleLink(l)) continue;
    const dim = focus && !(focus.has(l.source) && focus.has(l.target));
    const a = toScreen(l.s), b = toScreen(l.t);
    ctx.strokeStyle = dim ? "rgba(48,66,80,.28)" : (focus ? "rgba(61,155,253,.55)" : "rgba(48,66,80,.75)");
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    if (!dim && focus) {                       // arrowhead only where it can be read
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      const tipX = b.x - Math.cos(angle) * (l.t.r * view.k + 3);
      const tipY = b.y - Math.sin(angle) * (l.t.r * view.k + 3);
      ctx.beginPath();
      ctx.moveTo(tipX, tipY);
      ctx.lineTo(tipX - Math.cos(angle - 0.4) * 8, tipY - Math.sin(angle - 0.4) * 8);
      ctx.lineTo(tipX - Math.cos(angle + 0.4) * 8, tipY - Math.sin(angle + 0.4) * 8);
      ctx.fillStyle = "rgba(61,155,253,.75)";
      ctx.fill();
    }
  }

  for (const n of nodes) {
    if (!visibleNode(n)) continue;
    const dim = focus && !focus.has(n.id);
    const p = toScreen(n);
    const r = n.r * view.k;
    ctx.globalAlpha = dim ? 0.22 : 1;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.fillStyle = n.colour;
    ctx.fill();
    if (n === selected || n === hovered) {
      ctx.strokeStyle = "#eaf3ff";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.lineWidth = 1;
    }
    // Labels only where they will not turn the canvas into soup: the best-connected
    // entities always, everything else once zoomed in or picked out.
    const named = n === selected || n === hovered
      || (query && query.length > 1)
      || (focus && focus.has(n.id))
      || n.rank < LABEL_RANKS
      || r > 9;
    if (!dim && named) {
      ctx.globalAlpha = 0.92;
      ctx.fillStyle = "#cdddea";
      ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
      ctx.fillText(n.label.length > 34 ? n.label.slice(0, 33) + "\\u2026" : n.label, p.x + r + 5, p.y + 4);
    }
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

function frame() {
  if (step()) {
    // Fit once the layout has taken shape rather than at the end, so the graph is
    // usable while the last of the motion settles.
    if (!autoFitted && temperature < 9) { autoFitted = true; fitView(); }
    draw();
  }
  requestAnimationFrame(frame);
}

/* ----------------------------------------------------------- interaction */
function nodeAt(px, py) {
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (!visibleNode(n)) continue;
    const p = toScreen(n);
    const r = Math.max(n.r * view.k, 6);
    if ((px - p.x) ** 2 + (py - p.y) ** 2 <= r * r) return n;
  }
  return null;
}

canvas.addEventListener("mousemove", event => {
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  if (dragging) {
    dragging.x = (px - view.x - canvas.clientWidth / 2) / view.k;
    dragging.y = (py - view.y - canvas.clientHeight / 2) / view.k;
    draw();
    return;
  }
  if (panning) {
    view.x += px - panning.x; view.y += py - panning.y;
    panning = { x: px, y: py };
    draw();
    return;
  }
  const hit = nodeAt(px, py);
  if (hit !== hovered) { hovered = hit; draw(); }
  if (hit) {
    tip.innerHTML = "<strong>" + esc(hit.label) + "</strong><br>" + esc(hit.type) +
      " \\u00b7 " + hit.degree + " link" + (hit.degree === 1 ? "" : "s");
    tip.style.left = Math.min(px + 14, canvas.clientWidth - 300) + "px";
    tip.style.top = (py + 16) + "px";
    tip.style.opacity = "1";
  } else {
    tip.style.opacity = "0";
  }
});

canvas.addEventListener("mousedown", event => {
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  const hit = nodeAt(px, py);
  if (hit) { dragging = hit; temperature = Math.max(temperature, 6); }
  else { panning = { x: px, y: py }; canvas.classList.add("dragging"); }
});

window.addEventListener("mouseup", () => {
  dragging = null; panning = null;
  canvas.classList.remove("dragging");
});

canvas.addEventListener("click", event => {
  const rect = canvas.getBoundingClientRect();
  const hit = nodeAt(event.clientX - rect.left, event.clientY - rect.top);
  select(hit);
});

canvas.addEventListener("dblclick", event => {
  const rect = canvas.getBoundingClientRect();
  const hit = nodeAt(event.clientX - rect.left, event.clientY - rect.top);
  if (!hit) return;
  query = "";
  document.getElementById("search").value = "";
  isolated = related(hit.id);
  updateStats();
  select(hit);
});

canvas.addEventListener("wheel", event => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left - canvas.clientWidth / 2;
  const py = event.clientY - rect.top - canvas.clientHeight / 2;
  const factor = Math.exp(-event.deltaY * 0.0016);
  const next = Math.max(0.15, Math.min(6, view.k * factor));
  view.x = px - (px - view.x) * (next / view.k);
  view.y = py - (py - view.y) * (next / view.k);
  view.k = next;
  draw();
}, { passive: false });

/* ------------------------------------------------------------- side panel */
function select(node) {
  selected = node;
  const panel = document.getElementById("details");
  if (!node) {
    panel.innerHTML = '<p class="empty">Select an entity to see everything the graph records about it.</p>';
    draw();
    return;
  }
  const rows = Object.entries(node.attributes)
    .filter(([key]) => key !== "type")
    .map(([key, value]) => '<div class="row"><span>' + esc(key) + "</span><strong>" +
      esc(typeof value === "boolean" ? (value ? "Yes" : "No") : value) + "</strong></div>")
    .join("");
  const rels = incident.get(node.id)
    .filter(visibleLink)
    .sort((a, b) => a.relation.localeCompare(b.relation))
    .map(l => {
      const out = l.source === node.id;
      const other = out ? l.t : l.s;
      const detail = Object.entries(l.attributes)
        .map(([key, value]) => esc(key) + "=" + esc(value)).join(", ");
      return '<button class="rel" data-id="' + esc(other.id) + '">' +
        '<span class="verb">' + esc(l.relation) + "</span> " +
        '<span class="dir">' + (out ? "\\u2192" : "\\u2190") + "</span> " +
        esc(other.label) + (detail ? '<br><span class="dir">' + detail + "</span>" : "") +
        "</button>";
    }).join("");
  panel.innerHTML =
    '<span class="pill"><i style="width:8px;height:8px;border-radius:50%;background:' +
      node.colour + '"></i>' + esc(node.type) + "</span>" +
    "<h3 style='margin:10px 0 4px;font-size:15px'>" + esc(node.label) + "</h3>" +
    "<div style='color:var(--ink-soft);font-family:var(--mono);font-size:11px'>" +
      esc(node.id) + " \\u00b7 " + node.degree + " relationship" + (node.degree === 1 ? "" : "s") + "</div>" +
    (rows ? "<div style='margin-top:14px'>" + rows + "</div>" : "") +
    (rels ? "<h2 style='margin-top:18px'>Relationships</h2>" + rels : "");
  panel.querySelectorAll(".rel").forEach(button => {
    button.addEventListener("click", () => {
      const next = byId.get(button.dataset.id);
      if (next) { select(next); centre(next); }
    });
  });
  draw();
}

function centre(node) {
  view.x = -node.x * view.k;
  view.y = -node.y * view.k;
  draw();
}

/* --------------------------------------------------------------- filters */
function buildFilters() {
  const types = new Map(), rels = new Map();
  for (const n of nodes) types.set(n.type, (types.get(n.type) || 0) + 1);
  for (const l of links) rels.set(l.relation, (rels.get(l.relation) || 0) + 1);

  const typeBox = document.getElementById("typeFilters");
  typeBox.innerHTML = [...types.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => {
    const colour = (nodes.find(n => n.type === type) || {}).colour || "#888";
    return '<button class="filter" data-type="' + esc(type) + '">' +
      '<i class="dot" style="background:' + colour + '"></i>' +
      '<span class="name">' + esc(type) + "</span>" +
      '<span class="count">' + count + "</span></button>";
  }).join("");
  typeBox.querySelectorAll("[data-type]").forEach(button => {
    button.addEventListener("click", () => {
      const type = button.dataset.type;
      if (hiddenTypes.has(type)) hiddenTypes.delete(type); else hiddenTypes.add(type);
      button.classList.toggle("off", hiddenTypes.has(type));
      updateStats(); draw();
    });
  });

  const relBox = document.getElementById("relFilters");
  relBox.innerHTML = [...rels.entries()].sort((a, b) => b[1] - a[1]).map(([rel, count]) =>
    '<button class="filter" data-rel="' + esc(rel) + '">' +
      '<span class="name">' + esc(rel) + "</span>" +
      '<span class="count">' + count + "</span></button>").join("");
  relBox.querySelectorAll("[data-rel]").forEach(button => {
    button.addEventListener("click", () => {
      const rel = button.dataset.rel;
      if (hiddenRels.has(rel)) hiddenRels.delete(rel); else hiddenRels.add(rel);
      button.classList.toggle("off", hiddenRels.has(rel));
      updateStats(); draw();
    });
  });
}

function updateStats() {
  const n = nodes.filter(visibleNode).length;
  const l = links.filter(visibleLink).length;
  document.getElementById("stats").textContent =
    n + " / " + nodes.length + " entities \\u00b7 " + l + " / " + links.length + " relationships";
}

document.getElementById("search").addEventListener("input", event => {
  query = event.target.value.trim().toLowerCase();
  isolated = null;
  updateStats(); draw();
});
document.getElementById("fitView").addEventListener("click", fitView);
document.getElementById("resetView").addEventListener("click", () => {
  isolated = null; query = "";
  document.getElementById("search").value = "";
  hiddenTypes.clear(); hiddenRels.clear();
  document.querySelectorAll(".filter.off").forEach(b => b.classList.remove("off"));
  select(null); updateStats(); fitView();
});
document.getElementById("restart").addEventListener("click", () => {
  temperature = AREA / 8;
  autoFitted = false;
});

window.addEventListener("resize", resize);
document.getElementById("footer").textContent = DATA.footer;
buildFilters();
updateStats();
resize();
frame();
</script>
</body>
</html>
"""


def render(prepared: dict[str, Any], *, title: str, footer: str) -> str:
    payload = json.dumps(
        {"nodes": prepared["nodes"], "links": prepared["links"], "footer": footer},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # The payload sits in a <script type="application/json"> block, so the only sequence
    # that can break out of it is a literal closing script tag.
    payload = payload.replace("</", "<\\/")
    return (
        PAGE.replace("__TITLE__", title.replace("<", "&lt;"))
        .replace("__DATA__", payload)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a GraphML or node-link JSON graph as one self-contained "
        "interactive HTML file."
    )
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--focus", default="", help="limit to one entity's neighbourhood")
    parser.add_argument("--depth", type=int, default=2, help="hops from --focus")
    args = parser.parse_args(argv)

    graph = load_graph(args.graph)
    prepared = prepare(graph, focus=args.focus, depth=args.depth)
    if not prepared["nodes"]:
        print("The graph contains no renderable nodes.", file=sys.stderr)
        return 2

    title = args.title or (
        graph.get("graph", {}).get("name")
        or args.graph.stem.replace("_", " ").title()
    )
    if args.focus:
        title = f"{title} - {args.focus} neighbourhood"

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    types = Counter(node["type"] for node in prepared["nodes"])
    footer = (
        f"{len(prepared['nodes'])} entities, {len(prepared['links'])} relationships "
        f"from {args.graph.name} · "
        + ", ".join(f"{name} {count}" for name, count in types.most_common(6))
        + f" · generated {stamp}"
    )

    out = args.out or (
        DEFAULT_OUTPUT if args.graph == DEFAULT_GRAPH else args.graph.with_suffix(".html")
    )
    out.write_text(render(prepared, title=title, footer=footer), encoding="utf-8")
    size = out.stat().st_size
    print(f"wrote {out} ({size:,} bytes)")
    print(f"  {len(prepared['nodes'])} entities, {len(prepared['links'])} relationships")
    print("  self-contained: open it directly in a browser, no server needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
