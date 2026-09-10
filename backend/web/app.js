"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Mirrors TYPE_COLOURS in building_kg/pdf_to_kg.py. The first block is the source
// packet corpus, the second the corporate and infrastructure corpus.
const TYPE_COLOURS = {
  actor: "#ff4b4b",
  force: "#ff7043",
  organisation: "#ffb84b",
  programme: "#d4a017",
  capability: "#4b9bff",
  materiel: "#5c6bc0",
  infrastructure: "#4bff9b",
  industry: "#26a69a",
  location: "#b84bff",
  condition: "#8d6e63",
  metric: "#78909c",
  company: "#29b6f6",
  person: "#ec407a",
  site: "#66bb6a",
  equipment: "#7e57c2",
  jurisdiction: "#ab47bc",
  sector: "#c0ca33",
  document: "#a1887f",
  event: "#ffa726",
  unknown: "#888888",
};

// Graphs built before the pipeline's type vocabulary use display labels of their own.
// Mapping them onto the same palette keeps one visual language across old and new
// extractions in the same graph.
const TYPE_ALIASES = {
  // Legacy node-link graphs.
  "state / polity": "actor",
  "military force / org": "force",
  "location / infrastructure": "infrastructure",
  "concept / capability": "capability",
  "system / equipment": "materiel",
  entity: "unknown",
  // graph.graphml keeps its type names capitalised; only Facility and
  // Organisation differ from the palette's own names.
  facility: "site",
  organisation: "organisation",
};

// Schema-guided extraction introduces a type per selected schema term, so the
// palette above cannot enumerate them. An unmapped type gets a stable colour
// derived from its own name: distinct types stay visually distinct, and the
// same type keeps the same colour between sessions.
const SCHEMA_PALETTE = [
  "#ff4b4b", "#ff7043", "#ffb84b", "#d4a017", "#4b9bff", "#5c6bc0",
  "#4bff9b", "#26a69a", "#b84bff", "#78909c", "#29b6f6", "#ec407a",
  "#66bb6a", "#7e57c2", "#ab47bc", "#c0ca33", "#a1887f", "#ffa726",
];

// The backend serves a strict Content Security Policy without unsafe inline
// styles. Keep type colours in stylesheet classes so nodes and legends render
// correctly under that policy.
const TYPE_CLASS_PALETTE = [...new Set([...Object.values(TYPE_COLOURS), ...SCHEMA_PALETTE])];

function typeColourClass(type) {
  const key = String(type || "").trim().toLowerCase();
  const mapped = TYPE_COLOURS[key] || TYPE_COLOURS[TYPE_ALIASES[key]] || "";
  if (mapped) return `type-colour-${TYPE_CLASS_PALETTE.indexOf(mapped)}`;
  if (!key) return `type-colour-${TYPE_CLASS_PALETTE.indexOf(TYPE_COLOURS.unknown)}`;
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) >>> 0;
  }
  const colour = SCHEMA_PALETTE[hash % SCHEMA_PALETTE.length];
  return `type-colour-${TYPE_CLASS_PALETTE.indexOf(colour)}`;
}

// Provenance the extraction pipeline attaches to a record, in the order an analyst
// reads it: what kind of claim, where it came from, then how strongly it is held.
const PROVENANCE_FIELDS = [
  // Where the record came from, before anything else: an analyst deciding on a
  // candidate needs the document and page in front of them.
  ["source_document", "Source document"],
  ["sources", "Source documents"],
  ["source_page", "Page"],
  ["media_type", "Document kind"],
  ["page_count", "Pages"],
  ["character_count", "Characters"],
  ["ingested_at", "Ingested"],
  ["document_sha256", "Document SHA-256"],
  // Schema-guided records next: what the record is in the schema's own terms,
  // then how strongly the source holds it, then the qualifiers it carried.
  ["record_kind", "Record kind"],
  ["type_id", "Schema type"],
  ["predicate_id", "Predicate"],
  ["canonical_name", "Canonical name"],
  ["identifier", "Identifier"],
  ["polarity", "Polarity"],
  ["certainty", "Certainty"],
  ["modality", "Modality"],
  ["source_direction", "Source direction"],
  ["review_action", "Required review"],
  ["model_action", "Model action permitted"],
  ["temporal_requirement", "Temporal requirement"],
  ["valid_time_text", "Valid time (as stated)"],
  ["observation_time_text", "Observation time (as stated)"],
  ["assessor_or_authority_text", "Assessor or authority"],
  ["uncertainty_text", "Uncertainty (as stated)"],
  ["analytical_confidence_text", "Analytical confidence"],
  ["finding_text", "Finding"],
  ["qualitative_value_text", "Qualitative value"],
  ["quantitative_value_text", "Quantitative value"],
  ["unit_text", "Unit"],
  ["qualifier_text", "Qualifier"],
  ["baseline_or_scenario_text", "Baseline or scenario"],
  ["value_set_id", "Value set"],
  ["value_id", "Controlled value"],
  ["missing_required_qualifiers", "Missing qualifiers"],
  ["evidence_text", "Evidence (as stated)"],
  ["mention_count", "Mentions in source"],
  ["status", "Claim status"],
  ["claim_id", "Claim reference"],
  ["doc_type", "Document type"],
  ["doc_origin", "Originator"],
  ["confidence", "Extraction confidence"],
  ["source_grade", "Source grade"],
  ["phia", "PHIA yardstick"],
  ["time_scope", "Time scope"],
  // "Role" covers both an office held on an OFFICER_OF edge and an
  // organisation's role in the network on a GraphML node.
  ["role", "Role"],
  ["role_hint", "Role"],
  ["percent", "Disclosed holding"],
  ["stake_pct", "Stake (%)"],
  ["value", "Value"],
  ["contract_value_uah", "Contract value (UAH)"],
  ["annual_value_uah", "Annual value (UAH)"],
  ["commodity", "Commodity"],
  ["sole_source", "Sole source"],
  ["non_executive", "Non-executive"],
  ["valid_from", "Valid from"],
  ["valid_to", "Valid to"],
  // Attributes carried by graph.graphml entities.
  ["aliases", "Also known as"],
  ["sector", "Sector"],
  ["kind", "Kind"],
  ["criticality", "Criticality"],
  ["capacity", "Capacity"],
  ["commissioned", "Commissioned"],
  ["city", "City"],
  ["country", "Country"],
  ["jurisdiction", "Jurisdiction"],
  ["nationality", "Nationality"],
  ["domestic", "Domestic"],
  ["independent", "Independent"],
  ["derived", "Derived"],
  ["note", "Note"],
  ["description", "Description"],
  ["assertion_id", "Assertion id"],
];

// GraphML declares real booleans, so render them as words rather than "false".
function provenanceValue(value) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

const state = {
  view: "overview",
  summary: null,
  health: null,
  queue: { items: [], total: 0, offset: 0, limit: 20, has_more: false },
  reviewIndex: 0,
  reviewOffset: 0,
  reviewFilters: { kind: "all", status: "unreviewed", query: "", document: "" },
  graph: null,
  graphFocus: null,
  graphFocusItem: null,
  graphSelectedId: null,
  graphLayout: "auto",
  messages: [
    {
      role: "assistant",
      answer: "Ask a question about the graph. Deterministic mode handles supported counts, rankings and paths. GraphRAG becomes selectable only when its configured model endpoint is reachable, and model answers list the records supplied to it.",
      trace: "No answering mode has run yet.",
      nodes: [],
      edges: [],
    },
  ],
  pendingDecision: null,
  jobs: [],
  documents: [],
  askBusy: false,
  askMode: null,
  drawerReturnFocus: null,
  // The extraction schema down-selection. `draft` is what the analyst is
  // editing, `preview` is the server's resolution of it: which predicates
  // survive, which the reference closure dropped, and how large the compiled
  // prompts are. Nothing is committed until Save.
  schema: {
    catalogue: null,
    draft: { modules: new Set(), entities: new Set() },
    preview: null,
    saved: null,
    filter: { query: "", module: "all" },
    dirty: false,
    busy: false,
    suggestionTopic: "",
    suggestion: null,
    suggestBusy: false,
    missionText: "",
    missionSuggestion: null,
    missionBusy: false,
  },
  extractionMode: null,
  // Pre-extraction LangExtract settings: how many recall passes each chunk
  // gets, and how many characters go to the model per call. Mirrors
  // PDFToKnowledgeGraph/SchemaGuidedExtractor's own constructor defaults
  // (backend/building_kg/pdf_to_kg.py, backend/building_kg/schema_extractor.py)
  // so the box starts showing what would run anyway if left untouched.
  extractionSettings: { extraction_passes: 2, max_char_buffer: 1500 },
  // PRIMROSE is additive to the evidence workflow above. These views always
  // read the live Python APIs; there is no bundled or generated demo state.
  priorities: {
    bootstrap: null,
    activeQueue: "collect",
    selected: null,
    filter: "",
  },
  target: {
    meta: null,
    candidates: [],
    total: 0,
    query: "",
    selectedId: "",
    phase: "basic",
    preview: null,
    job: null,
    busy: false,
  },
  methods: {
    catalogue: null,
    profiles: null,
    query: "",
    family: "all",
  },
  audit: {
    items: [],
    adapters: null,
  },
  projects: { items: [], active_id: "default" },
  corpus: { available: false, directory: "", items: [], selected: new Set() },
  growthBatch: [],
  // Corpus-tailored LangExtract example sets, drafted by the model from real
  // documents. Selecting one swaps heuristic extraction's few-shot examples
  // away from the two hardcoded synthetic sets.
  exampleSets: [],
  selectedExampleSetId: "",
};

function esc(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-GB").format(Number(value || 0));
}

function short(value, max = 42) {
  const text = String(value || "");
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function statusPill(status = "unreviewed") {
  return `<span class="status-pill status-${esc(status)}">${esc(status)}</span>`;
}

function invalidateDerivedViews() {
  state.graph = null;
  state.priorities.bootstrap = null;
  state.priorities.selected = null;
  state.target.candidates = [];
  state.target.total = 0;
  state.target.preview = null;
  state.target.job = null;
  state.audit.items = [];
  state.audit.adapters = null;
}

function apiPath(path) {
  // Both the local Python server and the hosted same-origin proxy expose the
  // authoritative API at /api/*.
  return path;
}

async function api(path, options = {}) {
  const response = await fetch(apiPath(path), {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof Blob) ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === "object" ? payload.detail || payload.error : payload;
    throw new Error(message || `Request failed (${response.status})`);
  }
  return payload;
}

function toast(title, message = "", error = false) {
  const stack = $("#toastStack");
  const item = document.createElement("div");
  item.className = `toast${error ? " is-error" : ""}`;
  item.innerHTML = `<strong>${esc(title)}</strong>${message ? `<span>${esc(message)}</span>` : ""}`;
  stack.append(item);
  window.setTimeout(() => item.remove(), 4200);
}

function pageHead(kicker, title, description, actions = "") {
  return `<div class="page-head">
    <div><span class="eyebrow">${esc(kicker)}</span><h1>${esc(title)}</h1><p>${esc(description)}</p></div>
    ${actions ? `<div class="head-actions">${actions}</div>` : ""}
  </div>`;
}

function loading() {
  $("#mainContent").innerHTML = `<div class="loading-state"><div class="loader"></div><p>Loading…</p></div>`;
}

async function refreshSummary() {
  state.summary = await api("/api/summary");
  $("#workspaceName").textContent = state.summary.graph_name;
  $("#reviewNavCount").textContent = formatNumber(state.summary.review.unreviewed);
}

function updateNavigation() {
  $$(".nav-item").forEach((item) => {
    const active = item.dataset.view === state.view;
    item.classList.toggle("is-active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  document.title = `${state.view[0].toUpperCase()}${state.view.slice(1)} — AI Enabled Knowledge Graph`;
}

async function setView(view, { replace = false } = {}) {
  const allowed = ["overview", "review", "explore", "ask", "sources", "schema", "priorities", "target", "methods", "audit"];
  state.view = allowed.includes(view) ? view : "overview";
  if (replace) history.replaceState({}, "", `#${state.view}`);
  else history.pushState({}, "", `#${state.view}`);
  updateNavigation();
  $("#sidebar").classList.remove("is-open");
  $("#menuButton").setAttribute("aria-expanded", "false");
  loading();
  try {
    if (state.view === "overview") renderOverview();
    if (state.view === "review") {
      await loadReview();
      renderReview();
    }
    if (state.view === "explore") {
      if (!state.graph) await loadGraph("");
      else renderExplore();
      watchGraphGrowth();
    }
    if (state.view === "ask") renderAsk();
    if (state.view === "sources") {
      await Promise.all([loadJobs(), loadCorpus(), loadExampleSets()]);
      renderSources();
      watchGraphGrowth();
    }
    if (state.view === "schema") {
      await loadSchema();
      renderSchema();
    }
    if (state.view === "priorities") {
      await loadPriorities();
      renderPriorities();
    }
    if (state.view === "target") {
      await loadTargetDevelopment();
      renderTargetDevelopment();
    }
    if (state.view === "methods") {
      await loadMethods();
      renderMethods();
    }
    if (state.view === "audit") {
      await loadAuditStatus();
      renderAuditStatus();
    }
  } catch (error) {
    renderFailure(error);
  }
  $("#mainContent").focus({ preventScroll: true });
}

function renderFailure(error) {
  $("#mainContent").innerHTML = `<div class="page"><div class="empty-state panel"><div><strong>Unable to load this view</strong><p>${esc(error.message)}</p><button class="button button-primary" data-action="retry">Try again</button></div></div></div>`;
}

function renderOverview() {
  const s = state.summary;
  const review = s.review;
  const typeRows = s.entity_types.slice(0, 6).map((row) => `
    <li><button class="list-row" data-action="review-type" data-value="${esc(row.name)}"><span><strong>${esc(row.name)}</strong><span>Review this entity type</span></span><b class="count-pill">${formatNumber(row.count)}</b></button></li>`).join("");
  const connected = s.top_connected.slice(0, 6).map((row) => `
    <li><button class="list-row" data-action="load-graph" data-id="${esc(row.id)}"><span><strong>${esc(row.label)}</strong><span>${esc(row.type)} · ${row.degree} connections</span></span>${statusPill(row.status)}</button></li>`).join("");

  $("#mainContent").innerHTML = `<div class="page">
    <section class="hero">
      <div>
        <span class="eyebrow">Evidence-first graph workflow</span>
        <h1>Turn extracted information into a reviewed, queryable graph.</h1>
        <p>A cleaner first-draft workspace for reviewing candidate entities and relationships, exploring their context and asking traceable graph questions.</p>
        <div class="hero-actions">
          <button class="button button-primary" data-view-target="review">Continue review</button>
          <button class="button button-secondary" data-view-target="explore">Explore graph</button>
        </div>
      </div>
      <div class="hero-review">
        <span class="eyebrow">Review completion</span>
        <strong>${review.completion}%</strong>
        <progress class="progress-track" value="${review.completion}" max="100" aria-label="Review completion">${review.completion}%</progress>
        <small>${formatNumber(review.accepted + review.rejected)} of ${formatNumber(review.total)} candidates decided</small>
      </div>
    </section>

    <div class="workflow" aria-label="Application workflow">
      <div class="workflow-step is-done"><span class="eyebrow">01</span><strong>Collect</strong></div>
      <div class="workflow-step is-done"><span class="eyebrow">02</span><strong>Extract</strong></div>
      <div class="workflow-step is-active"><span class="eyebrow">03</span><strong>Review</strong></div>
      <div class="workflow-step"><span class="eyebrow">04</span><strong>Explore</strong></div>
      <div class="workflow-step"><span class="eyebrow">05</span><strong>Ask</strong></div>
    </div>

    <div class="metric-grid">
      <div class="metric-card"><span>Entities</span><strong>${formatNumber(s.nodes)}</strong><small>Loaded graph nodes</small></div>
      <div class="metric-card"><span>Relationships</span><strong>${formatNumber(s.relationships)}</strong><small>Recorded connections</small></div>
      <div class="metric-card"><span>Awaiting review</span><strong>${formatNumber(review.unreviewed)}</strong><small>Nodes and relationships</small></div>
      <div class="metric-card"><span>Accepted</span><strong>${formatNumber(review.accepted)}</strong><small>Analyst-reviewed records</small></div>
    </div>

    <div class="content-grid">
      <section class="panel">
        <div class="panel-head"><div><h2>Most connected entities</h2><p>Connectivity supports exploration; it is not a criticality judgement.</p></div><button class="text-action" data-view-target="explore">Open explorer</button></div>
        <ul class="clean-list">${connected || "<li>No nodes loaded.</li>"}</ul>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>Graph composition</h2><p>Highest-frequency entity classes.</p></div></div>
        <ul class="clean-list">${typeRows || "<li>No type information supplied.</li>"}</ul>
      </section>
    </div>
  </div>`;
}

async function loadReview() {
  const f = state.reviewFilters;
  const params = new URLSearchParams({ kind: f.kind, status: f.status, q: f.query, document: f.document || "", limit: "30", offset: String(state.reviewOffset) });
  state.queue = await api(`/api/review?${params}`);
  state.reviewIndex = Math.min(state.reviewIndex, Math.max(0, state.queue.items.length - 1));
}

function renderReview() {
  const queue = state.queue;
  const item = queue.items[state.reviewIndex];
  const queueRows = queue.items.map((row, index) => `
    <button class="queue-item${index === state.reviewIndex ? " is-active" : ""}" data-action="queue-select" data-index="${index}">
      <strong>${esc(short(row.label, 56))}</strong><span>${esc(row.kind)} · ${esc(row.type)}</span>
    </button>`).join("");
  const card = item ? renderReviewCard(item) : `<div class="review-card empty-state"><div><strong>No matching candidates</strong><p>Change the filters or continue with another workflow step.</p></div></div>`;
  const first = queue.total ? queue.offset + 1 : 0;
  const last = Math.min(queue.offset + queue.items.length, queue.total);

  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Analyst review", "Review candidate records", "Accept or reject extracted entities and relationships. Each decision is recorded in the audit trail.", `<button class="button button-secondary" data-view-target="overview">Back to overview</button>`)}
    <div class="filter-bar">
      <label class="sr-only" for="reviewKind">Candidate kind</label><select id="reviewKind" aria-label="Candidate kind"><option value="all">All records</option><option value="node">Entities</option><option value="edge">Relationships</option></select>
      <label class="sr-only" for="reviewStatus">Review status</label><select id="reviewStatus" aria-label="Review status"><option value="unreviewed">Awaiting review</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option><option value="accepted,rejected,unreviewed">All statuses</option></select>
      <label class="sr-only" for="reviewSearch">Filter review queue</label><input id="reviewSearch" type="search" value="${esc(state.reviewFilters.query)}" placeholder="Filter this queue">
      <button class="button button-secondary" data-action="apply-review-filters">Apply</button>
      ${state.reviewFilters.document ? `<button class="type-pill" data-action="clear-document-filter" title="Show every document">Document: ${esc(short(state.reviewFilters.document, 34))} ×</button>` : ""}
    </div>
    <div class="review-layout">
      ${card}
      <aside class="panel">
        <div class="panel-head"><div><h2>Queue</h2><p>${formatNumber(queue.total)} matching records</p></div><span class="count-pill">${queue.items.length ? state.reviewIndex + 1 : 0} / ${queue.items.length}</span></div>
        <div class="queue-list">${queueRows || "<p>No records in this queue.</p>"}</div>
        <div class="queue-pagination"><button class="button button-quiet" data-action="review-page" data-offset="${Math.max(0, queue.offset - queue.limit)}" ${queue.offset === 0 ? "disabled" : ""}>← Previous</button><small>${formatNumber(first)}–${formatNumber(last)} of ${formatNumber(queue.total)}</small><button class="button button-quiet" data-action="review-page" data-offset="${queue.offset + queue.limit}" ${queue.has_more ? "" : "disabled"}>Next →</button></div>
      </aside>
    </div>
  </div>`;
  $("#reviewKind").value = state.reviewFilters.kind;
  $("#reviewStatus").value = state.reviewFilters.status;
}

// Where a candidate came from, in one line. `source_documents` is the store's
// normalised view of the several ways a record can name its origin.
function documentLine(item) {
  const documents = item.source_documents || [];
  if (!documents.length) return "";
  const page = item.metadata?.source_page;
  const names = documents.map((name) => short(name, 40)).join(", ");
  return `${esc(names)}${page ? ` · page ${esc(page)}` : ""}`;
}

function renderReviewCard(item) {
  const relation = item.kind === "edge" ? `<div class="relation-strip"><strong>${esc(item.source.label)}</strong><span>${esc(item.relation)}</span><strong>${esc(item.target.label)}</strong></div>` : "";
  const secondary = item.kind === "node" ? `${formatNumber(item.degree)} connections` : item.confidence != null ? `Confidence ${esc(item.confidence)}` : "Relationship candidate";
  const actions = item.status === "unreviewed" ? `
    <button class="text-action" data-action="review" data-decision="accepted" data-kind="${item.kind}" data-id="${esc(item.id)}">Accept</button>
    <button class="text-action is-danger" data-action="review" data-decision="rejected" data-kind="${item.kind}" data-id="${esc(item.id)}">Reject</button>
    <button class="text-action skip" data-action="review-skip">Skip for now →</button>` : `
    <button class="text-action" data-action="review" data-decision="unreviewed" data-kind="${item.kind}" data-id="${esc(item.id)}">Return to queue</button>
    <button class="text-action skip" data-action="open-item" data-kind="${item.kind}" data-id="${esc(item.id)}">Open details →</button>`;
  const documents = documentLine(item);
  return `<article class="review-card">
    <div class="review-meta"><span class="type-pill">${esc(item.kind === "node" ? "Entity" : "Relationship")}</span>${statusPill(item.status)}<span class="count-pill">${esc(item.type)}</span>${item.host_derived ? `<span class="type-pill">Host provenance</span>` : ""}</div>
    <h2>${esc(item.label)}</h2>
    <p class="eyebrow">${esc(secondary)}${documents ? ` · ${documents}` : ""}</p>
    ${relation}
    <blockquote class="evidence-block"><span class="eyebrow">Source context</span>${esc(item.evidence)}</blockquote>
    ${item.reason ? `<p><strong>Recorded reason:</strong> ${esc(item.reason)}</p>` : ""}
    <div class="review-actions">${actions}</div>
  </article>`;
}

async function applyReview(kind, id, decision, reason = "") {
  try {
    await api("/api/review", {
      method: "POST",
      body: JSON.stringify({ kind, id, decision, reason, analyst: "Local analyst" }),
    });
    invalidateDerivedViews();
    toast(`Record ${decision}`, reason || "The audit trail has been updated.");
    await refreshSummary();
    if (state.view === "review") {
      await loadReview();
      if (!state.queue.items.length && state.reviewOffset > 0) {
        state.reviewOffset = Math.max(0, state.reviewOffset - state.queue.limit);
        await loadReview();
      }
      renderReview();
    }
    if ($("#detailDrawer").classList.contains("is-open")) await openItem(kind, id);
  } catch (error) {
    toast("Decision not saved", error.message, true);
  }
}

function promptDecision(kind, id, decision) {
  state.pendingDecision = { kind, id, decision };
  $("#decisionTitle").textContent = decision === "rejected" ? "Reject candidate" : "Record decision";
  $("#decisionPrompt").textContent = decision === "rejected" ? "Record a concise reason for the audit trail." : "Add an optional note for the audit trail.";
  $("#decisionReason").placeholder = decision === "rejected" ? "Why is this candidate being rejected?" : "Optional analyst note";
  $("#decisionReason").value = "";
  $("#decisionConfirm").textContent = decision === "rejected" ? "Reject" : "Confirm";
  $("#decisionConfirm").className = `button ${decision === "rejected" ? "button-danger" : "button-primary"}`;
  $("#decisionDialog").showModal();
  $("#decisionReason").focus();
}

// Hierarchical is the one layout where a single hop is too shallow to read as
// a tree at all - the root plus its direct neighbours is just two flat rows.
// Every other layout only repositions whatever is already loaded, so they
// stay on the cheaper single-hop fetch.
function graphDepthForLayout(mode) {
  return mode === "hierarchical" ? 2 : 1;
}

async function loadGraph(focus = "") {
  const depth = graphDepthForLayout(state.graphLayout);
  // A hub node's direct neighbours alone can fill the normal budget, leaving
  // no room for a second hop to ever be selected - so depth 2 gets a bigger
  // budget specifically so hop-2 nodes have somewhere to land.
  // The overview (no focus) requests the whole graph rather than a fixed
  // snippet - state.summary.nodes is the exact live entity count, so this
  // always covers the full graph regardless of project size.
  const limit = focus ? (depth > 1 ? "90" : "45") : String(Math.max(30, state.summary?.nodes || 0));
  const params = new URLSearchParams({ focus, depth: String(depth), limit, statuses: "accepted,unreviewed" });
  state.graph = await api(`/api/graph?${params}`);
  state.graphFocus = state.graph.focus_id;
  state.graphFocusItem = state.graphFocus ? await api(`/api/items/node/${encodeURIComponent(state.graphFocus)}`) : null;
  state.graphSelectedId = null;
  graphZoom = 1;
  if (state.view === "explore") renderExplore();
}

// A single click on a graph node only opens it in the left-hand panel; it
// does not disturb the current pan/zoom or re-centre the layout. Only
// centring (double-click, the search box, or a list row) calls loadGraph.
async function selectGraphNode(id) {
  state.graphSelectedId = id;
  state.graphFocusItem = await api(`/api/items/node/${encodeURIComponent(id)}`);
  if (state.view !== "explore") return;
  renderGraphFocusPanel();
  drawGraph();
}

function graphFocusPanelHtml() {
  const focus = state.graphFocusItem;
  if (!focus) {
    return `<span class="eyebrow">Graph overview</span><h2>Choose an entity</h2><p>Select a node to open its details; double-click a node to centre its immediate neighbourhood.</p>`;
  }
  // A single click only opens this panel; it does not centre the graph (see
  // selectGraphNode). Pulling into target development is offered only once
  // the entity is actually the centred neighbourhood, not merely selected.
  const isCentred = state.graph?.focus_id === focus.id;
  return `
    <span class="type-pill"><i class="type-dot ${typeColourClass(focus.type)}"></i>${esc(focus.type)}</span>${statusPill(focus.status)}
    <h2>${esc(focus.label)}</h2>
    <p>${esc(focus.evidence)}</p>
    <div class="detail-section"><div class="focus-stat"><span>Connections</span><strong>${formatNumber(focus.degree)}</strong></div><div class="focus-stat"><span>Review status</span><strong>${esc(focus.status)}</strong></div></div>
    <div class="detail-section"><h3>Immediate context</h3><ul class="clean-list">${focus.neighbours.slice(0, 8).map((n) => `<li><button class="list-row" data-action="load-graph" data-id="${esc(n.id)}"><span><strong>${esc(n.label)}</strong><span>${esc(n.relation)}</span></span></button></li>`).join("")}</ul></div>
    <div class="head-actions">
      <button class="button button-secondary" data-action="open-item" data-kind="node" data-id="${esc(focus.id)}">Open full details</button>
      ${isCentred ? `<button class="button button-primary" data-action="explore-open-target" data-id="${esc(focus.id)}">Pull into target development</button>` : ""}
    </div>`;
}

function renderGraphFocusPanel() {
  const panel = $("#graphFocusPanel");
  if (panel) panel.innerHTML = graphFocusPanelHtml();
}

function renderExplore() {
  const focus = state.graphFocusItem;
  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Graph explorer", "Explore structure and context", "Start with one entity and expand its immediate neighbourhood. Rejected records are excluded from this view.", `<a class="button button-secondary" href="${apiPath("/api/export/graph")}">Export graph</a>`)}
    <div class="explore-grid">
      <section class="panel graph-panel">
        <form class="graph-toolbar" id="graphSearchForm"><label class="sr-only" for="graphSearch">Entity to centre in the graph</label><input class="field" id="graphSearch" placeholder="Centre on an entity" value="${esc(focus?.label || "")}"><button class="button button-secondary">Centre</button><button type="button" class="button button-quiet" data-action="graph-reset">Overview</button><label class="sr-only" for="graphLayoutSelect">Graph layout</label><select class="field" id="graphLayoutSelect" aria-label="Graph layout">${Object.entries(GRAPH_LAYOUTS).map(([value, label]) => `<option value="${value}" ${state.graphLayout === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></form>
        <div class="graph-canvas" id="graphCanvas"><svg id="graphSvg" role="img" aria-label="Knowledge graph neighbourhood"></svg><div class="graph-legend"><span><i class="legend-dot accepted"></i>Accepted</span><span><i class="legend-dot"></i>Unreviewed</span><span class="legend-rule" id="graphTypeLegend"></span></div><div class="graph-tooltip" id="graphTooltip" role="tooltip" hidden></div><div class="graph-zoom-controls" role="group" aria-label="Zoom graph"><button type="button" class="button button-quiet" data-action="graph-zoom-out" aria-label="Zoom out">−</button><span class="graph-zoom-label" id="graphZoomLabel">100%</span><button type="button" class="button button-quiet" data-action="graph-zoom-in" aria-label="Zoom in">+</button><button type="button" class="button button-quiet" data-action="graph-zoom-reset" aria-label="Reset zoom">Reset</button></div></div>
      </section>
      <aside class="panel focus-panel" id="graphFocusPanel">${graphFocusPanelHtml()}</aside>
    </div>
  </div>`;
  requestAnimationFrame(() => { drawGraph(); centerGraphOnRoot(); });
  $("#graphSearchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadGraph($("#graphSearch").value);
  });
  $("#graphLayoutSelect").addEventListener("change", async (event) => {
    const previousDepth = graphDepthForLayout(state.graphLayout);
    state.graphLayout = event.target.value;
    graphZoom = 1;
    // Other layouts only reposition the nodes already on screen; hierarchical
    // needs a second hop of data the current fetch may not have, so only that
    // transition (in either direction) is worth a re-fetch.
    if (graphDepthForLayout(state.graphLayout) !== previousDepth) {
      await loadGraph(state.graphFocus || "");
    } else {
      drawGraph();
      centerGraphOnRoot();
    }
  });
}

// Rings and grid cells are sized from an estimated label footprint, not a
// fixed node count, so labels never overlap a neighbour regardless of how
// many nodes are in view. Content that needs more room than the panel grows
// the SVG height (or, for radial layout, the x-radius is compressed to fit
// the panel width) and the panel scrolls rather than compressing spacing.
const GRAPH_MIN_ARC = 92;
const GRAPH_COL_WIDTH = 170;
const GRAPH_ROW_HEIGHT = 58;

const GRAPH_LAYOUTS = {
  auto: "Auto layout",
  grid: "Grid",
  radial: "Radial",
  hierarchical: "Hierarchical",
  circular: "Circular",
  force: "Force-directed",
};

function layoutGrid(nodes, containerWidth) {
  const marginX = 90;
  const marginY = 55;
  const usableWidth = Math.max(containerWidth - marginX, GRAPH_COL_WIDTH);
  const count = nodes.length || 1;
  const columns = Math.max(1, Math.min(count, Math.floor(usableWidth / GRAPH_COL_WIDTH) + 1));
  const rows = Math.ceil(count / columns);
  const positions = new Map();
  nodes.forEach((node, index) => {
    const col = index % columns;
    const row = Math.floor(index / columns);
    const colsInRow = Math.min(columns, nodes.length - row * columns);
    const rowWidth = (colsInRow - 1) * GRAPH_COL_WIDTH;
    const startX = (containerWidth - rowWidth) / 2;
    positions.set(node.id, { x: startX + col * GRAPH_COL_WIDTH, y: marginY + row * GRAPH_ROW_HEIGHT });
  });
  const height = marginY * 2 + Math.max(0, rows - 1) * GRAPH_ROW_HEIGHT;
  return { positions, width: containerWidth, height };
}

function layoutRadial(nodes, focusId, containerWidth, containerHeight) {
  const others = nodes.filter((node) => node.id !== focusId);
  const maxRadiusX = Math.max(120, containerWidth / 2 - 90);
  const ringGap = Math.max(60, Math.min(containerWidth, containerHeight) * .14);
  const rings = [];
  let radius = Math.min(containerWidth, containerHeight) * .27;
  const remaining = [...others];
  while (remaining.length) {
    const effRadius = Math.min(radius, maxRadiusX);
    const capacity = Math.max(1, Math.floor((2 * Math.PI * effRadius) / GRAPH_MIN_ARC));
    rings.push({ radius, nodes: remaining.splice(0, capacity) });
    radius += ringGap;
  }
  const maxRadius = rings.length ? rings[rings.length - 1].radius : Math.min(containerWidth, containerHeight) * .27;
  const radiusScaleX = maxRadius > maxRadiusX ? maxRadiusX / maxRadius : 1;
  const height = Math.max(containerHeight, (maxRadius + 90) * 2);
  const cx = containerWidth / 2;
  const cy = height / 2;
  const positions = new Map();
  positions.set(focusId, { x: cx, y: cy });
  rings.forEach((ring) => {
    const n = ring.nodes.length;
    const rx = ring.radius * radiusScaleX;
    const ry = ring.radius;
    ring.nodes.forEach((node, i) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * i) / n;
      positions.set(node.id, { x: cx + Math.cos(angle) * rx, y: cy + Math.sin(angle) * ry });
    });
  });
  return { positions, width: containerWidth, height };
}

// The node with the most connections stands in for an explicit focus when a
// layout needs a root or centre but none is loaded (the overview grid, or a
// layout chosen while nothing is centred) — it is a layout convenience only
// and never changes `state.graph.focus_id` or the left-hand panel.
function graphLayoutRootId(nodes) {
  return nodes.reduce((best, node) => (!best || (node.degree || 0) > (best.degree || 0) ? node : best), null)?.id ?? null;
}

// Layered top-down tree: BFS distance from the root becomes the row, mirroring
// what a graphviz/networkx "dot"-style hierarchical layout would produce for a
// rooted graph. Nodes outside the root's component have no defined distance,
// so they sink into one trailing row rather than being dropped.
function layoutHierarchical(nodes, links, rootId, containerWidth) {
  const adjacency = new Map(nodes.map((node) => [node.id, []]));
  links.forEach((edge) => {
    if (adjacency.has(edge.source) && adjacency.has(edge.target)) {
      adjacency.get(edge.source).push(edge.target);
      adjacency.get(edge.target).push(edge.source);
    }
  });
  const levels = new Map();
  const order = [];
  if (rootId != null && adjacency.has(rootId)) {
    levels.set(rootId, 0);
    order.push(rootId);
    const queue = [rootId];
    while (queue.length) {
      const current = queue.shift();
      const depth = levels.get(current);
      for (const neighbour of adjacency.get(current)) {
        if (levels.has(neighbour)) continue;
        levels.set(neighbour, depth + 1);
        order.push(neighbour);
        queue.push(neighbour);
      }
    }
  }
  const strandedLevel = (levels.size ? Math.max(...levels.values()) : -1) + 1;
  nodes.forEach((node) => {
    if (!levels.has(node.id)) {
      levels.set(node.id, strandedLevel);
      order.push(node.id);
    }
  });
  const rows = new Map();
  order.forEach((id) => {
    const level = levels.get(id);
    if (!rows.has(level)) rows.set(level, []);
    rows.get(level).push(id);
  });
  const marginX = 90;
  const marginY = 55;
  const rowHeight = 100;
  const widest = Math.max(1, ...[...rows.values()].map((row) => row.length));
  const width = Math.max(containerWidth, marginX * 2 + (widest - 1) * GRAPH_COL_WIDTH);
  const positions = new Map();
  [...rows.keys()].sort((a, b) => a - b).forEach((level) => {
    const ids = rows.get(level);
    const rowWidth = (ids.length - 1) * GRAPH_COL_WIDTH;
    const startX = (width - rowWidth) / 2;
    ids.forEach((id, index) => {
      positions.set(id, { x: startX + index * GRAPH_COL_WIDTH, y: marginY + level * rowHeight });
    });
  });
  const levelCount = rows.size || 1;
  const height = marginY * 2 + (levelCount - 1) * rowHeight;
  return { positions, width, height };
}

// A single ring with every node evenly spaced, matching networkx's
// circular_layout — useful for eyeballing overall connection density without
// any one node privileged as a centre.
function layoutCircular(nodes, containerWidth, containerHeight) {
  const n = Math.max(nodes.length, 1);
  const arcRadius = (n * GRAPH_MIN_ARC) / (2 * Math.PI);
  const radius = Math.max(120, Math.min(containerWidth, containerHeight) / 2 - 90, arcRadius);
  const width = Math.max(containerWidth, radius * 2 + 180);
  const height = Math.max(containerHeight, radius * 2 + 180);
  const cx = width / 2;
  const cy = height / 2;
  const positions = new Map();
  nodes.forEach((node, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / n;
    positions.set(node.id, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
  });
  return { positions, width, height };
}

// Deterministic seeded PRNG (mulberry32) so the force layout below is a pure
// function of the node/edge set — re-running it (on zoom, selection, a
// re-render) must reproduce the exact same positions, or the canvas would
// visibly jump on every unrelated interaction.
function mulberry32(seed) {
  let a = seed >>> 0;
  return function rand() {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashSeed(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

// Fruchterman-Reingold force simulation, the same family of algorithm behind
// networkx's spring_layout: nodes repel each other, edges pull their
// endpoints together, and the whole system cools over fixed iterations.
function layoutForce(nodes, links, containerWidth, containerHeight) {
  const positions = new Map();
  if (!nodes.length) return { positions, width: containerWidth, height: containerHeight };
  const width = containerWidth;
  const height = containerHeight;
  const margin = 50;
  const k = Math.sqrt((width * height) / nodes.length) * 0.9;
  const rand = mulberry32(hashSeed(nodes.map((node) => node.id).join("|")));
  nodes.forEach((node) => {
    const angle = rand() * Math.PI * 2;
    const r = Math.min(width, height) * 0.35 * Math.sqrt(rand());
    positions.set(node.id, { x: width / 2 + Math.cos(angle) * r, y: height / 2 + Math.sin(angle) * r });
  });
  const edges = links.filter((edge) => positions.has(edge.source) && positions.has(edge.target));
  const disp = new Map(nodes.map((node) => [node.id, { x: 0, y: 0 }]));
  let temperature = width * 0.06;
  for (let iter = 0; iter < 220; iter += 1) {
    disp.forEach((d) => { d.x = 0; d.y = 0; });
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i].id;
        const b = nodes[j].id;
        const pa = positions.get(a);
        const pb = positions.get(b);
        let dx = pa.x - pb.x;
        let dy = pa.y - pb.y;
        const dist = Math.hypot(dx, dy) || 0.01;
        const force = (k * k) / dist;
        dx = (dx / dist) * force;
        dy = (dy / dist) * force;
        const da = disp.get(a);
        const db = disp.get(b);
        da.x += dx; da.y += dy;
        db.x -= dx; db.y -= dy;
      }
    }
    edges.forEach((edge) => {
      const pa = positions.get(edge.source);
      const pb = positions.get(edge.target);
      let dx = pa.x - pb.x;
      let dy = pa.y - pb.y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const force = (dist * dist) / k;
      dx = (dx / dist) * force;
      dy = (dy / dist) * force;
      const da = disp.get(edge.source);
      const db = disp.get(edge.target);
      da.x -= dx; da.y -= dy;
      db.x += dx; db.y += dy;
    });
    nodes.forEach((node) => {
      const d = disp.get(node.id);
      const dist = Math.hypot(d.x, d.y) || 0.01;
      const limited = Math.min(dist, temperature);
      const p = positions.get(node.id);
      p.x = Math.min(width - margin, Math.max(margin, p.x + (d.x / dist) * limited));
      p.y = Math.min(height - margin, Math.max(margin, p.y + (d.y / dist) * limited));
    });
    temperature *= 0.965;
  }
  return { positions, width, height };
}

function computeGraphLayout(containerWidth, containerHeight) {
  const { nodes, links, focus_id: focusId } = state.graph;
  switch (state.graphLayout) {
    case "grid":
      return layoutGrid(nodes, containerWidth);
    case "radial":
      return layoutRadial(nodes, focusId || graphLayoutRootId(nodes), containerWidth, containerHeight);
    case "hierarchical":
      return layoutHierarchical(nodes, links, focusId || graphLayoutRootId(nodes), containerWidth);
    case "circular":
      return layoutCircular(nodes, containerWidth, containerHeight);
    case "force":
      return layoutForce(nodes, links, containerWidth, containerHeight);
    default:
      return focusId ? layoutRadial(nodes, focusId, containerWidth, containerHeight) : layoutGrid(nodes, containerWidth);
  }
}

function graphEdgeTooltipText(edgeGroup) {
  return `Relationship: ${edgeGroup.dataset.relation || "unknown"} · click for evidence and source documents`;
}

function positionGraphTooltip(clientX, clientY) {
  const tooltip = $("#graphTooltip");
  const canvas = $("#graphCanvas");
  if (!tooltip || !canvas) return;
  const rect = canvas.getBoundingClientRect();
  tooltip.style.left = `${clientX - rect.left + canvas.scrollLeft}px`;
  tooltip.style.top = `${clientY - rect.top + canvas.scrollTop}px`;
}

function showGraphEdgeTooltip(edgeGroup, clientX, clientY) {
  const tooltip = $("#graphTooltip");
  if (!tooltip) return;
  tooltip.textContent = graphEdgeTooltipText(edgeGroup);
  positionGraphTooltip(clientX, clientY);
  tooltip.hidden = false;
}

function hideGraphEdgeTooltip() {
  const tooltip = $("#graphTooltip");
  if (tooltip) tooltip.hidden = true;
}

const GRAPH_ZOOM_MIN = 0.4;
const GRAPH_ZOOM_MAX = 2.5;
const GRAPH_ZOOM_STEP = 1.25;
let graphZoom = 1;
let lastGraphPositions = null;

// Scrolls so the loaded focus (or, in the overview, the layout's implicit
// root) is centred in the viewport. Layouts like hierarchical or circular can
// place that node far from the canvas's (0,0) origin, so without this a fresh
// layout can open on an empty stretch of canvas with the interesting part
// scrolled out of view. Only called right after a layout change or graph
// load — never from drawGraph() itself, which also redraws for zoom and node
// selection, where yanking the user's scroll position back would fight them.
function centerGraphOnRoot() {
  const canvas = $("#graphCanvas");
  if (!canvas || !state.graph || !lastGraphPositions) return;
  const rootId = state.graph.focus_id || graphLayoutRootId(state.graph.nodes);
  const p = rootId != null ? lastGraphPositions.get(rootId) : null;
  if (!p) return;
  canvas.scrollLeft = Math.max(0, p.x * graphZoom - canvas.clientWidth / 2);
  canvas.scrollTop = Math.max(0, p.y * graphZoom - canvas.clientHeight / 2);
}

function updateGraphZoomLabel() {
  const label = $("#graphZoomLabel");
  if (label) label.textContent = `${Math.round(graphZoom * 100)}%`;
}

// `anchor` (a MouseEvent, when given) is the point that should stay under the
// cursor across the zoom change — without it the view re-centres on the
// canvas's current scroll position instead.
function setGraphZoom(nextZoom, anchor) {
  const canvas = $("#graphCanvas");
  const clamped = Math.min(GRAPH_ZOOM_MAX, Math.max(GRAPH_ZOOM_MIN, nextZoom));
  if (!canvas || Math.abs(clamped - graphZoom) < 0.001) return;
  const rect = canvas.getBoundingClientRect();
  const offsetX = anchor ? anchor.clientX - rect.left : canvas.clientWidth / 2;
  const offsetY = anchor ? anchor.clientY - rect.top : canvas.clientHeight / 2;
  const contentX = (canvas.scrollLeft + offsetX) / graphZoom;
  const contentY = (canvas.scrollTop + offsetY) / graphZoom;
  graphZoom = clamped;
  drawGraph();
  canvas.scrollLeft = contentX * graphZoom - offsetX;
  canvas.scrollTop = contentY * graphZoom - offsetY;
}

function drawGraph() {
  const svg = $("#graphSvg");
  const canvas = $("#graphCanvas");
  if (!svg || !canvas || !state.graph) return;
  hideGraphEdgeTooltip();
  // The layout is computed in an unscaled coordinate space sized to the
  // canvas's own width, never the SVG's — the SVG's rendered pixel size
  // grows and shrinks with zoom, so reading it back would feed a moving
  // target into the next layout pass.
  const containerWidth = Math.max(680, canvas.clientWidth || 800);
  const containerHeight = 610;
  const layout = computeGraphLayout(containerWidth, containerHeight);
  const width = layout.width;
  const height = Math.max(containerHeight, layout.height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.style.width = `${width * graphZoom}px`;
  svg.style.height = `${height * graphZoom}px`;
  updateGraphZoomLabel();
  const positions = layout.positions;
  lastGraphPositions = positions;
  const edges = state.graph.links.map((edge) => {
    const a = positions.get(edge.source);
    const b = positions.get(edge.target);
    if (!a || !b) return "";
    return `<g class="graph-edge-group" data-action="open-item" data-kind="edge" data-id="${esc(edge.id)}" data-relation="${esc(edge.relation)}" tabindex="0" role="button" aria-label="Relationship: ${esc(edge.relation)}. Activate for evidence and source documents."><line class="graph-edge-hit" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"></line><line class="graph-edge${edge.status === "accepted" ? " is-accepted" : ""}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"></line></g>`;
  }).join("");
  const nodes = state.graph.nodes.map((node) => {
    const p = positions.get(node.id);
    const focus = node.id === state.graph.focus_id;
    const selected = !focus && node.id === state.graphSelectedId;
    const radius = focus ? 16 : Math.max(7, Math.min(12, 7 + node.degree / 5));
    // Fill carries the entity type, stroke stays with review status, so the two
    // signals never compete. The focus node keeps its accent fill.
    const colourClass = focus ? "" : ` ${typeColourClass(node.type)}`;
    return `<g class="graph-node${focus ? " is-focus" : ""}${selected ? " is-selected" : ""}${node.status === "accepted" ? " is-accepted" : ""}${colourClass}" data-action="select-node" data-id="${esc(node.id)}" transform="translate(${p.x},${p.y})" tabindex="0" role="button"><circle r="${radius}"><title>${esc(node.label)} · ${esc(node.type || "unknown")} · ${node.degree} connections</title></circle><text x="${radius + 6}" y="4">${esc(short(node.label, 24))}</text></g>`;
  }).join("");
  svg.innerHTML = edges + nodes;
  drawTypeLegend();
}

function drawTypeLegend() {
  const legend = $("#graphTypeLegend");
  if (!legend) return;
  const counts = new Map();
  for (const node of state.graph?.nodes || []) {
    const type = String(node.type || "unknown").toLowerCase();
    counts.set(type, (counts.get(type) || 0) + 1);
  }
  const present = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  legend.innerHTML = present
    .map(([type]) => `<span><i class="legend-dot is-type ${typeColourClass(type)}"></i>${esc(type)}</span>`)
    .join("");
}

function currentAskMode() {
  const requested = state.askMode || state.health?.reasoning?.default_mode || "deterministic";
  const reasoning = state.health?.reasoning || {};
  if (requested === "graphrag" && !(reasoning.available && reasoning.endpoint_reachable)) {
    return "deterministic";
  }
  return requested;
}

function renderAsk() {
  const messages = state.messages.map(renderMessage).join("");
  const reasoning = state.health?.reasoning || { available: false };
  const mode = currentAskMode();
  const examples = state.summary?.top_connected || [];
  const first = examples[0]?.label;
  const second = examples.find((item) => item.label !== first)?.label;
  const deterministicSuggestions = [
    "How many entities are in the graph?",
    "What relationship types are most common?",
    "Which nodes are the most connected?",
    ...(first ? [`What is connected to "${first}"?`] : []),
    ...(first && second ? [`Find the shortest path between "${first}" and "${second}"`] : []),
  ];
  const graphragSuggestions = [
    ...(first ? [`Summarise what the graph records about "${first}".`] : []),
    ...(first && second ? [`How are "${first}" and "${second}" related, and on what evidence?`] : []),
    "Which relationships still need review before they can be relied on?",
    "What does the graph not tell us about the highest-degree entities?",
  ];
  const modelReady = reasoning.available && reasoning.endpoint_reachable;
  const usingModel = mode !== "deterministic" && modelReady;
  const suggestions = usingModel ? graphragSuggestions : deterministicSuggestions;
  const modelLine = !reasoning.available
    ? "Install the optional LangChain toolchain to enable the local model."
    : reasoning.endpoint_reachable
      ? `${esc(reasoning.model_id)} via ${esc(reasoning.base_url)}`
      : `No model answered at ${esc(reasoning.base_url)}. Start LM Studio and reload.`;
  const modelPill = modelReady
    ? `<span class="status-pill status-accepted">Model ready</span>`
    : reasoning.available
      ? `<span class="status-pill status-rejected">Endpoint offline</span>`
      : `<span class="status-pill status-unreviewed">Optional setup</span>`;
  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Graph questions", "Ask with a visible evidence trail", "Answers use the loaded graph and return the nodes and relationships consulted. Connectivity is never presented as criticality.")}
    <div class="ask-layout">
      <section class="panel chat-panel"><div class="messages" id="messages" aria-live="polite" aria-busy="${state.askBusy}">${messages}</div><form class="ask-form" id="askForm"><label class="sr-only" for="askInput">Question for the graph</label><input id="askInput" autocomplete="off" placeholder="${usingModel ? "Ask anything about the retrieved subgraph" : "Ask about an entity, relationship or path"}" ${state.askBusy ? "disabled" : ""}><button class="button button-primary" ${state.askBusy ? "disabled" : ""}>${state.askBusy ? "Checking…" : "Ask graph"}</button></form></section>
      <aside class="panel">
        <div class="panel-head"><div><h2>Answering mode</h2><p>${modelLine}</p></div>${modelPill}</div>
        <div class="filter-bar">
          <label class="sr-only" for="askModeSelect">Answering mode</label>
          <select id="askModeSelect" aria-label="Answering mode">
            <option value="deterministic" ${mode === "deterministic" ? "selected" : ""}>Deterministic graph queries</option>
            <option value="graphrag" ${mode === "graphrag" ? "selected" : ""} ${modelReady ? "" : "disabled"}>GraphRAG with the local model</option>
            <option value="auto" ${mode === "auto" ? "selected" : ""}>Auto — local model, deterministic fallback</option>
          </select>
        </div>
        <div class="panel-head"><div><h2>Example questions</h2><p>${usingModel ? "The model only sees the retrieved subgraph." : "Start with a supported query pattern."}</p></div></div>
        <div class="suggestions">${suggestions.map((q) => `<button class="suggestion" data-action="ask-suggestion" data-question="${esc(q)}">${esc(q)}</button>`).join("")}</div>
      </aside>
    </div>
  </div>`;
  $("#askForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#askInput");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    await askQuestion(question);
  });
  $("#askModeSelect").addEventListener("change", (event) => {
    state.askMode = event.target.value;
    renderAsk();
  });
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function renderMessage(message) {
  if (message.role === "user") return `<div class="message user"><p>${esc(message.answer)}</p></div>`;
  const records = [...(message.nodes || []), ...(message.edges || [])].slice(0, 10);
  const citations = records.map((item) => {
    const kind = item.kind || (item.relation && item.source ? "edge" : "node");
    return `<button class="citation-chip" data-action="open-item" data-kind="${kind}" data-id="${esc(item.id)}">${esc(short(item.label || item.relation || item.id, 40))}</button>`;
  }).join("");
  const retrieval = message.retrieval
    ? `<p class="notice">Retrieved ${formatNumber(message.retrieval.entities)} entities and ${formatNumber(message.retrieval.relationships)} relationships at depth ${message.retrieval.depth}, excluding rejected records${message.retrieval.truncated ? ", truncated to the context limit" : ""}.</p>`
    : "";
  const badge = message.mode === "graphrag"
    ? `<span class="status-pill status-unreviewed">Local model · ${esc(message.model?.model_id || "GraphRAG")}</span>`
    : "";
  return `<div class="message assistant">${badge}<p>${esc(message.answer)}</p>${citations ? `<div class="citation-row">${citations}</div>` : ""}<details><summary>How this answer was produced</summary><p>${esc(message.trace || "No trace supplied.")}</p>${retrieval}</details>${message.notice ? `<p class="notice">${esc(message.notice)}</p>` : ""}</div>`;
}

async function askQuestion(question) {
  if (state.askBusy) return;
  state.messages.push({ role: "user", answer: question });
  state.askBusy = true;
  renderAsk();
  try {
    const response = await api("/api/query", { method: "POST", body: JSON.stringify({ question, mode: currentAskMode() }) });
    state.messages.push({ role: "assistant", ...response });
  } catch (error) {
    state.messages.push({ role: "assistant", answer: `I could not complete that question: ${error.message}`, trace: "The API request failed.", nodes: [], edges: [] });
  }
  state.askBusy = false;
  renderAsk();
  $("#askInput")?.focus();
}

async function loadJobs() {
  const [jobs, documents] = await Promise.all([api("/api/jobs"), api("/api/documents")]);
  state.jobs = jobs.items;
  state.documents = documents.items;
}

async function loadCorpus() {
  state.corpus = { ...state.corpus, ...(await api("/api/corpus")) };
}

async function loadExampleSets() {
  state.exampleSets = (await api("/api/examples")).items;
}

async function refreshProjects() {
  state.projects = await api("/api/projects");
}

function resetAppStateForProjectSwitch() {
  invalidateDerivedViews();
  state.summary = null;
  state.jobs = [];
  state.documents = [];
  state.queue = { items: [], total: 0, offset: 0, limit: 20, has_more: false };
  state.reviewIndex = 0;
  state.reviewOffset = 0;
  state.reviewFilters = { kind: "all", status: "unreviewed", query: "", document: "" };
  state.graph = null;
  state.graphFocus = null;
  state.graphFocusItem = null;
  state.graphSelectedId = null;
  state.graphLayout = "auto";
  state.schema.catalogue = null;
  state.schema.preview = null;
  state.schema.saved = null;
  state.methods.catalogue = null;
  state.methods.profiles = null;
  state.priorities.bootstrap = null;
  state.priorities.selected = null;
  state.corpus = { available: false, directory: "", items: [], selected: new Set() };
  state.growthBatch = [];
  state.exampleSets = [];
  state.selectedExampleSetId = "";
}

async function switchProject(id) {
  await api(`/api/projects/${encodeURIComponent(id)}/activate`, { method: "POST" });
  resetAppStateForProjectSwitch();
  await Promise.all([refreshSummary(), refreshProjects()]);
  toast("Project switched", `Now working in ${state.summary.graph_name}.`);
  await setView("overview", { replace: true });
}

async function createProject(name) {
  const created = await api("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
  await switchProject(created.id);
}

function renderProjectDialog() {
  const rows = state.projects.items
    .map(
      (project) => `<button type="button" class="list-row" data-action="activate-project" data-id="${esc(project.id)}" ${project.active ? "disabled" : ""}>
        <span><strong>${esc(project.name)}</strong><span>${formatNumber(project.nodes)} entities · ${formatNumber(project.relationships)} relationships</span></span>
        ${project.active ? `<span class="status-pill status-accepted">Active</span>` : ""}
      </button>`
    )
    .join("");
  $("#projectList").innerHTML = rows || "<p>No projects yet.</p>";
}

let growthWatchTimer = null;

function watchGraphGrowth() {
  if (growthWatchTimer || !state.growthBatch.length) return;
  const tick = async () => {
    const jobById = new Map(state.jobs.map((job) => [job.id, job]));
    const pending = state.growthBatch.some((id) => {
      const job = jobById.get(id);
      return !job || ["queued", "running"].includes(job.status);
    });
    if (state.view === "explore") {
      await loadGraph(state.graphFocus || "");
    }
    if (!pending) {
      growthWatchTimer = null;
      state.growthBatch = [];
      return;
    }
    growthWatchTimer = window.setTimeout(tick, 2200);
  };
  growthWatchTimer = window.setTimeout(tick, 2200);
}

function renderDocuments() {
  if (!state.documents.length) {
    return `<p>No record in this graph names a source document. Extract a document, or import a graph whose records carry <code>source_document</code>.</p>`;
  }
  return state.documents
    .map((row) => {
      const facts = [
        row.media_type ? esc(row.media_type.toUpperCase()) : "",
        row.page_count ? `${formatNumber(row.page_count)} pages` : "",
        `${formatNumber(row.nodes)} entities`,
        `${formatNumber(row.relationships)} relationships`,
        row.ingested_at ? esc(new Date(row.ingested_at).toLocaleString("en-GB")) : "",
      ].filter(Boolean);
      return `<button class="list-row" data-action="review-document" data-value="${esc(row.name)}">
        <span><strong>${esc(row.name)}</strong><span>${facts.join(" · ")}</span></span>
        <b class="count-pill">${formatNumber(row.unreviewed)}</b>
      </button>`;
    })
    .join("");
}

function renderJob(job) {
  if (job.job_type === "example_generation") {
    const result = job.result || {};
    const counts = result.counts || {};
    const summary = job.status === "completed"
      ? `${counts.entities || 0} entities, ${counts.relationships || 0} relationships drafted from ${counts.pages_sampled || 0} page(s) · profile: ${esc(result.profile || "")}`
      : esc(job.message);
    return `<div class="job-row"><strong>Example set from ${esc(job.filename)} · ${esc(job.status)}</strong><span>${summary}</span></div>`;
  }
  const report = job.result?.extraction_report;
  const passes = report
    ? report.passes
        .map((pass) => {
          if (pass.skipped) return `<span>${esc(pass.pass)}: skipped — ${esc(pass.skipped)}</span>`;
          const reasons = Object.entries(pass.rejection_reasons || {})
            .map(([reason, count]) => `${reason} (${count})`)
            .join("; ");
          return `<span>${esc(pass.pass)}: ${pass.accepted} accepted, ${pass.rejected} rejected of ${pass.raw_extractions}${reasons ? ` — ${esc(reasons)}` : ""}</span>`;
        })
        .join("")
    : "";
  // Heuristic mode has no fixed pass structure to report - instead it says
  // which of "packet" / "corpus" / "general" the document's own content
  // picked, and how many raw relationship extractions the predicate/quantity
  // filter then dropped. Without this, a mismatched profile (e.g. a real
  // report silently routed through a synthetic-corpus prompt) produces a
  // lopsided entity/relationship count with no visible explanation.
  const profileInfo = job.result?.extraction_profile;
  const profileSummary = profileInfo?.name
    ? `<span>heuristic profile: ${esc(profileInfo.name)}${profileInfo.auto_detected ? " (auto-detected)" : ""} — ${formatNumber(profileInfo.entities || 0)} entities, ${formatNumber(profileInfo.relationships_kept || 0)} of ${formatNumber(profileInfo.relationships_extracted || 0)} extracted relationships kept${profileInfo.relationships_dropped ? ` (${formatNumber(profileInfo.relationships_dropped)} dropped by the predicate/quantity filter)` : ""}</span>`
    : profileInfo?.note
      ? `<span>heuristic profile: ${esc(profileInfo.note)}</span>`
      : "";
  // What this specific job actually ran with, frozen at submission - shown
  // here so a run stays auditable after the "Extract from documents" box
  // that set it has long since moved on to different values.
  const settings = job.extraction_settings;
  const settingsSummary = settings
    ? `<span>settings: ${settings.extraction_passes != null ? `${formatNumber(settings.extraction_passes)} pass(es)` : "default passes"}, ${settings.max_char_buffer != null ? `${formatNumber(settings.max_char_buffer)} char chunks` : "default chunk length"}</span>`
    : "";
  return `<div class="job-row"><strong>${esc(job.filename)} · ${esc(job.status)}${job.mode ? ` · ${esc(job.mode)}` : ""}</strong><span>${esc(job.message)}</span>${passes}${profileSummary}${settingsSummary}</div>`;
}

function renderCorpusList() {
  if (!state.corpus.items.length) return `<p>No documents were found in ${esc(state.corpus.directory)}.</p>`;
  return state.corpus.items
    .map(
      (item) => `<label class="list-row corpus-row">
        <span><strong>${esc(item.filename)}</strong><span>${formatNumber(item.size)} bytes</span></span>
        <input type="checkbox" class="corpus-checkbox" data-filename="${esc(item.filename)}" ${state.corpus.selected.has(item.filename) ? "checked" : ""}>
      </label>`
    )
    .join("");
}

function renderExampleSets() {
  if (!state.exampleSets.length) return "";
  const rows = state.exampleSets
    .map(
      (set) => `<div class="system-row">
        <span>${esc((set.source_documents || []).join(", "))} · ${esc(set.profile || "")}</span>
        <strong>${formatNumber(set.counts?.entities || 0)}e / ${formatNumber(set.counts?.relationships || 0)}r
          <a class="text-action" href="${apiPath(`/api/examples/${encodeURIComponent(set.id)}`)}" target="_blank" rel="noopener">Inspect</a>
        </strong>
      </div>`
    )
    .join("");
  return `<details class="detail-section"><summary>Generated example sets (${state.exampleSets.length})</summary><div class="system-list">${rows}</div></details>`;
}

function renderSources() {
  const extraction = state.health?.extraction || { available: false, modules: {} };
  // Older backend payloads did not expose `ready`; preserve their established
  // local behaviour while new payloads require both dependencies and endpoint.
  const extractionReady = extraction.ready === undefined
    ? Boolean(extraction.available)
    : Boolean(extraction.ready);
  const schema = state.health?.schema || { available: false };
  const modules = Object.entries(extraction.modules || {}).map(([name, available]) => `<div class="system-row"><span>${esc(name)}</span><strong>${available ? "Ready" : "Not installed"}</strong></div>`).join("");
  const jobs = state.jobs.length ? state.jobs.map(renderJob).join("") : `<p>No extraction jobs have been started in this session.</p>`;
  const mode = state.extractionMode || extraction.default_mode || "heuristic";
  const counts = schema.selection?.counts;
  const schemaLine = schema.available
    ? `${formatNumber(counts?.entity_types || 0)} entity types and ${formatNumber(counts?.relationship_types || 0)} predicates are in scope. <button class="text-action" data-action="goto-schema">Change the selection</button>`
    : "The targeting schema did not load, so only the heuristic profile is available.";
  const modeChooser = `<div class="filter-bar"><label class="sr-only" for="extractionMode">Extraction mode</label><select id="extractionMode">
      <option value="schema" ${mode === "schema" ? "selected" : ""} ${schema.available ? "" : "disabled"}>Schema-guided (targeting profile)</option>
      <option value="heuristic" ${mode === "heuristic" ? "selected" : ""}>Heuristic (per-corpus prompts)</option>
    </select></div>`;
  // LangExtract's own settings, editable before a run rather than left buried
  // in the extractor's constructor defaults. Applies to both the direct
  // upload below and "Load from corpus".
  const extractionSettingsBox = `<div class="filter-bar extraction-settings">
      <label for="extractionPasses">Extraction passes</label>
      <input id="extractionPasses" type="number" min="1" max="5" step="1" value="${state.extractionSettings.extraction_passes}">
      <label for="extractionChunkLength">Chunk length (characters)</label>
      <input id="extractionChunkLength" type="number" min="300" max="8000" step="50" value="${state.extractionSettings.max_char_buffer}">
    </div>
    <p class="rule-notice">Extraction passes: how many times LangExtract re-reads each chunk for extra recall (its own defaults are 2 for the heuristic profile, 1 for schema-guided) - more passes cost more model calls. Chunk length: characters sent to the model per call - shorter chunks catch more detail but need more calls to cover a document.</p>`;
  const exampleSetOptions = [`<option value="">Built-in examples</option>`]
    .concat(
      state.exampleSets.map(
        (set) =>
          `<option value="${esc(set.id)}" ${state.selectedExampleSetId === set.id ? "selected" : ""}>${esc((set.source_documents || []).join(", ")) || esc(set.id)} · ${esc(set.profile || "")}</option>`
      )
    )
    .join("");
  const exampleSetChooser = mode === "heuristic"
    ? `<div class="filter-bar"><label class="sr-only" for="exampleSetSelect">LangExtract example set</label><select id="exampleSetSelect">${exampleSetOptions}</select></div>`
    : "";
  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Sources and data", "Manage the graph input", "The current graph works immediately. Optional PDF extraction uses the LangExtract pipeline and a configured local model endpoint.", `<a class="button button-primary" href="${apiPath("/api/export/graph?status=accepted")}">Export accepted graph</a><a class="button button-secondary" href="${apiPath("/api/export/graph")}">Export all candidates</a><a class="button button-secondary" href="${apiPath("/api/export/reviews")}">Export review log</a>`)}
    <div class="source-grid">
      <section class="panel source-card"><div class="panel-head"><div><h2>Current graph</h2><p>${esc(state.summary.graph_name)}</p></div><span class="status-pill status-accepted">Loaded</span></div><div class="system-list"><div class="system-row"><span>Entities</span><strong>${formatNumber(state.summary.nodes)}</strong></div><div class="system-row"><span>Relationships</span><strong>${formatNumber(state.summary.relationships)}</strong></div><div class="system-row"><span>Last updated</span><strong>${esc(new Date(state.summary.updated_at).toLocaleString("en-GB"))}</strong></div></div></section>
      <section class="panel source-card"><div class="panel-head"><div><h2>Import node-link JSON</h2><p>Merge another graph or replace the current dataset. A local backup is created first.</p></div></div><div class="upload-zone"><div><label for="graphImportFile"><strong>Select a graph JSON file</strong><br><small>Expected keys: nodes[] and links[]</small></label><br><input id="graphImportFile" type="file" accept="application/json,.json"></div></div><div class="filter-bar"><label class="sr-only" for="graphImportMode">Import mode</label><select id="graphImportMode"><option value="merge">Merge with current graph</option><option value="replace">Replace current graph</option></select><button class="button button-primary" id="graphImportBtn">Import graph</button></div></section>
      <section class="panel source-card"><div class="panel-head"><div><h2>Extract from documents</h2><p>Optional background extraction using the document-to-graph module. Jobs run one at a time to protect local model capacity.</p></div>${extractionReady ? `<span class="status-pill status-accepted">Ready</span>` : extraction.available ? `<span class="status-pill status-rejected">Model offline</span>` : `<span class="status-pill status-unreviewed">Optional setup</span>`}</div>${modeChooser}<p class="rule-notice">${mode === "schema" ? schemaLine : "Prompts and predicate vocabulary are chosen from the document's own content."}</p>${extractionSettingsBox}<div class="upload-zone"><div><label for="pdfUpload"><strong>${extractionReady ? "Select one or more PDF or text documents to extract" : extraction.available ? "Connect the configured extraction model endpoint" : "Install the full requirements to enable extraction"}</strong><br><small>${extraction.available ? `${esc(extraction.model_id)} via ${esc(extraction.base_url)} · endpoint ${extraction.endpoint_reachable ? "reachable" : "offline"}` : "The review, explore and deterministic query workflows remain available."}</small></label><br><input id="pdfUpload" type="file" multiple accept=".pdf,.txt,application/pdf,text/plain" ${extractionReady ? "" : "disabled"}></div></div>${modules ? `<details><summary>Extraction dependency status</summary><div class="system-list">${modules}</div></details>` : ""}</section>
      <section class="panel source-card">
        <div class="panel-head"><div><h2>Load from corpus</h2><p>Select part of the shared document corpus to ingest into the current project.</p></div><b class="count-pill">${state.corpus.selected.size}</b></div>
        ${state.corpus.available ? `<div class="filter-bar">
          <button type="button" class="text-action" data-action="corpus-select-all">Select all</button>
          <button type="button" class="text-action" data-action="corpus-select-none">Clear</button>
          <label class="sr-only" for="corpusMode">Extraction mode</label>
          <select id="corpusMode">
            <option value="schema" ${mode === "schema" ? "selected" : ""} ${schema.available ? "" : "disabled"}>Schema-guided (targeting profile)</option>
            <option value="heuristic" ${mode === "heuristic" ? "selected" : ""}>Heuristic (per-corpus prompts)</option>
          </select>
        </div>
        ${exampleSetChooser}
        <div class="clean-list corpus-list">${renderCorpusList()}</div>
        <div class="filter-bar">
          <button type="button" class="button button-primary" id="corpusLoadBtn" ${state.corpus.selected.size ? "" : "disabled"}>Load selected (${state.corpus.selected.size})</button>
          <button type="button" class="button button-secondary" id="generateExamplesBtn" title="Draft LangExtract few-shot examples from the selected document(s), tailored to this corpus" ${state.corpus.selected.size >= 1 && state.corpus.selected.size <= 3 ? "" : "disabled"}>Generate examples (${state.corpus.selected.size})</button>
        </div>
        <p class="rule-notice">Pick 1-3 documents above, then "Generate examples" to draft LangExtract few-shot examples grounded in a few of their pages, instead of the two built-in synthetic example sets.</p>
        ${renderExampleSets()}` : `<p>No corpus directory is configured on the backend (see <code>--corpus</code>).</p>`}
      </section>
      <section class="panel source-card"><div class="panel-head"><div><h2>Source documents</h2><p>Every document the current graph attributes a record to. The count is the candidates still awaiting review.</p></div><b class="count-pill">${formatNumber(state.documents.length)}</b></div><div class="clean-list">${renderDocuments()}</div></section>
      <section class="panel source-card"><div class="panel-head"><div><h2>Extraction activity</h2><p>Jobs are processed one at a time to protect local model capacity.</p></div><button class="text-action" data-action="refresh-jobs">Refresh</button></div><div id="jobList">${jobs}</div></section>
    </div>
  </div>`;
  bindSourceInputs();
}

function bindSourceInputs() {
  $("#graphImportBtn")?.addEventListener("click", async () => {
    const file = $("#graphImportFile").files[0];
    if (!file) return toast("Choose a JSON file", "No graph file is selected.", true);
    const mode = $("#graphImportMode").value;
    if (mode === "replace" && !window.confirm("Replace the current graph? A backup will be created and existing review decisions will be cleared.")) return;
    try {
      const graph = JSON.parse(await file.text());
      await api(`/api/graph/import?mode=${mode}`, { method: "POST", body: JSON.stringify({ graph }) });
      invalidateDerivedViews();
      await Promise.all([refreshSummary(), loadJobs()]);
      toast("Graph imported", `${file.name} was ${mode === "merge" ? "merged" : "loaded"}.`);
      renderSources();
    } catch (error) {
      toast("Import failed", error.message, true);
    }
  });
  $("#extractionMode")?.addEventListener("change", (event) => {
    state.extractionMode = event.target.value;
    renderSources();
  });
  $("#exampleSetSelect")?.addEventListener("change", (event) => {
    state.selectedExampleSetId = event.target.value;
  });
  $("#extractionPasses")?.addEventListener("change", (event) => {
    const value = Math.round(Number(event.target.value));
    if (Number.isFinite(value)) state.extractionSettings.extraction_passes = Math.min(5, Math.max(1, value));
    event.target.value = state.extractionSettings.extraction_passes;
  });
  $("#extractionChunkLength")?.addEventListener("change", (event) => {
    const value = Math.round(Number(event.target.value));
    if (Number.isFinite(value)) state.extractionSettings.max_char_buffer = Math.min(8000, Math.max(300, value));
    event.target.value = state.extractionSettings.max_char_buffer;
  });
  $("#pdfUpload")?.addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    if (!files.length) return;
    const mode = state.extractionMode || state.health?.extraction?.default_mode || "heuristic";
    const exampleParam = mode === "heuristic" && state.selectedExampleSetId
      ? `&example_set_id=${encodeURIComponent(state.selectedExampleSetId)}`
      : "";
    const settingsParams = `&extraction_passes=${encodeURIComponent(state.extractionSettings.extraction_passes)}`
      + `&max_char_buffer=${encodeURIComponent(state.extractionSettings.max_char_buffer)}`;
    const outcomes = await Promise.allSettled(
      files.map((file) =>
        api(`/api/extractions?mode=${encodeURIComponent(mode)}${exampleParam}${settingsParams}`, {
          method: "POST",
          body: file,
          headers: {
            "Content-Type": file.type || "application/octet-stream",
            "X-Filename": file.name.replace(/[^A-Za-z0-9._-]/g, "_"),
          },
        }).then((job) => ({ file, job }))
      )
    );
    const submitted = outcomes.filter((o) => o.status === "fulfilled").map((o) => o.value.job);
    const failed = outcomes.filter((o) => o.status === "rejected");
    state.jobs.unshift(...submitted);
    state.growthBatch = [...new Set([...state.growthBatch, ...submitted.map((job) => job.id)])];
    if (submitted.length) {
      toast(
        files.length > 1 ? "Extraction batch queued" : "Extraction queued",
        files.length > 1
          ? `${submitted.length} of ${files.length} documents are waiting for the local model.${failed.length ? ` ${failed.length} could not be queued.` : ""}`
          : `${files[0].name} is waiting for the local model.`,
        failed.length > 0 && submitted.length === 0
      );
    } else {
      toast("Extraction could not start", failed[0]?.reason?.message || "No documents were queued.", true);
    }
    renderSources();
    submitted.forEach((job) => pollJob(job.id));
    watchGraphGrowth();
  });
  $$(".corpus-checkbox").forEach((box) => {
    box.addEventListener("change", () => {
      const filename = box.dataset.filename;
      if (box.checked) state.corpus.selected.add(filename);
      else state.corpus.selected.delete(filename);
      renderSources();
    });
  });
  $("#corpusLoadBtn")?.addEventListener("click", async () => {
    const files = [...state.corpus.selected];
    if (!files.length) return;
    const mode = $("#corpusMode")?.value || state.extractionMode || "heuristic";
    const example_set_id = mode === "heuristic" ? state.selectedExampleSetId || "" : "";
    try {
      const result = await api("/api/corpus/ingest", {
        method: "POST",
        body: JSON.stringify({
          files,
          mode,
          example_set_id,
          extraction_passes: state.extractionSettings.extraction_passes,
          max_char_buffer: state.extractionSettings.max_char_buffer,
        }),
      });
      state.jobs.unshift(...result.submitted);
      state.growthBatch = [...new Set([...state.growthBatch, ...result.submitted.map((job) => job.id)])];
      state.corpus.selected.clear();
      toast(
        "Corpus batch queued",
        `${result.total_submitted} of ${files.length} documents queued.${result.errors.length ? ` ${result.errors.length} could not be queued.` : ""}`,
        result.errors.length > 0 && result.total_submitted === 0
      );
      renderSources();
      result.submitted.forEach((job) => pollJob(job.id));
      watchGraphGrowth();
    } catch (error) {
      toast("Corpus batch could not start", error.message, true);
    }
  });
  $("#generateExamplesBtn")?.addEventListener("click", async () => {
    const files = [...state.corpus.selected];
    if (files.length < 1 || files.length > 3) return;
    try {
      const job = await api("/api/examples/generate", {
        method: "POST",
        body: JSON.stringify({ files, pages_per_doc: 3, profile: "auto" }),
      });
      state.jobs.unshift(job);
      toast("Example generation queued", `Drafting examples from ${files.length} document(s).`);
      renderSources();
      pollJob(job.id);
    } catch (error) {
      toast("Example generation could not start", error.message, true);
    }
  });
}

async function pollJob(id) {
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    const index = state.jobs.findIndex((row) => row.id === id);
    if (index >= 0) state.jobs[index] = job;
    if (state.view === "sources") renderSources();
    if (["queued", "running"].includes(job.status)) window.setTimeout(() => pollJob(id), 1800);
    else if (job.job_type === "example_generation") {
      if (job.status === "completed") {
        await loadExampleSets();
        if (state.view === "sources") renderSources();
        toast("Example generation completed", job.message);
      } else toast("Example generation failed", job.message, true);
    } else {
      if (job.status === "completed") {
        invalidateDerivedViews();
        await Promise.all([refreshSummary(), loadJobs()]);
        if (state.view === "sources") renderSources();
        toast("Extraction completed", "New candidates are available in the review queue.");
      } else toast("Extraction failed", job.message, true);
    }
  } catch (error) {
    toast("Job status unavailable", error.message, true);
  }
}

/* ------------------------------------------------------------------ schema */

// An empty saved entity list means "every extractable type in the selected
// modules". The picker is explicit, so that shorthand is expanded on load and
// collapsed again on save; otherwise ticking a new module later would silently
// fail to bring its types with it.
function availableEntityTypes(moduleIds = null) {
  const catalogue = state.schema.catalogue;
  if (!catalogue) return [];
  const modules = moduleIds || state.schema.draft.modules;
  return catalogue.entity_types.filter(
    (row) => row.extractable && row.module_ids.some((id) => modules.has(id)),
  );
}

async function loadSchema(force = false) {
  if (!state.schema.catalogue || force) {
    state.schema.catalogue = await api("/api/schema");
  }
  const catalogue = state.schema.catalogue;
  if (!state.schema.saved || force) {
    const saved = catalogue.saved_selection || {};
    const modules = new Set(
      saved.module_ids?.length ? saved.module_ids : catalogue.default_selection.module_ids,
    );
    modules.add(catalogue.core_module_id);
    state.schema.draft.modules = modules;
    const savedEntities = saved.entity_type_ids || [];
    state.schema.draft.entities = new Set(
      savedEntities.length ? savedEntities : availableEntityTypes(modules).map((row) => row.id),
    );
    state.schema.saved = saved;
    state.schema.dirty = false;
    state.schema.preview = await api("/api/schema/selection");
  }
}

function draftRequest() {
  const modules = [...state.schema.draft.modules];
  const available = availableEntityTypes();
  const chosen = available.filter((row) => state.schema.draft.entities.has(row.id));
  return {
    module_ids: modules,
    // Everything ticked collapses back to the shorthand so the saved selection
    // keeps tracking its modules rather than freezing today's type list.
    entity_type_ids: chosen.length === available.length ? [] : chosen.map((row) => row.id),
  };
}

async function previewSchema() {
  state.schema.busy = true;
  updateSchemaSummary();
  try {
    state.schema.preview = await api("/api/schema/selection/preview", {
      method: "POST",
      body: JSON.stringify(draftRequest()),
    });
  } catch (error) {
    toast("Preview failed", error.message, true);
  } finally {
    state.schema.busy = false;
    renderSchema();
  }
}

let schemaPreviewTimer = null;
function schedulePreview() {
  state.schema.dirty = true;
  clearTimeout(schemaPreviewTimer);
  updateSchemaSummary();
  schemaPreviewTimer = window.setTimeout(previewSchema, 250);
}

async function suggestSchemaTopic() {
  const topic = state.schema.suggestionTopic.trim();
  if (!topic || state.schema.suggestBusy) return;
  state.schema.suggestBusy = true;
  renderSchema();
  try {
    const suggestion = await api("/api/schema/suggest", {
      method: "POST",
      body: JSON.stringify({ topic, max_entity_types: 24 }),
    });
    state.schema.suggestion = suggestion;
    const entityIds = suggestion.suggestion?.entity_type_ids || [];
    if (entityIds.length) {
      const modules = new Set(suggestion.suggestion?.module_ids || []);
      modules.add(state.schema.catalogue.core_module_id);
      state.schema.draft.modules = modules;
      state.schema.draft.entities = new Set(entityIds);
      state.schema.preview = suggestion.preview;
      state.schema.dirty = true;
      toast("Topic suggestion applied to the draft", "Review the matches and save explicitly if the slice is correct.");
    } else {
      toast("No ontology terms matched", suggestion.warning || "Refine the topic or use the manual picker.", true);
    }
  } catch (error) {
    toast("Topic narrowing failed", error.message, true);
  } finally {
    state.schema.suggestBusy = false;
    renderSchema();
  }
}

async function suggestSchemaMission() {
  const mission = state.schema.missionText.trim();
  if (!mission || state.schema.missionBusy) return;
  state.schema.missionBusy = true;
  renderSchema();
  try {
    const suggestion = await api("/api/schema/suggest-llm", {
      method: "POST",
      body: JSON.stringify({ mission, max_entity_types: 40 }),
    });
    state.schema.missionSuggestion = suggestion;
    const entityIds = suggestion.suggestion?.entity_type_ids || [];
    if (entityIds.length) {
      const modules = new Set(suggestion.suggestion?.module_ids || []);
      modules.add(state.schema.catalogue.core_module_id);
      state.schema.draft.modules = modules;
      state.schema.draft.entities = new Set(entityIds);
      state.schema.preview = suggestion.preview;
      state.schema.dirty = true;
      toast("Mission suggestion applied to the draft", "Review the rationale and matches, then save explicitly if the slice is correct.");
    } else {
      toast("No ontology terms selected", suggestion.warning || "Refine the mission statement or use the manual picker.", true);
    }
  } catch (error) {
    toast("Mission narrowing failed", error.message, true);
  } finally {
    state.schema.missionBusy = false;
    renderSchema();
  }
}

function promptBadge(prompt) {
  if (!prompt) return "";
  const heavy = prompt.notes.some((note) => note.startsWith("The compiled"));
  const trimmed = prompt.detail === "compact";
  const tone = heavy ? "status-rejected" : trimmed ? "status-unreviewed" : "status-accepted";
  return `<div class="system-row"><span>${esc(prompt.pass)} prompt</span><strong>${formatNumber(prompt.characters)} characters · ~${formatNumber(prompt.estimated_tokens)} tokens <span class="status-pill ${tone}">${heavy ? "over budget" : trimmed ? "trimmed" : "full detail"}</span></strong></div>`;
}

function updateSchemaSummary() {
  const counts = state.schema.preview?.counts;
  const badge = $("#schemaNavCount");
  if (badge) badge.textContent = counts ? formatNumber(counts.entity_types) : "—";
  const banner = $("#schemaDirty");
  if (banner) banner.hidden = !state.schema.dirty;
  const save = $("#schemaSave");
  if (save) save.disabled = !state.schema.dirty || state.schema.busy;
}

function renderSchema() {
  const catalogue = state.schema.catalogue;
  const preview = state.schema.preview;
  if (!catalogue) {
    $("#mainContent").innerHTML = `<div class="page"><div class="empty-state panel"><div><strong>The extraction schema is not loaded</strong><p>Start the server with <code>--schema</code> pointing at the targeting profile JSON.</p></div></div></div>`;
    return;
  }
  const counts = preview?.counts || { modules: 0, entity_types: 0, relationship_types: 0, value_sets: 0 };
  const available = availableEntityTypes();
  const chosen = state.schema.draft.entities;

  const moduleRows = catalogue.modules
    .map((module) => {
      const ticked = state.schema.draft.modules.has(module.id);
      const locked = module.mandatory;
      return `<label class="pick-row${locked ? " is-locked" : ""}">
        <input type="checkbox" data-schema-module="${esc(module.id)}" ${ticked ? "checked" : ""} ${locked ? "disabled" : ""}>
        <span><strong>${esc(module.code)} · ${esc(module.label)}</strong><span>${esc(module.question)}</span></span>
        <b class="count-pill">${module.entity_type_ids.length}</b>
      </label>`;
    })
    .join("");

  const query = state.schema.filter.query.trim().toLowerCase();
  const moduleFilter = state.schema.filter.module;
  const visible = available.filter((row) => {
    if (moduleFilter !== "all" && !row.module_ids.includes(moduleFilter)) return false;
    if (!query) return true;
    return `${row.label} ${row.id} ${row.definition}`.toLowerCase().includes(query);
  });
  const entityRows = visible
    .map(
      (row) => `<label class="pick-row" title="${esc(row.definition)}">
        <input type="checkbox" data-schema-entity="${esc(row.id)}" ${chosen.has(row.id) ? "checked" : ""}>
        <span><strong>${esc(row.label)}</strong><span>${esc(row.id)}</span></span>
        ${row.sme_review_required ? `<span class="status-pill status-unreviewed">SME</span>` : ""}
      </label>`,
    )
    .join("");

  const moduleOptions = [`<option value="all">All selected modules</option>`]
    .concat(
      catalogue.modules
        .filter((module) => state.schema.draft.modules.has(module.id))
        .map((module) => `<option value="${esc(module.id)}" ${moduleFilter === module.id ? "selected" : ""}>${esc(module.code)} · ${esc(module.label)}</option>`),
    )
    .join("");

  const byId = new Map(catalogue.relationship_types.map((row) => [row.id, row]));
  const predicateRows = (preview?.relationship_type_ids || [])
    .map((id) => byId.get(id))
    .filter(Boolean)
    .map(
      (row) => `<div class="system-row"><span><strong>${esc(row.label)}</strong><br><small>${esc(row.domain_type_ids.join(", ") || "any")} → ${esc(row.range_value_set_id || row.range_type_ids.join(", ") || "any")}</small></span><strong>${esc(row.model_action.replaceAll("_", " ").toLowerCase())}</strong></div>`,
    )
    .join("");

  const droppedRows = (preview?.dropped_relationships || [])
    .map((row) => `<div class="system-row"><span>${esc(row.label)}</span><strong>${esc(row.reason)}</strong></div>`)
    .join("");

  const warnings = (preview?.warnings || [])
    .map((note) => `<p class="rule-notice">${esc(note)}</p>`)
    .join("");
  const promptNotes = ["ENTITY", "RELATIONSHIP"]
    .flatMap((pass) => (preview?.prompts?.[pass]?.notes || []).map((note) => `${pass}: ${note}`))
    .map((note) => `<p class="rule-notice">${esc(note)}</p>`)
    .join("");
  const suggestion = state.schema.suggestion;
  const matchedEntities = (suggestion?.matches?.entity_types || []).slice(0, 12).map((row) => `<span class="type-pill" title="Matched terms: ${esc((row.matched_terms || []).join(", "))}">${esc(row.label)} · ${formatNumber(row.score)}</span>`).join("");
  const matchedRelationships = (suggestion?.matches?.relationship_types || []).slice(0, 8).map((row) => `<span class="count-pill" title="Matched terms: ${esc((row.matched_terms || []).join(", "))}">${esc(row.label)}</span>`).join("");
  const suggestionResult = suggestion ? `<div class="schema-suggestion-result"><p class="rule-notice">${esc(suggestion.warning || suggestion.notice || "Review this lexical suggestion before saving.")}</p>${matchedEntities ? `<div><span class="eyebrow">Matched entity types</span><div class="match-chips">${matchedEntities}</div></div>` : ""}${matchedRelationships ? `<details><summary>Matched relationship terms</summary><div class="match-chips">${matchedRelationships}</div></details>` : ""}<small>${esc(suggestion.strategy || "deterministic lexical match")} · not saved</small></div>` : "";

  const missionSuggestion = state.schema.missionSuggestion;
  const missionEntities = (missionSuggestion?.matches?.entity_types || []).slice(0, 24).map((row) => `<span class="type-pill">${esc(row.label)}</span>`).join("");
  const missionModules = (missionSuggestion?.matches?.modules || []).map((row) => `<span class="count-pill">${esc(row.label)}</span>`).join("");
  const missionResult = missionSuggestion ? `<div class="schema-suggestion-result"><p class="rule-notice">${esc(missionSuggestion.warning || missionSuggestion.notice || "Review this model-generated suggestion before saving.")}</p>${missionSuggestion.rationale ? `<p><strong>Model rationale:</strong> ${esc(missionSuggestion.rationale)}</p>` : ""}${missionModules ? `<div><span class="eyebrow">Selected modules</span><div class="match-chips">${missionModules}</div></div>` : ""}${missionEntities ? `<div><span class="eyebrow">Selected entity types</span><div class="match-chips">${missionEntities}</div></div>` : ""}<small>${esc(missionSuggestion.strategy || "llm mission narrowing")} · ${esc(missionSuggestion.model_id || "")} · not saved</small></div>` : "";

  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead(
      "Extraction schema",
      "Choose what to extract",
      `${esc(catalogue.metadata.title || "Targeting extraction context")} v${esc(catalogue.metadata.version || "")}. Both extraction passes are compiled from the slice selected here: the entity pass may only emit these type ids, and only predicates whose domain and range are covered by them stay in scope.`,
      `<button class="button button-primary" id="schemaSave" disabled>Save selection</button><button class="button button-secondary" data-action="schema-reset">Reset to defaults</button><button class="button button-secondary" data-action="schema-prompt" data-pass="ENTITY">Entity prompt</button><button class="button button-secondary" data-action="schema-prompt" data-pass="RELATIONSHIP">Relationship prompt</button>`,
    )}
    <p class="rule-notice" id="schemaDirty" hidden>Unsaved changes. Extraction runs against the last saved selection until you save.</p>
    <section class="panel schema-topic-panel"><div><span class="eyebrow">Narrow by topic</span><h2>Suggest a reviewable ontology slice</h2><p>Deterministic lexical matching only. The suggestion fills the manual draft below; it never calls a model and never saves automatically.</p></div><form id="schemaSuggestForm" class="schema-topic-form"><label class="sr-only" for="schemaTopic">Extraction topic</label><input id="schemaTopic" class="field" type="search" value="${esc(state.schema.suggestionTopic)}" placeholder="For example: ports, manufacturing or air-defence systems" autocomplete="off"><button class="button button-secondary" ${state.schema.suggestBusy ? "disabled" : ""}>${state.schema.suggestBusy ? "Matching…" : "Narrow draft"}</button></form>${suggestionResult}</section>
    <section class="panel schema-topic-panel"><div><span class="eyebrow">Narrow by mission statement</span><h2>Ask a model to propose a reviewable ontology slice</h2><p>Sends the mission statement and the ontology's ids, labels and definitions (never case data) to the configured model. The model may only pick ids that already exist in this schema; the suggestion fills the manual draft below and nothing is saved automatically.</p></div><form id="schemaMissionForm" class="schema-topic-form schema-mission-form"><label class="sr-only" for="schemaMission">Mission statement</label><textarea id="schemaMission" class="field" rows="3" placeholder="For example: Identify and characterise adversary air-defence command nodes and their supporting logistics to support a suppression mission." autocomplete="off">${esc(state.schema.missionText)}</textarea><button class="button button-secondary" ${state.schema.missionBusy ? "disabled" : ""}>${state.schema.missionBusy ? "Narrowing…" : "Narrow with AI"}</button></form>${missionResult}</section>
    ${warnings}${promptNotes}
    <div class="metric-grid">
      <div class="metric-card"><span class="eyebrow">Modules</span><strong>${formatNumber(counts.modules)}</strong><span>of ${catalogue.modules.length}</span></div>
      <div class="metric-card"><span class="eyebrow">Entity types</span><strong>${formatNumber(counts.entity_types)}</strong><span>the entity pass may emit</span></div>
      <div class="metric-card"><span class="eyebrow">Predicates</span><strong>${formatNumber(counts.relationship_types)}</strong><span>${formatNumber((preview?.dropped_relationships || []).length)} out of scope</span></div>
      <div class="metric-card"><span class="eyebrow">Prompt fit</span><strong>${preview?.ready ? "Ready" : "Too broad"}</strong><span>${preview?.ready ? "both passes fit the budget" : "narrow the selection"}</span></div>
    </div>
    <div class="source-grid">
      <section class="panel source-card">
        <div class="panel-head"><div><h2>Modules</h2><p>Each module is reference-closed. CORE carries identity, evidence and assertion types and is always loaded.</p></div></div>
        <div class="pick-list">${moduleRows}</div>
      </section>
      <section class="panel source-card">
        <div class="panel-head"><div><h2>Entity types</h2><p>${formatNumber(chosen.size)} of ${formatNumber(available.length)} selected in the chosen modules.</p></div><div><button class="text-action" data-action="schema-all">Select all</button> · <button class="text-action" data-action="schema-none">Clear</button></div></div>
        <div class="filter-bar">
          <label class="sr-only" for="schemaSearch">Filter entity types</label>
          <input id="schemaSearch" type="search" placeholder="Filter by name, id or definition" value="${esc(state.schema.filter.query)}" autocomplete="off">
          <label class="sr-only" for="schemaModuleFilter">Module</label>
          <select id="schemaModuleFilter">${moduleOptions}</select>
        </div>
        <div class="pick-list is-tall">${entityRows || `<p>No entity type matches this filter.</p>`}</div>
      </section>
      <section class="panel source-card">
        <div class="panel-head"><div><h2>Predicates in scope</h2><p>Derived from the entity selection. A predicate is only askable when a selected type sits at both ends.</p></div><b class="count-pill">${formatNumber(counts.relationship_types)}</b></div>
        <div class="system-list is-scroll">${predicateRows || `<p>No predicate survives this selection.</p>`}</div>
      </section>
      <section class="panel source-card">
        <div class="panel-head"><div><h2>Compiled prompts</h2><p>Prompt size is estimated. Pre-tokenise for the deployed model before a production run.</p></div></div>
        <div class="system-list">
          ${promptBadge(preview?.prompts?.ENTITY)}
          ${promptBadge(preview?.prompts?.RELATIONSHIP)}
          <div class="system-row"><span>Controlled value sets</span><strong>${formatNumber(counts.value_sets)}</strong></div>
        </div>
        <details><summary>Predicates excluded by the closure (${formatNumber((preview?.dropped_relationships || []).length)})</summary><div class="system-list is-scroll">${droppedRows || `<p>None.</p>`}</div></details>
      </section>
    </div>
  </div>`;
  bindSchemaInputs();
  $("#schemaSuggestForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.schema.suggestionTopic = $("#schemaTopic").value;
    await suggestSchemaTopic();
  });
  $("#schemaTopic")?.addEventListener("input", (event) => {
    state.schema.suggestionTopic = event.target.value;
  });
  $("#schemaMissionForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.schema.missionText = $("#schemaMission").value;
    await suggestSchemaMission();
  });
  $("#schemaMission")?.addEventListener("input", (event) => {
    state.schema.missionText = event.target.value;
  });
  updateSchemaSummary();
}

function bindSchemaInputs() {
  $("#schemaSave")?.addEventListener("click", async () => {
    try {
      state.schema.preview = await api("/api/schema/selection", {
        method: "POST",
        body: JSON.stringify(draftRequest()),
      });
      state.schema.saved = draftRequest();
      state.schema.dirty = false;
      state.schema.suggestion = null;
      state.schema.missionSuggestion = null;
      // Sources reads the active slice from /api/health, so it has to be
      // re-read or that panel keeps quoting the previous counts.
      state.health = await api("/api/health");
      toast("Selection saved", "New extractions will run against this slice.");
      renderSchema();
    } catch (error) {
      toast("Selection could not be saved", error.message, true);
    }
  });
  $("#schemaSearch")?.addEventListener("input", (event) => {
    state.schema.filter.query = event.target.value;
    const list = $(".pick-list.is-tall");
    const position = list?.scrollTop || 0;
    renderSchema();
    $("#schemaSearch").focus();
    const refreshed = $(".pick-list.is-tall");
    if (refreshed) refreshed.scrollTop = position;
  });
  $("#schemaModuleFilter")?.addEventListener("change", (event) => {
    state.schema.filter.module = event.target.value;
    renderSchema();
  });
  $$("[data-schema-module]").forEach((input) =>
    input.addEventListener("change", (event) => {
      const id = event.target.dataset.schemaModule;
      if (event.target.checked) {
        state.schema.draft.modules.add(id);
        // A newly selected module arrives with its types on, matching the
        // shorthand the saved selection uses.
        availableEntityTypes(new Set([id])).forEach((row) => state.schema.draft.entities.add(row.id));
      } else {
        state.schema.draft.modules.delete(id);
      }
      schedulePreview();
      renderSchema();
    }),
  );
  $$("[data-schema-entity]").forEach((input) =>
    input.addEventListener("change", (event) => {
      const id = event.target.dataset.schemaEntity;
      if (event.target.checked) state.schema.draft.entities.add(id);
      else state.schema.draft.entities.delete(id);
      schedulePreview();
    }),
  );
}

/* ---------------------------------------------------------- PRIMROSE views */

const PRIORITY_QUEUES = [
  ["act", "Act", "Analyst-review actions whose hard gates pass."],
  ["collect", "Collect", "Decision-relevant evidence questions."],
  ["challenge", "Challenge", "Material contradictions requiring review."],
  ["hypotheses", "Hypotheses", "Model or rule candidates kept outside the accepted graph."],
  ["monitor", "Monitor", "Records that do not meet another route."],
];

function observationFor(item, methodId) {
  return (item?.metrics || []).find((row) => row.method_id === methodId)
    || item?.summary_metrics?.[methodId]
    || null;
}

function observationValue(observation, { normalised = false } = {}) {
  if (!observation || observation.applicability !== "available") return "Unavailable";
  const value = normalised ? observation.normalised_value : observation.raw_value;
  if (value === null || value === undefined || value === "") return "Unavailable";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (normalised) return `${Math.round(value * 100)}%`;
    return Math.abs(value) < 0.1 && value !== 0 ? value.toFixed(4) : value.toFixed(2);
  }
  return String(value);
}

function scoreValue(score) {
  return score?.value === null || score?.value === undefined
    ? "Unavailable"
    : `${Math.round(Number(score.value) * 100)}%`;
}

function methodInfoButton(methodId, label = "method") {
  const method = state.methods.catalogue?.items?.find((row) => row.id === methodId);
  const tooltipId = `method-tip-${String(methodId).replace(/[^A-Za-z0-9_-]/g, "-")}`;
  const explanation = method?.definition || `Open the ${label} definition, calculation, inputs and limitations.`;
  return `<span class="method-info-wrap"><button class="icon-button method-info-button" type="button" data-action="method-info" data-method-id="${esc(methodId)}" aria-label="Open ${esc(label)} definition and calculation" aria-describedby="${tooltipId}">i</button><span class="method-tooltip" id="${tooltipId}" role="tooltip"><strong>${esc(method?.display_name || label)}</strong><span>${esc(explanation)}</span><em>${method?.contributes_to_score ? `Contributes to: ${esc((method.score_profiles || []).join(", "))}` : "Diagnostic only; no score weight"}</em></span></span>`;
}

function priorityRoutePill(route) {
  const tone = route === "act" ? "accepted" : route === "challenge" ? "rejected" : "unreviewed";
  return `<span class="status-pill status-${tone}">${esc(route || "monitor")}</span>`;
}

async function loadPriorities(force = false) {
  if (state.priorities.bootstrap && !force) return;
  const [bootstrap, catalogue] = await Promise.all([
    api("/api/workbench/bootstrap"),
    api("/api/workbench/methods"),
  ]);
  state.priorities.bootstrap = bootstrap;
  state.methods.catalogue = catalogue;
  const current = state.priorities.activeQueue;
  const nextQueue = bootstrap.counts?.[current]
    ? current
    : PRIORITY_QUEUES.find(([id]) => bootstrap.counts?.[id])?.[0] || "monitor";
  state.priorities.activeQueue = nextQueue;
  state.priorities.selected = bootstrap.selected_object
    || bootstrap.queues?.[nextQueue]?.[0]
    || PRIORITY_QUEUES.flatMap(([id]) => bootstrap.queues?.[id] || [])[0]
    || null;
  const total = Object.values(bootstrap.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  const priorityBadge = $("#priorityNavCount");
  if (priorityBadge) priorityBadge.textContent = formatNumber(total);
  const methodBadge = $("#methodNavCount");
  if (methodBadge) methodBadge.textContent = formatNumber(catalogue.total || catalogue.items?.length || 0);
}

async function selectPriorityItem(kind, id) {
  const detail = await api(`/api/workbench/objects/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
  state.priorities.selected = detail;
  renderPriorities();
}

function renderPriorityMetric(item, methodId, title, value) {
  const observation = observationFor(item, methodId);
  const reason = observation?.applicability === "available"
    ? observation.band || "Calculated from the live graph"
    : observation?.reason || "Required input unavailable";
  return `<article class="metric-card priority-metric">
    <div class="metric-title"><span>${esc(title)}</span>${methodInfoButton(methodId, title)}</div>
    <strong>${esc(value)}</strong><small>${esc(reason)}</small>
  </article>`;
}

function renderPriorities() {
  const bootstrap = state.priorities.bootstrap;
  if (!bootstrap) return renderFailure(new Error("The PRIMROSE workbench API returned no data."));
  const activeQueue = state.priorities.activeQueue;
  const selected = state.priorities.selected;
  const query = state.priorities.filter.trim().toLowerCase();
  const items = (bootstrap.queues?.[activeQueue] || []).filter((item) =>
    !query || `${item.label} ${item.id} ${item.type}`.toLowerCase().includes(query));
  const queueButtons = PRIORITY_QUEUES.map(([id, label]) => `<button class="priority-tab${id === activeQueue ? " is-active" : ""}" type="button" data-action="priority-queue" data-queue="${id}" aria-pressed="${id === activeQueue}"><span>${label}</span><b>${formatNumber(bootstrap.counts?.[id] || 0)}</b></button>`).join("");
  const rows = items.map((item) => {
    const pagerank = observationValue(observationFor(item, "centrality.pagerank"));
    return `<button class="queue-item priority-item${selected?.id === item.id ? " is-active" : ""}" type="button" data-action="priority-select" data-kind="${esc(item.kind || "node")}" data-id="${esc(item.id)}">
      <span><strong>${esc(item.label)}</strong><span>${esc(item.type)} · ${esc(item.epistemic_state || item.review_status)}</span></span>
      <span class="priority-row-meta">${priorityRoutePill(item.route)}<b class="count-pill">PageRank ${esc(pagerank)}</b></span>
    </button>`;
  }).join("");
  const activeDescription = PRIORITY_QUEUES.find(([id]) => id === activeQueue)?.[2] || "";
  const metrics = selected ? [
    renderPriorityMetric(selected, "mission.consequence", "Mission consequence", observationValue(observationFor(selected, "mission.consequence"), { normalised: true })),
    renderPriorityMetric(selected, "evidence.independent_corroboration", "Evidential confidence", scoreValue(selected.scores?.evidential_confidence)),
    renderPriorityMetric(selected, "centrality.pagerank", "PageRank", observationValue(observationFor(selected, "centrality.pagerank"))),
    renderPriorityMetric(selected, "structure.bridge_participation", "Bridge participation", observationValue(observationFor(selected, "structure.bridge_participation"))),
  ].join("") : "";
  const routeTrace = (selected?.route_trace || []).map((row) => `<li>${esc(row)}</li>`).join("");
  const gates = (selected?.gates || []).map((gate) => `<div class="system-row"><span>${esc(gate.id.replaceAll("_", " "))}</span><strong class="${gate.passed ? "gate-pass" : "gate-hold"}">${gate.passed ? "Pass" : "Held"}</strong><small>${esc(gate.reason)}</small></div>`).join("");
  const evidence = selected?.evidence?.supporting || [];
  const evidenceRows = evidence.map((row) => `<div class="evidence-block"><span class="eyebrow">${esc(row.title)}</span>${esc(row.passage || "No passage supplied.")}<small>Lineage: ${esc(row.lineage || "unavailable")}</small></div>`).join("");
  const selectedPanel = selected ? `<article class="panel priority-detail">
    <div class="panel-head"><div><span class="eyebrow">Selected live record</span><h2>${esc(selected.label)}</h2></div>${priorityRoutePill(selected.route)}</div>
    <div class="review-meta"><span class="type-pill"><i class="type-dot ${typeColourClass(selected.type)}"></i>${esc(selected.type)}</span>${statusPill(selected.review_status)}</div>
    <div class="detail-section"><h3>Recommended analyst step</h3><p>${esc(selected.recommended_action)}</p></div>
    <div class="detail-section"><h3>Routing trace</h3>${routeTrace ? `<ol class="trace-list">${routeTrace}</ol>` : `<p>No routing trace was returned.</p>`}</div>
    <div class="detail-section"><h3>Hard gates</h3><div class="system-list gate-list">${gates || "<p>No gates returned.</p>"}</div></div>
    <div class="detail-section"><h3>Source support</h3>${evidenceRows || `<p>${esc(selected.evidence?.notice || "No assertion-level source passage is attached.")}</p>`}</div>
    <div class="head-actions priority-actions"><button class="button button-secondary" type="button" data-action="priority-open-graph" data-id="${esc(selected.id)}">Open in graph</button>${selected.target_development?.available ? `<button class="button button-primary" type="button" data-action="priority-open-target" data-id="${esc(selected.id)}">Open target development</button>` : ""}</div>
  </article>` : `<div class="panel empty-state"><div><strong>No priority record</strong><p>This queue contains no live records.</p></div></div>`;

  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("PRIMROSE analytics", "Priorities and analyst work queues", "Live diagnostics over accepted and unreviewed graph records. Missing evidence is unavailable, never silently converted to zero.", `<button class="button button-secondary" data-action="priority-refresh">Refresh calculations</button>`)}
    <p class="rule-notice">${esc(bootstrap.handling || bootstrap.notice || "Diagnostic output only.")}</p>
    ${selected ? `<div class="metric-grid">${metrics}</div>` : ""}
    <div class="priority-tabs" role="group" aria-label="PRIMROSE work queues">${queueButtons}</div>
    <p class="queue-description">${esc(activeDescription)}</p>
    <div class="filter-bar"><label class="sr-only" for="priorityFilter">Filter current priority queue</label><input id="priorityFilter" type="search" value="${esc(state.priorities.filter)}" placeholder="Filter this queue by label, id or type"></div>
    <div class="priority-layout"><section class="panel priority-queue"><div class="queue-list">${rows || `<div class="empty-state"><div><strong>No matching records</strong><p>Change the queue or filter.</p></div></div>`}</div></section>${selectedPanel}</div>
  </div>`;
  $("#priorityFilter")?.addEventListener("input", (event) => {
    state.priorities.filter = event.target.value;
    renderPriorities();
    $("#priorityFilter")?.focus();
  });
}

async function loadTargetDevelopment(force = false) {
  if (state.target.meta && state.target.candidates.length && !force) return;
  const [meta, candidates] = await Promise.all([
    api("/api/target-development/meta"),
    api(`/api/target-development/candidates?limit=100&offset=0&q=${encodeURIComponent(state.target.query)}`),
  ]);
  state.target.meta = meta;
  state.target.candidates = candidates.items || [];
  state.target.total = candidates.total || state.target.candidates.length;
  const preferred = state.target.selectedId || state.priorities.selected?.id || "";
  state.target.selectedId = state.target.candidates.some((row) => row.id === preferred)
    ? preferred
    : state.target.candidates[0]?.id || "";
  if (!meta.phases?.some((row) => row.id === state.target.phase)) {
    state.target.phase = meta.phases?.[0]?.id || "basic";
  }
}

function targetFactValue(value) {
  if (value === null || value === undefined || value === "") return "Unavailable";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function runTargetDevelopment() {
  if (!state.target.selectedId || state.target.busy) return;
  state.target.busy = true;
  renderTargetDevelopment();
  try {
    state.target.preview = await api("/api/target-development/preview", {
      method: "POST",
      body: JSON.stringify({ target_id: state.target.selectedId, phase: state.target.phase, depth: 2, limit: 90 }),
    });
    toast("Dossier assembled", "The deterministic target-development record is ready.");
  } catch (error) {
    state.target.preview = null;
    toast("Target development stopped", error.message, true);
  } finally {
    state.target.busy = false;
    renderTargetDevelopment();
  }
}

async function pollTargetGeneration(jobId) {
  try {
    const job = await api(`/api/target-development/jobs/${encodeURIComponent(jobId)}`);
    state.target.job = job;
    if (["completed", "completed_with_errors"].includes(job.status) && job.result) {
      state.target.preview = job.result;
      toast("Model generation completed", job.message);
    } else if (job.status === "failed") {
      toast("Model generation failed", job.error || job.message, true);
    } else {
      window.setTimeout(() => pollTargetGeneration(jobId), 1800);
    }
    if (state.view === "target") renderTargetDevelopment();
  } catch (error) {
    toast("Generation status unavailable", error.message, true);
  }
}

async function startTargetGeneration() {
  if (!state.target.selectedId || state.target.busy || !state.target.meta?.model_generation?.available) return;
  state.target.busy = true;
  renderTargetDevelopment();
  try {
    const job = await api("/api/target-development/jobs", {
      method: "POST",
      body: JSON.stringify({ target_id: state.target.selectedId, phase: state.target.phase, depth: 2, limit: 90 }),
    });
    state.target.job = job;
    toast("Model generation queued", `${job.target_label} is waiting for the configured model worker.`);
    window.setTimeout(() => pollTargetGeneration(job.id), 500);
  } catch (error) {
    state.target.job = null;
    toast("Model generation could not start", error.message, true);
  } finally {
    state.target.busy = false;
    renderTargetDevelopment();
  }
}

function renderTargetDevelopment() {
  const meta = state.target.meta;
  if (!meta) return renderFailure(new Error("Target-development metadata is unavailable."));
  const selected = state.target.candidates.find((row) => row.id === state.target.selectedId) || null;
  const canPreview = Boolean(selected?.screening?.adapter_allows_preview);
  const candidateOptions = state.target.candidates.map((row) => `<option value="${esc(row.id)}" ${row.id === state.target.selectedId ? "selected" : ""}>${esc(row.label)} · ${esc(row.type)}</option>`).join("");
  const phaseOptions = (meta.phases || []).map((row) => `<option value="${esc(row.id)}" ${row.id === state.target.phase ? "selected" : ""}>${esc(row.title)}</option>`).join("");
  const safety = (meta.safety_boundary || []).map((row) => `<li>${esc(row)}</li>`).join("");
  const preview = state.target.preview;
  const job = state.target.job;
  const modelCalled = Boolean(job && ["completed", "completed_with_errors"].includes(job.status) && job.result === preview) || Boolean(preview?.adapter?.model_called);
  const sections = (preview?.sections || []).map((section, index) => {
    const facts = Object.entries(section.graph_facts || {}).map(([key, value]) => `<div class="system-row"><span>${esc(key.replaceAll("_", " "))}</span><strong>${esc(targetFactValue(value))}</strong></div>`).join("");
    const requirements = (section.required_elements || []).map((row) => `<li>${esc(row)}</li>`).join("");
    // A model narrative is the adapter's own commentary, not an established
    // graph fact - it gets its own labelled box so a reader never mistakes it
    // for one of the deterministic facts above it. Absent a model call, the
    // fallback text is not an AI insight and stays in the plain notice style.
    const narrativeBlock = section.narrative
      ? `<div class="ai-insight-box"><span class="eyebrow">AI insights</span><p>${esc(section.narrative)}</p></div>`
      : `<p class="rule-notice">No model was called. This view exposes deterministic facts and gaps only.</p>`;
    return `<details class="target-section" ${index === 0 ? "open" : ""}><summary><span>${String(index + 1).padStart(2, "0")}</span><strong>${esc(section.title)}</strong><span>${section.mandatory ? "Mandatory" : "Optional"}</span></summary><div><p>${esc(section.definition)}</p><h3>Established graph facts</h3><div class="system-list">${facts || "<p>No required fact is established in the current graph.</p>"}</div><h3>Required elements</h3><ul>${requirements}</ul>${narrativeBlock}${section.error ? `<p class="rule-notice">${esc(section.error)}</p>` : ""}</div></details>`;
  }).join("");
  const resultNotice = modelCalled
    ? (preview?.notice || "Model narratives are separated from the established graph facts below.")
    : "Deterministic graph facts and explicit gaps only; no language model was called.";
  const result = preview ? `<section class="panel target-result"><div class="panel-head"><div><span class="eyebrow">${esc(preview.phase_title)}</span><h2>${esc(preview.target.label)}</h2><p>${esc(preview.phase_purpose)}</p></div><span class="status-pill status-accepted">${formatNumber(preview.sections.length)} sections</span></div><p class="rule-notice">${esc(resultNotice)}</p><div class="review-meta"><span class="type-pill">${formatNumber(preview.evidence.entities)} entities</span><span class="type-pill">${formatNumber(preview.evidence.relationships)} relationships</span><span class="type-pill">Model called: ${modelCalled ? "yes" : "no"}</span></div><div class="target-sections">${sections}</div></section>` : `<section class="panel empty-state"><div><strong>No dossier assembled</strong><p>Choose an eligible live graph entity and build the deterministic dossier.</p></div></section>`;
  const generationReady = Boolean(meta.model_generation?.available);
  const jobStatus = job ? `<div class="job-row target-job"><strong>${esc(job.target_label || selected?.label || "Target")} · ${esc(job.status)}</strong><span>${esc(job.message || "")}</span>${job.model?.model_id ? `<span>Model: ${esc(job.model.model_id)}</span>` : ""}${job.error ? `<span class="error-copy">${esc(job.error)}</span>` : ""}</div>` : "";

  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Fail-closed core adapter", "Target development insights", "Assemble a deterministic dossier from the live graph while preserving entity extraction and graph reasoning. Restricted, protected and personnel records are stopped by the hardened Python screening gate.")}
    <p class="rule-notice">${esc(meta.handling)}</p>
    <div class="target-layout"><section class="panel target-controls"><div class="panel-head"><div><h2>Dossier inputs</h2><p>Showing ${formatNumber(state.target.candidates.length)} of ${formatNumber(state.target.total)} matching screened records</p></div><span class="status-pill status-unreviewed">${esc(meta.mode)}</span></div>
      <form id="targetSearchForm" class="filter-bar target-search"><label class="sr-only" for="targetSearch">Find a target-development candidate</label><input id="targetSearch" type="search" value="${esc(state.target.query)}" placeholder="Find candidate by label or exact id"><button class="button button-secondary">Find</button></form>
      <div class="filter-bar target-fields"><label for="targetCandidate">Entity</label><select id="targetCandidate" class="field" ${candidateOptions ? "" : "disabled"}>${candidateOptions || `<option>No eligible candidates</option>`}</select><label for="targetPhase">Phase</label><select id="targetPhase" class="field">${phaseOptions}</select></div>
      <div class="target-actions"><button id="targetRun" class="button button-primary" type="button" ${!canPreview || state.target.busy ? "disabled" : ""}>${state.target.busy ? "Working…" : "Build deterministic dossier"}</button>${generationReady && canPreview ? `<button id="targetGenerate" class="button button-secondary" type="button" ${state.target.busy || ["queued", "running"].includes(job?.status) ? "disabled" : ""}>Generate model narratives</button>` : ""}</div>
      ${selected ? `<div class="screening-card"><div><span class="eyebrow">Screening result</span><h3>${esc(selected.screening.category)}</h3></div>${selected.screening.adapter_allows_preview ? `<span class="status-pill status-accepted">developable</span>` : `<span class="status-pill status-rejected">held</span>`}<p>${esc(selected.screening.rationale)}</p></div>` : ""}
      ${jobStatus}<div class="detail-section"><h3>Safety boundary</h3><ul>${safety}</ul></div><p class="rule-notice">Model generation: ${esc(meta.model_generation?.status || "disabled")}. ${esc(meta.model_generation?.reason || "")}</p>
    </section>${result}</div>
  </div>`;
  $("#targetCandidate")?.addEventListener("change", (event) => {
    state.target.selectedId = event.target.value;
    state.target.preview = null;
    state.target.job = null;
    renderTargetDevelopment();
  });
  $("#targetSearchForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.target.query = $("#targetSearch").value.trim();
    state.target.candidates = [];
    state.target.preview = null;
    state.target.job = null;
    await loadTargetDevelopment(true);
    renderTargetDevelopment();
  });
  $("#targetPhase")?.addEventListener("change", (event) => {
    state.target.phase = event.target.value;
    state.target.preview = null;
    state.target.job = null;
    renderTargetDevelopment();
  });
  $("#targetRun")?.addEventListener("click", () => runTargetDevelopment());
  $("#targetGenerate")?.addEventListener("click", () => startTargetGeneration());
}

async function loadMethods(force = false) {
  if (state.methods.catalogue && state.methods.profiles && !force) return;
  const [catalogue, profiles] = await Promise.all([
    api("/api/workbench/methods"),
    api("/api/workbench/profiles"),
  ]);
  state.methods.catalogue = catalogue;
  state.methods.profiles = profiles;
  const badge = $("#methodNavCount");
  if (badge) badge.textContent = formatNumber(catalogue.total || catalogue.items?.length || 0);
}

function methodApplicability(method) {
  const observation = observationFor(state.priorities.selected, method.id);
  if (observation?.applicability === "available") return ["accepted", "Calculated"];
  if (method.default_enabled) return ["unreviewed", "Input unavailable"];
  return ["rejected", "Adapter disabled"];
}

function renderMethods() {
  const catalogue = state.methods.catalogue;
  const profiles = state.methods.profiles;
  if (!catalogue) return renderFailure(new Error("The method registry is unavailable."));
  const methods = catalogue.items || [];
  const families = [...new Set(methods.map((row) => row.family))].sort();
  const query = state.methods.query.trim().toLowerCase();
  const filtered = methods.filter((method) => (state.methods.family === "all" || method.family === state.methods.family) && (!query || `${method.display_name} ${method.definition} ${method.analytical_question}`.toLowerCase().includes(query)));
  const familyOptions = [`<option value="all">All method families</option>`].concat(families.map((family) => `<option value="${esc(family)}" ${family === state.methods.family ? "selected" : ""}>${esc(family)}</option>`)).join("");
  const cards = filtered.map((method) => {
    const [tone, label] = methodApplicability(method);
    return `<article class="method-card"><div class="method-card-head"><span class="type-pill">${esc(method.family)}</span>${methodInfoButton(method.id, method.display_name)}</div><h2>${esc(method.display_name)}</h2><p>${esc(method.analytical_question)}</p><div class="method-card-foot"><span class="status-pill status-${tone}">${label}</span><span>${esc(method.computational_cost)} cost</span></div><button class="text-action" type="button" data-action="method-info" data-method-id="${esc(method.id)}">Definition, calculation and limitations →</button></article>`;
  }).join("");
  const profileRows = (profiles?.items || []).map((profile) => `<details class="profile-row"><summary><strong>${esc(profile.name)}</strong><span>v${esc(profile.version)}</span></summary><p>${esc(profile.description)}</p><div class="system-list">${Object.entries(profile.weights || {}).map(([key, value]) => `<div class="system-row"><span>${esc(key)}</span><strong>${Math.round(Number(value) * 100)}%</strong></div>`).join("")}</div></details>`).join("");
  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Registry-driven analytics", "Methods and metrics", "Every exposed method includes its definition, calculation, formula, raw LaTeX, inputs, assumptions, limitations and scoring role.")}
    <div class="filter-bar"><label class="sr-only" for="methodSearch">Search methods</label><input id="methodSearch" type="search" value="${esc(state.methods.query)}" placeholder="Search methods and analytical questions"><label class="sr-only" for="methodFamily">Method family</label><select id="methodFamily">${familyOptions}</select></div>
    <div class="method-grid">${cards || `<div class="panel empty-state"><div><strong>No matching methods</strong><p>Change the search or family filter.</p></div></div>`}</div>
    <section class="panel profile-panel"><div class="panel-head"><div><h2>Versioned weight profiles</h2><p>Unavailable contributors are not converted to zero and profiles are not reweighted silently.</p></div></div>${profileRows || "<p>No profiles returned.</p>"}</section>
  </div>`;
  $("#methodSearch")?.addEventListener("input", (event) => {
    state.methods.query = event.target.value;
    renderMethods();
    $("#methodSearch")?.focus();
  });
  $("#methodFamily")?.addEventListener("change", (event) => {
    state.methods.family = event.target.value;
    renderMethods();
  });
}

async function openMethodInfo(methodId) {
  await loadMethods();
  const method = state.methods.catalogue?.items?.find((row) => row.id === methodId);
  if (!method) throw new Error(`Unknown method: ${methodId}`);
  const observation = observationFor(state.priorities.selected, method.id);
  const trace = (observation?.trace || []).map((row) => `<li>${esc(row)}</li>`).join("");
  const tracePanel = observation ? `<div class="detail-section"><h3>Selected-object trace</h3>${observation.applicability === "available" ? `<p><strong>${esc(observationValue(observation))}</strong>${observation.band ? ` · ${esc(observation.band)}` : ""}</p><ol class="trace-list">${trace}</ol>` : `<p>${esc(observation.reason || "Required inputs are unavailable.")}</p>`}</div>` : "";
  $("#drawerTitle").textContent = method.display_name;
  $("#detailDrawer").setAttribute("aria-label", "Method details");
  $("#drawerBody").innerHTML = `<div class="review-meta"><span class="type-pill">${esc(method.family)}</span><span class="count-pill">v${esc(method.version)}</span></div><div class="detail-section"><h3>Definition</h3><p>${esc(method.definition)}</p></div><div class="detail-section"><h3>Analytical question</h3><p>${esc(method.analytical_question)}</p></div>${tracePanel}<div class="system-list method-definition-list"><div class="system-row"><span>Calculation</span><strong>${esc(method.calculation)}</strong></div><div class="system-row"><span>Interpretation</span><strong>${esc(method.interpretation)}</strong></div><div class="system-row"><span>Formula</span><strong>${esc(method.formula)}</strong></div><div class="system-row"><span>Raw LaTeX</span><strong><code>${esc(method.raw_latex || "Not applicable")}</code></strong></div><div class="system-row"><span>Required inputs</span><strong>${esc((method.required_inputs || []).join(" · "))}</strong></div><div class="system-row"><span>Normalisation</span><strong>${esc(method.normalisation)}</strong></div><div class="system-row"><span>Output</span><strong>${esc(method.output_range_units)}</strong></div><div class="system-row"><span>Assumptions</span><strong>${esc(method.assumptions)}</strong></div><div class="system-row definition-warning"><span>Limitations and failure modes</span><strong>${esc(method.limitations)}</strong></div><div class="system-row"><span>Scoring role</span><strong>${method.contributes_to_score ? esc((method.score_profiles || []).join(" · ")) : "Diagnostic only; no numerical weight"}</strong></div><div class="system-row"><span>Reference</span><strong>${esc(method.reference)}</strong></div></div>`;
  state.drawerReturnFocus = document.activeElement;
  $("#detailDrawer").removeAttribute("inert");
  $("#detailDrawer").classList.add("is-open");
  $("#detailDrawer").setAttribute("aria-hidden", "false");
  $("#drawerScrim").hidden = false;
  $("#closeDrawer").focus();
}

async function loadAuditStatus() {
  const [health, audit, adapters] = await Promise.all([
    api("/api/health"),
    api("/api/audit?limit=100"),
    api("/api/workbench/adapters"),
  ]);
  state.health = health;
  state.audit.items = audit.items || [];
  state.audit.adapters = adapters;
}

function readinessPill(ready, readyText = "Ready", heldText = "Unavailable") {
  return `<span class="status-pill status-${ready ? "accepted" : "unreviewed"}">${ready ? readyText : heldText}</span>`;
}

function renderAuditStatus() {
  const health = state.health || {};
  const reasoning = health.reasoning || {};
  const extraction = health.extraction || {};
  const schema = health.schema || {};
  const adapters = state.audit.adapters?.items || [];
  const adapterCards = adapters.map((adapter) => {
    const ready = adapter.id === "retrieval.graphrag"
      ? Boolean(reasoning.available && reasoning.endpoint_reachable)
      : adapter.status === "ready"
        || (adapter.status === "available" && adapter.endpoint_reachable !== false);
    return `<article class="adapter-card"><div class="panel-head"><h3>${esc(adapter.name)}</h3>${readinessPill(ready, "Ready", esc(adapter.status))}</div><p>${esc(adapter.reason || "Configured and ready.")}</p><code>${esc(adapter.reason_code || adapter.id)}</code></article>`;
  }).join("");
  const auditRows = state.audit.items.map((row) => `<article class="audit-row"><time>${esc(row.timestamp ? new Date(row.timestamp).toLocaleString("en-GB") : "Unknown time")}</time><div><strong>${esc(String(row.action || "event").replaceAll("_", " "))}</strong><span>${esc(row.kind || "record")} · ${esc(row.item_id || "")}</span><p>${esc(row.reason || "No reason recorded.")}</p><small>${esc(row.analyst || "System")} · ${esc(row.previous || "")} to ${esc(row.next || "")}</small></div></article>`).join("");
  $("#mainContent").innerHTML = `<div class="page">
    ${pageHead("Live assurance", "Audit and system status", "Runtime capability states and the persisted graph review audit. Nothing on this page is inferred from a bundled snapshot.", `<button class="button button-secondary" data-action="audit-refresh">Refresh status</button>`)}
    <div class="metric-grid"><div class="metric-card"><span>Python backend</span><strong>${health.status === "ok" ? "Ready" : "Unavailable"}</strong><small>${esc(health.application || "No application identity")}</small></div><div class="metric-card"><span>Document extraction</span><strong>${extraction.ready ? "Ready" : extraction.available ? "Model offline" : "Unavailable"}</strong><small>${esc(extraction.model_id || "No model configured")} · endpoint ${extraction.endpoint_reachable ? "reachable" : "offline"}</small></div><div class="metric-card"><span>GraphRAG</span><strong>${reasoning.available && reasoning.endpoint_reachable ? "Ready" : "Unavailable"}</strong><small>${esc(reasoning.model_id || "No model configured")}</small></div><div class="metric-card"><span>Ontology schema</span><strong>${schema.available ? "Ready" : "Unavailable"}</strong><small>${schema.selection?.counts ? `${formatNumber(schema.selection.counts.entity_types)} types · ${formatNumber(schema.selection.counts.relationship_types)} predicates` : esc(schema.error || "No schema status")}</small></div></div>
    <section class="panel"><div class="panel-head"><div><h2>Optional adapters</h2><p>A configured integration is not reported ready unless its runtime prerequisites are available.</p></div></div><div class="adapter-grid">${adapterCards || "<p>No adapter statuses returned.</p>"}</div></section>
    <section class="panel audit-panel"><div class="panel-head"><div><h2>Persisted review audit</h2><p>${formatNumber(state.audit.items.length)} most recent events</p></div><a class="button button-secondary" href="${apiPath("/api/export/reviews")}">Export review log</a></div><div class="audit-list">${auditRows || `<div class="empty-state"><div><strong>No audit events</strong><p>Review and import actions will appear here.</p></div></div>`}</div></section>
  </div>`;
}

async function showSchemaPrompt(pass) {
  try {
    const compiled = await api(`/api/schema/prompt?pass=${encodeURIComponent(pass)}`);
    $("#drawerTitle").textContent = `${pass} pass prompt`;
    $("#detailDrawer").setAttribute("aria-label", "Compiled schema prompt");
    $("#drawerBody").innerHTML = `<div class="review-meta"><span class="type-pill">${esc(compiled.detail)} detail</span><span class="type-pill">${formatNumber(compiled.characters)} characters</span><span class="type-pill">~${formatNumber(compiled.estimated_tokens)} tokens</span></div>
      ${compiled.notes.map((note) => `<p class="rule-notice">${esc(note)}</p>`).join("")}
      <div class="detail-section"><h3>Compiled from the saved selection</h3><pre class="prompt-preview">${esc(compiled.text)}</pre></div>`;
    state.drawerReturnFocus = document.activeElement;
    $("#detailDrawer").removeAttribute("inert");
    $("#detailDrawer").classList.add("is-open");
    $("#detailDrawer").setAttribute("aria-hidden", "false");
    $("#drawerScrim").hidden = false;
    $("#closeDrawer").focus();
  } catch (error) {
    toast("Prompt unavailable", error.message, true);
  }
}

async function openItem(kind, id) {
  hideGraphEdgeTooltip();
  try {
    const item = await api(`/api/items/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
    $("#drawerTitle").textContent = item.label;
    $("#detailDrawer").setAttribute("aria-label", "Graph item details");
    $("#drawerBody").innerHTML = renderDrawer(item);
    state.drawerReturnFocus = document.activeElement;
    $("#detailDrawer").removeAttribute("inert");
    $("#detailDrawer").classList.add("is-open");
    $("#detailDrawer").setAttribute("aria-hidden", "false");
    $("#drawerScrim").hidden = false;
    $("#closeDrawer").focus();
  } catch (error) {
    toast("Unable to open item", error.message, true);
  }
}

function isAnalyticRule(metadata) {
  if (!metadata) return false;
  return String(metadata.is_analytic_rule || "").toLowerCase() === "true"
    || String(metadata.status || "").toUpperCase() === "GUIDANCE"
    || String(metadata.time_scope || "").toLowerCase() === "analytic rule";
}

function renderProvenance(item) {
  const metadata = item.metadata || {};
  const seen = new Set();
  const rows = PROVENANCE_FIELDS
    .filter(([key]) => metadata[key] !== undefined && metadata[key] !== null && metadata[key] !== "")
    .filter(([, label]) => !seen.has(label) && seen.add(label))
    .map(([key, label]) => `<div class="system-row"><span>${esc(label)}</span><strong>${esc(provenanceValue(metadata[key]))}</strong></div>`)
    .join("");
  const rule = isAnalyticRule(metadata)
    ? `<p class="rule-notice">Analytic rule — this record describes how the data should be modelled, not a fact about the subject.</p>`
    : "";
  if (!rows && !rule) return "";
  return `<div class="detail-section"><h3>Provenance</h3>${rule}${rows ? `<div class="system-list">${rows}</div>` : ""}</div>`;
}

function renderDrawer(item) {
  const relation = item.kind === "edge" ? `<div class="relation-strip"><strong>${esc(item.source.label)}</strong><span>${esc(item.relation)}</span><strong>${esc(item.target.label)}</strong></div>` : "";
  const dot = item.kind === "node" ? `<i class="type-dot ${typeColourClass(item.type)}"></i>` : "";
  const documents = documentLine(item);
  return `<div class="review-meta"><span class="type-pill">${dot}${esc(item.type)}</span>${statusPill(item.status)}${item.host_derived ? `<span class="type-pill">Host provenance</span>` : ""}</div>
    ${relation}
    ${documents ? `<div class="detail-section"><h3>Source document</h3><p>${documents}</p>${(item.source_documents || []).map((name) => `<button class="text-action" data-action="review-document" data-value="${esc(name)}">Review everything from this document →</button>`).join("")}</div>` : ""}
    <div class="detail-section"><h3>Source context</h3><p>${esc(item.evidence)}</p></div>
    ${renderProvenance(item)}
    <div class="detail-section"><h3>Review</h3>${item.reason ? `<p>${esc(item.reason)}</p>` : `<p>No analyst decision has been recorded.</p>`}<div class="review-actions">${item.status !== "accepted" ? `<button class="text-action" data-action="review" data-decision="accepted" data-kind="${item.kind}" data-id="${esc(item.id)}">Accept</button>` : ""}${item.status !== "rejected" ? `<button class="text-action is-danger" data-action="review" data-decision="rejected" data-kind="${item.kind}" data-id="${esc(item.id)}">Reject</button>` : ""}${item.status !== "unreviewed" ? `<button class="text-action skip" data-action="review" data-decision="unreviewed" data-kind="${item.kind}" data-id="${esc(item.id)}">Reset</button>` : ""}</div></div>
    <details class="detail-section"><summary>Technical metadata</summary><pre class="metadata">${esc(JSON.stringify(item.metadata, null, 2))}</pre></details>`;
}

function closeDrawer() {
  $("#detailDrawer").classList.remove("is-open");
  $("#detailDrawer").setAttribute("aria-hidden", "true");
  $("#detailDrawer").setAttribute("inert", "");
  $("#drawerScrim").hidden = true;
  if (state.drawerReturnFocus?.isConnected) state.drawerReturnFocus.focus();
  state.drawerReturnFocus = null;
}

async function performSearch(query) {
  const container = $("#searchResults");
  const request = ++searchRequest;
  if (query.trim().length < 2) {
    container.hidden = true;
    return;
  }
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}&limit=12`);
    if (request !== searchRequest) return;
    container.innerHTML = data.results.length ? data.results.map((item) => `<button class="search-result" data-action="open-search-result" data-kind="${item.kind}" data-id="${esc(item.id)}"><span><strong>${esc(short(item.label, 54))}</strong><small>${esc(item.type || item.relation || item.kind)}</small></span>${statusPill(item.status)}</button>`).join("") : `<div class="empty-state"><p>No graph matches.</p></div>`;
    container.hidden = false;
  } catch (error) {
    if (request !== searchRequest) return;
    container.innerHTML = `<div class="empty-state"><p>${esc(error.message)}</p></div>`;
    container.hidden = false;
  }
}

let searchTimer;
let searchRequest = 0;

async function skipReview() {
  if (state.reviewIndex + 1 < state.queue.items.length) {
    state.reviewIndex += 1;
    renderReview();
    return;
  }
  if (state.queue.has_more) {
    state.reviewOffset += state.queue.limit;
    state.reviewIndex = 0;
    await loadReview();
    renderReview();
    return;
  }
  state.reviewIndex = 0;
  renderReview();
}

function bindGlobalEvents() {
  document.addEventListener("click", (event) => {
    void (async () => {
    const viewTarget = event.target.closest("[data-view-target]");
    if (viewTarget) return setView(viewTarget.dataset.viewTarget);
    const nav = event.target.closest("[data-view]");
    if (nav) return setView(nav.dataset.view);
    const action = event.target.closest("[data-action]");
    if (!action) {
      if (!event.target.closest(".global-search-wrap")) $("#searchResults").hidden = true;
      return;
    }
    const name = action.dataset.action;
    if (name === "retry") return setView(state.view, { replace: true });
    if (name === "queue-select") { state.reviewIndex = Number(action.dataset.index); return renderReview(); }
    if (name === "apply-review-filters") {
      // The document scope survives a filter change; it is cleared by its own
      // chip, not by touching an unrelated control.
      state.reviewFilters = { kind: $("#reviewKind").value, status: $("#reviewStatus").value, query: $("#reviewSearch").value.trim(), document: state.reviewFilters.document };
      state.reviewIndex = 0;
      state.reviewOffset = 0;
      await loadReview();
      return renderReview();
    }
    if (name === "review-type") {
      state.reviewFilters = { kind: "node", status: "accepted,rejected,unreviewed", query: action.dataset.value, document: "" };
      state.reviewIndex = 0;
      state.reviewOffset = 0;
      return setView("review");
    }
    if (name === "review-document") {
      state.reviewFilters = { kind: "all", status: "unreviewed", query: "", document: action.dataset.value };
      state.reviewIndex = 0;
      state.reviewOffset = 0;
      return setView("review");
    }
    if (name === "clear-document-filter") {
      state.reviewFilters = { ...state.reviewFilters, document: "" };
      state.reviewIndex = 0;
      state.reviewOffset = 0;
      await loadReview();
      return renderReview();
    }
    if (name === "review-page") {
      state.reviewOffset = Math.max(0, Number(action.dataset.offset) || 0);
      state.reviewIndex = 0;
      await loadReview();
      return renderReview();
    }
    if (name === "review-skip") return skipReview();
    if (name === "review") {
      const decision = action.dataset.decision;
      if (decision === "accepted" || decision === "unreviewed") return applyReview(action.dataset.kind, action.dataset.id, decision, decision === "accepted" ? "Accepted during analyst review." : "Returned for further review.");
      return promptDecision(action.dataset.kind, action.dataset.id, decision);
    }
    if (name === "open-item") return openItem(action.dataset.kind, action.dataset.id);
    if (name === "open-search-result") { $("#searchResults").hidden = true; return openItem(action.dataset.kind, action.dataset.id); }
    if (name === "load-graph") { await setView("explore"); return loadGraph(action.dataset.id); }
    if (name === "select-node") return selectGraphNode(action.dataset.id);
    if (name === "graph-reset") return loadGraph("");
    if (name === "graph-zoom-in") { setGraphZoom(graphZoom * GRAPH_ZOOM_STEP); return; }
    if (name === "graph-zoom-out") { setGraphZoom(graphZoom / GRAPH_ZOOM_STEP); return; }
    if (name === "graph-zoom-reset") { setGraphZoom(1); return; }
    if (name === "ask-suggestion") { $("#askInput").value = action.dataset.question; return askQuestion(action.dataset.question); }
    if (name === "refresh-jobs") { await loadJobs(); return renderSources(); }
    if (name === "corpus-select-all") {
      state.corpus.items.forEach((item) => state.corpus.selected.add(item.filename));
      return renderSources();
    }
    if (name === "corpus-select-none") {
      state.corpus.selected.clear();
      return renderSources();
    }
    if (name === "open-projects") {
      renderProjectDialog();
      $("#projectDialog").showModal();
      await refreshProjects();
      renderProjectDialog();
      return;
    }
    if (name === "close-project-dialog") { $("#projectDialog").close(); return; }
    if (name === "activate-project") {
      $("#projectDialog").close();
      try {
        await switchProject(action.dataset.id);
      } catch (error) {
        toast("Could not switch project", error.message, true);
      }
      return;
    }
    if (name === "goto-schema") return setView("schema");
    if (name === "schema-prompt") return showSchemaPrompt(action.dataset.pass);
    if (name === "schema-reset") {
      state.schema.preview = await api("/api/schema/selection/reset", { method: "POST", body: "{}" });
      state.schema.saved = null;
      state.schema.suggestion = null;
      state.schema.missionSuggestion = null;
      await loadSchema(true);
      toast("Selection reset", "The schema's own runtime defaults are restored.");
      return renderSchema();
    }
    if (name === "schema-all") {
      availableEntityTypes().forEach((row) => state.schema.draft.entities.add(row.id));
      schedulePreview();
      return renderSchema();
    }
    if (name === "schema-none") {
      state.schema.draft.entities.clear();
      schedulePreview();
      return renderSchema();
    }
    if (name === "priority-queue") {
      state.priorities.activeQueue = action.dataset.queue;
      state.priorities.filter = "";
      const first = state.priorities.bootstrap?.queues?.[action.dataset.queue]?.[0];
      if (first) return selectPriorityItem(first.kind || "node", first.id);
      state.priorities.selected = null;
      return renderPriorities();
    }
    if (name === "priority-select") return selectPriorityItem(action.dataset.kind || "node", action.dataset.id);
    if (name === "priority-refresh") {
      await loadPriorities(true);
      return renderPriorities();
    }
    if (name === "priority-open-graph") {
      await setView("explore");
      return loadGraph(action.dataset.id);
    }
    if (name === "priority-open-target") {
      state.target.selectedId = action.dataset.id;
      state.target.preview = null;
      return setView("target");
    }
    if (name === "explore-open-target") {
      const id = action.dataset.id;
      const label = state.graphFocusItem?.label || id;
      // Scoping the candidate search to this exact id (rather than just
      // setting selectedId) guarantees it is actually in the list the
      // dropdown loads from - otherwise an id outside the default top-100
      // candidates would silently be swapped for a different entity.
      state.target.query = id;
      state.target.selectedId = id;
      state.target.candidates = [];
      state.target.preview = null;
      state.target.job = null;
      await setView("target");
      if (state.target.selectedId !== id) {
        // The scoped search can still return other, text-similar entities
        // even when this exact one fails target-development screening (a
        // bare location, personnel record, rejected item...); the shared
        // candidate-resolution fallback then silently keeps the closest
        // remaining match, which is right for a free-text search but here
        // would land the analyst on an unrelated entity with no explanation.
        state.target.query = "";
        state.target.selectedId = "";
        state.target.candidates = [];
        await loadTargetDevelopment(true);
        renderTargetDevelopment();
        toast(
          "Not an eligible target-development candidate",
          `"${label}" did not pass target-development screening (for example, a bare location, personnel record, or rejected item). Choose a different entity, or search directly on this page.`,
          true,
        );
      }
      return;
    }
    if (name === "method-info") return openMethodInfo(action.dataset.methodId);
    if (name === "audit-refresh") {
      await loadAuditStatus();
      toast("Status refreshed", "Runtime and audit data were reloaded from the Python backend.");
      return renderAuditStatus();
    }
    })().catch((error) => toast("Action could not be completed", error.message, true));
  });

  document.addEventListener(
    "wheel",
    (event) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (!event.target.closest?.("#graphCanvas")) return;
      event.preventDefault();
      setGraphZoom(graphZoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1), event);
    },
    { passive: false },
  );
  document.addEventListener("dblclick", (event) => {
    const node = event.target.closest?.(".graph-node");
    if (!node) return;
    event.preventDefault();
    loadGraph(node.dataset.id).catch((error) => toast("Could not centre graph", error.message, true));
  });
  document.addEventListener("mouseover", (event) => {
    const edgeGroup = event.target.closest?.(".graph-edge-group");
    if (edgeGroup) showGraphEdgeTooltip(edgeGroup, event.clientX, event.clientY);
  });
  document.addEventListener("mousemove", (event) => {
    if (event.target.closest?.(".graph-edge-group")) positionGraphTooltip(event.clientX, event.clientY);
  });
  document.addEventListener("mouseout", (event) => {
    if (event.target.closest?.(".graph-edge-group")) hideGraphEdgeTooltip();
  });
  document.addEventListener(
    "focusin",
    (event) => {
      const edgeGroup = event.target.closest?.(".graph-edge-group");
      if (!edgeGroup) return;
      const rect = edgeGroup.getBoundingClientRect();
      showGraphEdgeTooltip(edgeGroup, rect.left + rect.width / 2, rect.top + rect.height / 2);
    },
    true,
  );
  document.addEventListener(
    "focusout",
    (event) => {
      if (event.target.closest?.(".graph-edge-group")) hideGraphEdgeTooltip();
    },
    true,
  );
  $("#closeDrawer").addEventListener("click", closeDrawer);
  $("#drawerScrim").addEventListener("click", closeDrawer);
  $("#menuButton").addEventListener("click", () => {
    const open = $("#sidebar").classList.toggle("is-open");
    $("#menuButton").setAttribute("aria-expanded", String(open));
  });
  $("#globalSearch").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => performSearch(event.target.value), 180);
  });
  $("#decisionForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const choice = event.submitter?.value;
    if (choice !== "confirm") {
      $("#decisionDialog").close();
      return;
    }
    const reason = $("#decisionReason").value.trim();
    if (state.pendingDecision?.decision === "rejected" && !reason) {
      toast("Reason required", "Add a short reason before rejecting the candidate.", true);
      return;
    }
    const pending = state.pendingDecision;
    $("#decisionDialog").close();
    if (pending) await applyReview(pending.kind, pending.id, pending.decision, reason);
    state.pendingDecision = null;
  });
  $("#projectCreateForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#projectNameInput");
    const name = input.value.trim();
    if (!name) return toast("Name required", "Give the new project a name.", true);
    try {
      $("#projectDialog").close();
      input.value = "";
      await createProject(name);
    } catch (error) {
      toast("Could not create project", error.message, true);
    }
  });
  window.addEventListener("popstate", () => setView(location.hash.slice(1) || "overview", { replace: true }));
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#globalSearch").focus();
    }
    if (event.key === "Escape") closeDrawer();
    const graphNode = event.target.closest?.(".graph-node[data-action='select-node']");
    if (graphNode && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      selectGraphNode(graphNode.dataset.id).catch((error) => toast("Node could not be opened", error.message, true));
      return;
    }
    const graphEdgeGroup = event.target.closest?.(".graph-edge-group[data-action='open-item']");
    if (graphEdgeGroup && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      openItem("edge", graphEdgeGroup.dataset.id);
      return;
    }
    if (state.view === "review" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) && !$("#decisionDialog").open) {
      const item = state.queue.items[state.reviewIndex];
      if (!item) return;
      if (event.key.toLowerCase() === "a") applyReview(item.kind, item.id, "accepted", "Accepted during analyst review.");
      if (event.key.toLowerCase() === "r") promptDecision(item.kind, item.id, "rejected");
      if (event.key.toLowerCase() === "s") skipReview().catch((error) => toast("Queue could not advance", error.message, true));
    }
    if (state.view === "explore" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      if (event.key === "+" || event.key === "=") { event.preventDefault(); setGraphZoom(graphZoom * GRAPH_ZOOM_STEP); }
      if (event.key === "-" || event.key === "_") { event.preventDefault(); setGraphZoom(graphZoom / GRAPH_ZOOM_STEP); }
      if (event.key === "0") { event.preventDefault(); setGraphZoom(1); }
    }
  });
}

async function init() {
  bindGlobalEvents();
  try {
    [state.health] = await Promise.all([api("/api/health"), refreshSummary(), refreshProjects()]);
    const service = $("#serviceState");
    service.className = "service-state is-ready";
    service.innerHTML = "<span></span>Local graph ready";
    const initial = location.hash.slice(1) || "overview";
    await setView(initial, { replace: true });
  } catch (error) {
    const service = $("#serviceState");
    service.className = "service-state is-error";
    service.innerHTML = "<span></span>Connection failed";
    renderFailure(error);
  }
}

init();
