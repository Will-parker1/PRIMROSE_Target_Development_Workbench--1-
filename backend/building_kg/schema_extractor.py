

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx  # noqa: E402

from kg_backend.schema import (  # noqa: E402
    ENTITY_PASS,
    RELATIONSHIP_PASS,
    Selection,
    TargetingSchema,
    UNRESOLVED,
    load_schema,
)
from kg_backend.schema_prompt import (  # noqa: E402
    CompiledPrompt,
    PromptCompiler,
    ShardPlan,
    examples_for,
)

# Node "type" values the projection introduces alongside the schema's own entity
# labels. The review UI colours by type, so these stay short and stable.
MENTION_TYPE_FALLBACK = "unresolved mention"
ASSERTION_TYPE = "source assertion"
PREDICTION_TYPE = "model prediction"
DOCUMENT_TYPE = "source document"

# Host-created provenance records. The model never emits these: the host knows
# what it read, and the schema types that knowledge properly.
DOCUMENT_RECORD_TYPE = "core:DocumentVersion"
MENTION_RECORD_TYPE = "core:Mention"
DOCUMENT_MENTION_RELATION = "core_rel:hasMention"
ASSERTION_SOURCE_RELATION = "core_rel:hasSourceInstance"

# Characters of surrounding source kept with each record, so the review drawer
# shows the sentence a span came from rather than the span alone.
EXCERPT_WINDOW = 180

_PAGE_MARKER = re.compile(r"\[\[page (\d+)\]\]")

# Structural edges of a reified assertion. They exist to hold the assertion
# together; the analyst's decision belongs on the assertion node.
STRUCTURAL_RELATIONS = ("core_rel:assertionHasSubject", "core_rel:assertionHasObject")

TYPE_COLOURS = {
    ASSERTION_TYPE: "#4b9bff",
    PREDICTION_TYPE: "#8d6e63",
    MENTION_TYPE_FALLBACK: "#888888",
    DOCUMENT_TYPE: "#a1887f",
}
_PALETTE = (
    "#ff4b4b", "#ff7043", "#ffb84b", "#d4a017", "#4b9bff", "#5c6bc0",
    "#4bff9b", "#26a69a", "#b84bff", "#78909c", "#29b6f6", "#ec407a",
    "#66bb6a", "#7e57c2", "#ab47bc", "#c0ca33", "#a1887f", "#ffa726",
)


def _colour_for(type_id: str) -> str:
    if type_id in TYPE_COLOURS:
        return TYPE_COLOURS[type_id]
    digest = hashlib.sha256(type_id.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def _as_text(value: Any) -> str:
    """Coerce a model-extracted field to a string.

    Extraction fields are contractually strings, but small local models
    occasionally emit a list where a single string was expected; join those
    rather than letting them crash _normalise().
    """
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value if v not in (None, ""))
    return "" if value is None else str(value)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", _as_text(value)).strip().casefold()


def _digest(*parts: Any) -> str:
    material = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


@dataclass
class SourceDocument:
    """The document a run read, as an addressable provenance record.

    Identity is the content hash, not the filename: re-uploading the same bytes
    under a different name resolves to the same document node, and editing a
    document makes a genuinely new one. That is what `core:DocumentVersion`
    means in the schema — an immutable manifestation used for span provenance.
    """

    filename: str
    media_type: str
    sha256: str
    byte_size: int
    character_count: int
    page_count: int = 0
    ingested_at: str = ""
    # (character offset, page number) for each page marker, in order. Lets a
    # span's offset be resolved back to the page an analyst would turn to.
    page_offsets: list[tuple[int, int]] = field(default_factory=list)

    @property
    def node_id(self) -> str:
        return "document-" + self.sha256[:20]

    def page_for(self, offset: int | None) -> int:
        if offset is None or not self.page_offsets:
            return 0
        page = 0
        for marker_offset, number in self.page_offsets:
            if marker_offset > offset:
                break
            page = number
        return page

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.node_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "character_count": self.character_count,
            "page_count": self.page_count,
            "ingested_at": self.ingested_at,
        }


@dataclass
class PassReport:
    pass_id: str
    raw: int = 0
    accepted: int = 0
    rejected: int = 0
    reasons: Counter = field(default_factory=Counter)
    repairs: Counter = field(default_factory=Counter)
    skipped: str = ""
    # How the pass was actually compiled and run: how many budget-bounded
    # `lx.extract()` prompts it took (schema_prompt.PromptCompiler.plan), and
    # how many selected ids were dropped before any of them ran because their
    # surface forms had no lexical trace in the document.
    shard_count: int = 0
    skipped_for_relevance: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass": self.pass_id,
            "raw_extractions": self.raw,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "rejection_reasons": dict(self.reasons.most_common(12)),
            "identifier_repairs": dict(self.repairs.most_common(12)),
            "skipped": self.skipped,
            "shard_count": self.shard_count,
            "skipped_for_relevance": self.skipped_for_relevance,
        }


@dataclass
class ExtractionReport:
    selection: dict[str, Any]
    passes: list[PassReport] = field(default_factory=list)
    prompts: dict[str, Any] = field(default_factory=dict)
    mentions: int = 0
    assertions: int = 0
    document: str = ""
    source_document: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "source_document": dict(self.source_document),
            "selection": self.selection,
            "prompts": self.prompts,
            "passes": [report.as_dict() for report in self.passes],
            "mentions": self.mentions,
            "assertions": self.assertions,
        }


class _MergedAnnotation:
    """Extractions from every shard of one pass, in the shape `_collect`
    expects (it only reads `.extractions`). Every shard's `lx.extract()` call
    runs over the same full document text, so `char_interval` values from
    different shards stay absolute and directly comparable — nothing here
    needs offset adjustment."""

    __slots__ = ("extractions",)

    def __init__(self, extractions: list[Any]) -> None:
        self.extractions = extractions


class SchemaGuidedExtractor:
    def __init__(
        self,
        *,
        schema: TargetingSchema | None = None,
        selection_request: dict[str, Any] | None = None,
        model_id: str = "google/gemma-4-e4b",
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str = "lm-studio",
        max_char_buffer: int = 1500,
        extraction_passes: int = 1,
        max_workers: int = 4,
        temperature: float = 0.0,
        page_markers: bool = True,
        prompt_budget: int | None = None,
        skip_zero_relevance_shards: bool = False,
        verbose: bool = True,
    ):
        self.schema = schema or load_schema()
        self.selection = self.schema.resolve(selection_request or {})
        compiler = (
            PromptCompiler(self.schema, prompt_budget)
            if prompt_budget
            else PromptCompiler(self.schema)
        )
        self.prompts = compiler.compile(self.selection)
        self._compiler = compiler
        # Off by default: PromptCompiler.plan's relevance score is a plain
        # substring match against the schema's curated surface forms, which
        # real prose paraphrases far more often than it repeats verbatim.
        # Leaving every selected id in the run (packed purely by budget) costs
        # more model calls for a broad selection but keeps the recall the
        # single-prompt design always had. Only flip this on for callers who
        # have deliberately chosen to trade recall for fewer calls.
        self.skip_zero_relevance_shards = skip_zero_relevance_shards
        self.model_id = model_id
        self.base_url = base_url
        self.api_key = api_key
        self.max_char_buffer = max_char_buffer
        # runtime_contract: extraction_passes is a recall repeat within one pass,
        # never a way to run the entity and relationship tasks together.
        self.extraction_passes = max(1, extraction_passes)
        self.max_workers = max_workers
        self.temperature = temperature
        self.page_markers = page_markers
        self.verbose = verbose
        self._model_config = None

    def _log(self, *args: Any) -> None:
        if self.verbose:
            print(*args)

    # ------------------------------------------------------------------ input

    def read_document(
        self, path: str | Path, filename: str = ""
    ) -> tuple[str, SourceDocument]:
        """Read a document and describe it.

        `filename` overrides the name on disk. Uploads are stored under a
        job-prefixed name, and the analyst should see the name they uploaded.
        """
        path = Path(path)
        suffix = path.suffix.lower()
        payload = path.read_bytes()
        document = SourceDocument(
            filename=filename or path.name,
            media_type="pdf" if suffix == ".pdf" else "text",
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            character_count=0,
            ingested_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )

        if suffix == ".pdf":
            import fitz  # imported lazily: text documents do not need pymupdf

            parts = []
            with fitz.open(path) as handle:
                document.page_count = handle.page_count
                for index, page in enumerate(handle, start=1):
                    text = page.get_text("text").strip()
                    if not text:
                        continue
                    parts.append(f"[[page {index}]]\n{text}" if self.page_markers else text)
            content = "\n\n".join(parts)
        elif suffix in {".txt", ".text", ".md"}:
            content = path.read_text(encoding="utf-8", errors="replace")
        else:
            raise ValueError(f"unsupported file type {path.suffix!r}: {path}")

        document.character_count = len(content)
        # Marker offsets are read back from the assembled text rather than
        # accumulated while building it, so they stay correct whatever the
        # joining does.
        document.page_offsets = [
            (match.start(), int(match.group(1))) for match in _PAGE_MARKER.finditer(content)
        ]
        return content, document

    # ------------------------------------------------------------ langextract

    def _config(self):
        if self._model_config is None:
            from langextract import factory

            # LM Studio's OpenAI-compatible endpoint does not expose Gemini's
            # responseSchema, so output is fenced JSON parsed by LangExtract.
            self._model_config = factory.ModelConfig(
                model_id=self.model_id,
                provider="OpenAILanguageModel",
                provider_kwargs={
                    "base_url": self.base_url,
                    "api_key": self.api_key,
                    "response_format": {"type": "text"},
                },
            )
        return self._model_config

    def _examples(self, pass_id: str, selection: Selection | None = None) -> list:
        import langextract as lx

        built = []
        for example in examples_for(self.schema, selection or self.selection, pass_id):
            extractions = [
                lx.data.Extraction(
                    extraction_class=item["extraction_class"],
                    extraction_text=item["extraction_text"],
                    attributes=dict(item.get("attributes", {})),
                )
                for item in example.get("extractions", [])
            ]
            if not extractions:
                # negative_example_rule: zero-extraction ExampleData was not
                # stable in LangExtract 1.6.0. Those cases are prompt guardrails,
                # compiled into the prompt text instead.
                continue
            built.append(lx.data.ExampleData(text=example["text"], extractions=extractions))
        return built

    def _examples_for_shard(self, pass_id: str, shard: CompiledPrompt) -> list:
        """Few-shot examples scoped to what this shard's prompt actually
        lists. Falls back to the whole pass's examples when the shard's own
        ids match none — only 18 few-shot examples exist across the whole
        schema today, so most predicates already rely on generalising from
        examples that name other ids even in the unsharded prompt; failing a
        shard over this would be a regression, not a safety property."""
        if not shard.type_ids:
            return self._examples(pass_id)
        if pass_id == ENTITY_PASS:
            shard_selection = dataclasses.replace(self.selection, entity_type_ids=shard.type_ids)
        else:
            shard_selection = dataclasses.replace(self.selection, relationship_type_ids=shard.type_ids)
        shard_examples = self._examples(pass_id, shard_selection)
        return shard_examples if shard_examples else self._examples(pass_id)

    def _run_pass(self, text: str, pass_id: str) -> tuple[_MergedAnnotation, ShardPlan]:
        import langextract as lx

        plan = self._compiler.plan(
            self.selection,
            pass_id,
            text,
            skip_zero_relevance=self.skip_zero_relevance_shards,
        )
        if not plan.shards:
            # Every selected id was skipped for relevance: nothing in `text`
            # has any lexical trace of a selected label/surface form. This is
            # a legitimate "found nothing" outcome for this document, not a
            # misconfigured selection (process_text already refuses to call
            # `_run_pass` when the selection itself is empty) — report it via
            # `plan.skipped_type_ids` rather than failing the run.
            self._log(
                f"  {pass_id}: 0 shards — every selected type/predicate was skipped "
                f"as absent from the text"
            )
            return _MergedAnnotation([]), plan
        self._log(
            f"  {pass_id}: {len(plan.shards)} shard(s)"
            + (
                f", {len(plan.skipped_type_ids)} skipped as absent from the text"
                if plan.skipped_type_ids
                else ""
            )
        )
        all_extractions: list[Any] = []
        for index, shard in enumerate(plan.shards, start=1):
            examples = self._examples_for_shard(pass_id, shard)
            if not examples:
                raise RuntimeError(
                    f"the {pass_id} pass has no usable few-shot example inside this selection; "
                    f"widen the module or type selection"
                )
            self._log(
                f"    shard {index}/{len(plan.shards)}: {len(shard.type_ids)} types, "
                f"{shard.characters:,} chars ({shard.detail})"
            )
            annotated = lx.extract(
                text_or_documents=text,
                prompt_description=shard.text,
                examples=examples,
                config=self._config(),
                use_schema_constraints=False,
                fence_output=True,
                max_char_buffer=self.max_char_buffer,
                extraction_passes=self.extraction_passes,
                max_workers=self.max_workers,
                temperature=self.temperature,
                debug=False,
            )
            all_extractions.extend(getattr(annotated, "extractions", []) or [])
        return _MergedAnnotation(all_extractions), plan

    # ------------------------------------------------------------- validation

    @staticmethod
    def _grounded(extraction, source: str) -> bool:
        """host_semantic_validation step 1: the recorded span must slice back to
        exactly the extraction text. LangExtract aligns against the source, so a
        mismatch means the model paraphrased and the alignment is fuzzy."""
        interval = getattr(extraction, "char_interval", None)
        start = getattr(interval, "start_pos", None)
        end = getattr(interval, "end_pos", None)
        if start is None or end is None:
            # Unaligned output cannot be grounded; fall back to a containment
            # check so a correct verbatim span is not thrown away.
            return extraction.extraction_text in source
        return source[start:end] == extraction.extraction_text

    def _collect(self, annotated, pass_id: str, source: str) -> tuple[list[dict[str, Any]], PassReport]:
        report = PassReport(pass_id)
        records: list[dict[str, Any]] = []
        for extraction in getattr(annotated, "extractions", []) or []:
            report.raw += 1
            record = {
                "extraction_class": extraction.extraction_class,
                "extraction_text": extraction.extraction_text,
                "attributes": dict(extraction.attributes or {}),
            }
            if not self._grounded(extraction, source):
                report.rejected += 1
                report.reasons["span is not an exact substring of the source"] += 1
                continue
            for note in self.schema.repair_extraction(record, self.selection, pass_id):
                report.repairs[note] += 1
            result = self.schema.validate_extraction(record, self.selection, pass_id)
            if not result.ok:
                report.rejected += 1
                for error in result.errors:
                    report.reasons[error] += 1
                continue
            text_error = self._verbatim_error(result.record["attributes"], source, pass_id)
            if text_error:
                report.rejected += 1
                report.reasons[text_error] += 1
                continue
            interval = getattr(extraction, "char_interval", None)
            result.record["char_start"] = getattr(interval, "start_pos", None)
            result.record["char_end"] = getattr(interval, "end_pos", None)
            records.append(result.record)
            report.accepted += 1
        return records, report

    # Attributes the contract requires to be copied verbatim from the passage.
    _VERBATIM_FIELDS = (
        "subject_text", "object_text", "evidence_text", "finding_text",
        "qualitative_value_text", "quantitative_value_text", "unit_text",
        "qualifier_text", "baseline_or_scenario_text", "valid_time_text",
        "observation_time_text", "assessor_or_authority_text", "uncertainty_text",
        "analytical_confidence_text",
    )

    def _verbatim_error(self, attributes: dict[str, Any], source: str, pass_id: str) -> str:
        """host_semantic_validation step 6: every quoted field must occur in the
        source. This is the check that stops a small model inventing a date or an
        authority to make a record look complete."""
        if pass_id != RELATIONSHIP_PASS:
            return ""
        for name in self._VERBATIM_FIELDS:
            value = str(attributes.get(name, "") or "").strip()
            if value and value not in source:
                return f"{name} is not verbatim in the source"
        return ""

    # ------------------------------------------------------------- projection

    def _mention_id(self, text: str, type_id: str) -> str:
        return "mention-" + _digest(_normalise(text), type_id)

    @staticmethod
    def _excerpt(source: str, start: int | None, end: int | None) -> str:
        """The span in its surrounding sentence, for the review drawer.

        A mention on its own tells an analyst nothing about whether the type is
        right. The window is widened to whitespace so words are not cut in half.
        """
        if start is None or end is None or not source:
            return ""
        left = max(0, start - EXCERPT_WINDOW)
        right = min(len(source), end + EXCERPT_WINDOW)
        excerpt = source[left:right]
        if left > 0:
            excerpt = excerpt.split(" ", 1)[-1]
        if right < len(source):
            excerpt = excerpt.rsplit(" ", 1)[0]
        excerpt = " ".join(excerpt.split())
        prefix = "…" if left > 0 else ""
        suffix = "…" if right < len(source) else ""
        return f"{prefix}{excerpt}{suffix}"

    def _provenance(
        self,
        document: SourceDocument | None,
        source_text: str,
        start: int | None,
        end: int | None,
        anchor: str = "",
    ) -> dict[str, Any]:
        """The provenance every projected record carries.

        `source_document` is the field name the rest of the application already
        reads, so a schema-guided record shows its document in exactly the same
        place as a heuristic one.

        When LangExtract returns no character interval, the span is located by
        searching for its own verbatim text: validation has already established
        that the text occurs in the source, so a record never loses its page and
        surrounding context merely because alignment metadata was missing.
        """
        if document is None:
            return {}
        provenance: dict[str, Any] = {
            "source_document": document.filename,
            "document_id": document.node_id,
            "document_sha256": document.sha256,
        }
        if start is None and anchor and source_text:
            found = source_text.find(anchor)
            if found >= 0:
                start, end = found, found + len(anchor)
        page = document.page_for(start)
        if page:
            provenance["source_page"] = page
        excerpt = self._excerpt(source_text, start, end)
        if excerpt:
            provenance["source_excerpt"] = excerpt
        return provenance

    def _add_mention(
        self,
        graph: nx.MultiDiGraph,
        text: str,
        type_id: str,
        source: str,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        text = _as_text(text)
        attributes = attributes or {}
        node_id = self._mention_id(text, type_id)
        entity_type = self.schema.entities.get(type_id, {})
        label = entity_type.get("label", MENTION_TYPE_FALLBACK if type_id == UNRESOLVED else type_id)
        # A character offset of 0 is a real offset, so emptiness is tested
        # against None and "" rather than falsiness.
        attributes = {key: value for key, value in attributes.items() if value not in (None, "")}
        if node_id in graph:
            node = graph.nodes[node_id]
            node["sources"] = list(dict.fromkeys([*node.get("sources", []), source]))
            node["mention_count"] = node.get("mention_count", 1) + 1
            for key, value in attributes.items():
                node.setdefault(key, value)
            return node_id
        graph.add_node(
            node_id,
            label=text,
            title=f"{label}: {text}",
            # `type` drives the existing review and explore UI; `type_id` is the
            # canonical schema identity the projection is actually asserting.
            type=label,
            type_id=type_id,
            # The record is a Mention; its type_id is the candidate type that
            # mention proposes. graph_projection_contract keeps those apart.
            record_type_id=MENTION_RECORD_TYPE,
            layer=entity_type.get("layer", ""),
            schema_module_ids=list(entity_type.get("module_ids", [])),
            record_kind="MENTION",
            color=_colour_for(type_id),
            size=26,
            sources=[source] if source else [],
            mention_count=1,
            **attributes,
        )
        return node_id

    def _add_document(
        self, graph: nx.MultiDiGraph, document: SourceDocument
    ) -> str:
        """The document itself, as a node the graph can be queried through."""
        graph.add_node(
            document.node_id,
            label=document.filename,
            title=f"Source document: {document.filename}",
            type=DOCUMENT_TYPE,
            type_id=DOCUMENT_RECORD_TYPE,
            record_type_id=DOCUMENT_RECORD_TYPE,
            record_kind="SOURCE_DOCUMENT",
            color=_colour_for(DOCUMENT_TYPE),
            size=34,
            sources=[document.filename],
            source_document=document.filename,
            document_id=document.node_id,
            document_sha256=document.sha256,
            media_type=document.media_type,
            byte_size=document.byte_size,
            character_count=document.character_count,
            page_count=document.page_count,
            ingested_at=document.ingested_at,
            # Host-created provenance, not a model extraction. Recorded so a
            # reviewer can tell the two apart at a glance.
            host_derived=True,
        )
        return document.node_id

    def _project(
        self,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        source: str,
        document: SourceDocument | None = None,
        source_text: str = "",
    ) -> nx.MultiDiGraph:
        """graph_projection_contract, both passes.

        Entity pass creates grounded mentions only: no merging, no enduring
        entity. Relationship pass creates a reified SourceAssertion (Prediction
        when modality is MODEL_PREDICTED) linked to its subject and object
        mentions. The predicate, polarity and modality field mappings are carried
        as canonical attributes on the assertion node rather than as further
        nodes, so one analyst decision covers one claim.

        The document is projected too, as a `core:DocumentVersion` joined to its
        mentions and assertions. Provenance is also denormalised onto every
        record, so a reviewer looking at one candidate sees its document, page
        and surrounding text without traversing the graph first.
        """
        graph = nx.MultiDiGraph()
        polarity_map = self.schema.graph_projection_contract.get("polarity_value_mapping", {})
        modality_map = self.schema.graph_projection_contract.get("modality_value_mapping", {})
        field_map = self.schema.graph_projection_contract.get("relationship_field_mapping", {})
        document_id = self._add_document(graph, document) if document else ""

        for record in entities:
            attributes = record["attributes"]
            self._add_mention(
                graph,
                record["extraction_text"],
                attributes.get("type_id", UNRESOLVED),
                source,
                {
                    "canonical_name": attributes.get("canonical_name", ""),
                    "identifier": attributes.get("identifier", ""),
                    "mention_role": attributes.get("mention_role", ""),
                    "review_action": attributes.get("review_action", ""),
                    "char_start": record.get("char_start"),
                    "char_end": record.get("char_end"),
                    **self._provenance(
                        document,
                        source_text,
                        record.get("char_start"),
                        record.get("char_end"),
                        record["extraction_text"],
                    ),
                },
            )

        for record in relationships:
            attributes = record["attributes"]
            predicate_id = attributes["predicate_id"]
            relationship = self.schema.relationships[predicate_id]
            endpoint_provenance = self._provenance(
                document,
                source_text,
                record.get("char_start"),
                record.get("char_end"),
                record["extraction_text"],
            )
            subject_id = self._add_mention(
                graph,
                attributes["subject_text"],
                attributes["subject_type_id"],
                source,
                dict(endpoint_provenance),
            )
            object_id = self._add_mention(
                graph,
                attributes["object_text"],
                attributes["object_type_id"],
                source,
                dict(endpoint_provenance),
            )
            modality = attributes.get("modality", "")
            is_prediction = modality == "MODEL_PREDICTED"
            assertion_id = "assertion-" + _digest(
                source,
                attributes["subject_text"],
                predicate_id,
                attributes["object_text"],
                record["extraction_text"],
                attributes.get("valid_time_text", ""),
            )
            label = (
                f"{attributes['subject_text']} —[{relationship.get('label', predicate_id)}]→ "
                f"{attributes['object_text']}"
            )
            payload = {
                key: value
                for key, value in attributes.items()
                if value and key not in {"subject_text", "object_text"}
            }
            graph.add_node(
                assertion_id,
                label=label,
                title=record["extraction_text"],
                type=PREDICTION_TYPE if is_prediction else ASSERTION_TYPE,
                type_id="core:Prediction" if is_prediction else "core:SourceAssertion",
                record_kind="PREDICTION" if is_prediction else "SOURCE_ASSERTION",
                color=_colour_for(PREDICTION_TYPE if is_prediction else ASSERTION_TYPE),
                size=20,
                sources=[source] if source else [],
                evidence_span=record["extraction_text"],
                char_start=record.get("char_start"),
                char_end=record.get("char_end"),
                subject_mention=subject_id,
                object_mention=object_id,
                predicate_label=relationship.get("label", predicate_id),
                model_action=relationship.get("model_action", ""),
                temporal_requirement=relationship.get("temporal_requirement", ""),
                # graph_projection_contract.relationship_field_mapping, carried so
                # a downstream IES projection can read the intended relation ids.
                projection_field_map=json.dumps(field_map, ensure_ascii=False),
                polarity_id=polarity_map.get(attributes.get("polarity", ""), ""),
                modality_id=modality_map.get(modality, ""),
                schema_module_ids=list(relationship.get("module_ids", [])),
                **{
                    key: value
                    for key, value in endpoint_provenance.items()
                    if key not in payload
                },
                **payload,
            )
            for relation, endpoint in (
                (STRUCTURAL_RELATIONS[0], subject_id),
                (STRUCTURAL_RELATIONS[1], object_id),
            ):
                graph.add_edge(
                    assertion_id,
                    endpoint,
                    label=relation.split(":")[-1],
                    relation=relation,
                    predicate_id=relation,
                    structural=True,
                    # Scaffolding that holds the reified assertion together, not
                    # a claim of its own. The analyst's decision belongs on the
                    # assertion node; these follow it.
                    host_derived=True,
                    title=record["extraction_text"],
                    assertion_id=assertion_id,
                    source_document=document.filename if document else source,
                    document_id=document_id,
                )

            if document_id:
                # core_rel:hasSourceInstance — core:Assertion -> core:DocumentVersion.
                graph.add_edge(
                    assertion_id,
                    document_id,
                    label=ASSERTION_SOURCE_RELATION.split(":")[-1],
                    relation=ASSERTION_SOURCE_RELATION,
                    predicate_id=ASSERTION_SOURCE_RELATION,
                    structural=True,
                    host_derived=True,
                    title=f"Asserted from {document.filename}",
                    assertion_id=assertion_id,
                    source_document=document.filename,
                    document_id=document_id,
                )

        if document_id:
            # core_rel:hasMention — core:DocumentVersion (an InformationArtifact)
            # -> core:Mention. Added last so it covers mentions created by either
            # pass, and skipped for the document node itself.
            for node_id, data in list(graph.nodes(data=True)):
                if data.get("record_kind") != "MENTION":
                    continue
                graph.add_edge(
                    document_id,
                    node_id,
                    label=DOCUMENT_MENTION_RELATION.split(":")[-1],
                    relation=DOCUMENT_MENTION_RELATION,
                    predicate_id=DOCUMENT_MENTION_RELATION,
                    structural=True,
                    host_derived=True,
                    title=f"{document.filename} mentions {data.get('label', '')}",
                    source_document=document.filename,
                    document_id=document_id,
                )
        return graph

    # ------------------------------------------------------------------- runs

    def process_text(
        self,
        text: str,
        source: str = "",
        document: SourceDocument | None = None,
    ) -> tuple[nx.MultiDiGraph, ExtractionReport]:
        if document is None and text:
            # Text handed in directly still deserves a provenance record; the
            # content hash gives it a stable identity even without a file.
            document = SourceDocument(
                filename=source or "inline text",
                media_type="text",
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                byte_size=len(text.encode("utf-8")),
                character_count=len(text),
                ingested_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                page_offsets=[
                    (match.start(), int(match.group(1))) for match in _PAGE_MARKER.finditer(text)
                ],
            )
        report = ExtractionReport(
            selection=self.selection.as_dict(),
            prompts={
                pass_id: {
                    key: value
                    for key, value in prompt.as_dict().items()
                    if key != "text"
                }
                for pass_id, prompt in self.prompts.items()
            },
            document=document.filename if document else source,
            source_document=document.as_dict() if document else {},
        )
        if not text.strip():
            raise ValueError("the document has no extractable text")

        entities: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []

        if self.selection.entity_type_ids:
            annotated, plan = self._run_pass(text, ENTITY_PASS)
            entities, entity_report = self._collect(annotated, ENTITY_PASS, text)
            entity_report.shard_count = len(plan.shards)
            entity_report.skipped_for_relevance = len(plan.skipped_type_ids)
            report.passes.append(entity_report)
            self._log(
                f"  ENTITY: {entity_report.accepted} accepted, "
                f"{entity_report.rejected} rejected of {entity_report.raw}"
            )
        else:
            report.passes.append(
                PassReport(ENTITY_PASS, skipped="no entity type is selected")
            )

        if self.selection.relationship_type_ids:
            annotated, plan = self._run_pass(text, RELATIONSHIP_PASS)
            relationships, relationship_report = self._collect(annotated, RELATIONSHIP_PASS, text)
            relationship_report.shard_count = len(plan.shards)
            relationship_report.skipped_for_relevance = len(plan.skipped_type_ids)
            report.passes.append(relationship_report)
            self._log(
                f"  RELATIONSHIP: {relationship_report.accepted} accepted, "
                f"{relationship_report.rejected} rejected of {relationship_report.raw}"
            )
        else:
            report.passes.append(
                PassReport(RELATIONSHIP_PASS, skipped="no predicate survived the selection")
            )

        graph = self._project(
            entities,
            relationships,
            document.filename if document else source,
            document=document,
            source_text=text,
        )
        kinds = Counter(data.get("record_kind") for _, data in graph.nodes(data=True))
        report.mentions = kinds["MENTION"]
        report.assertions = kinds["SOURCE_ASSERTION"] + kinds["PREDICTION"]
        return graph, report

    def process_document(
        self, path: str | Path, filename: str = ""
    ) -> tuple[nx.MultiDiGraph, ExtractionReport]:
        path = Path(path)
        text, document = self.read_document(path, filename)
        self._log(
            f"{document.filename}: {document.character_count:,} characters"
            + (f", {document.page_count} pages" if document.page_count else "")
        )
        return self.process_text(text, source=document.filename, document=document)

    @staticmethod
    def to_node_link(graph: nx.MultiDiGraph) -> dict[str, Any]:
        try:
            data = nx.node_link_data(graph, edges="links")
        except TypeError:  # NetworkX < 3.4
            data = nx.node_link_data(graph)
            if "links" not in data and "edges" in data:
                data["links"] = data.pop("edges")
        return data


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", help="PDF or text document to extract")
    parser.add_argument("-o", "--out", default="schema_extraction.json")
    parser.add_argument(
        "--module",
        action="append",
        default=None,
        help="module id or code to select; repeatable (default: the schema's runtime defaults)",
    )
    parser.add_argument(
        "--entity-type",
        action="append",
        default=None,
        help="restrict the entity pass to these type ids; repeatable",
    )
    parser.add_argument("--model-id", default="google/gemma-4-e4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--schema", default=None, help="path to the schema JSON")
    parser.add_argument(
        "--print-prompt",
        choices=("ENTITY", "RELATIONSHIP"),
        help="print the compiled prompt for a pass and exit without calling a model",
    )
    args = parser.parse_args()

    schema = load_schema(args.schema)
    modules = None
    if args.module:
        by_code = {module.get("code"): module["id"] for module in schema.data["modules"]}
        modules = [by_code.get(value, value) for value in args.module]

    extractor = SchemaGuidedExtractor(
        schema=schema,
        selection_request={
            "module_ids": modules,
            "entity_type_ids": args.entity_type,
        },
        model_id=args.model_id,
        base_url=args.base_url,
    )
    selection = extractor.selection
    print(
        f"selection: {len(selection.module_ids)} modules, "
        f"{len(selection.entity_type_ids)} entity types, "
        f"{len(selection.relationship_type_ids)} predicates"
    )
    for warning in selection.warnings:
        print(f"  warning: {warning}")

    if args.print_prompt:
        print()
        print(extractor.prompts[args.print_prompt].text)
        return

    graph, report = extractor.process_document(args.document)
    data = extractor.to_node_link(graph)
    data.setdefault("graph", {})["extraction_report"] = report.as_dict()
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_path} ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")


if __name__ == "__main__":
    _cli()
