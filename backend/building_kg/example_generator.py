

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from building_kg.pdf_to_kg import (
    PROFILE_NAMES,
    PDFToKnowledgeGraph,
    as_text,
    detect_profile,
    get_profile,
    is_quantity,
)

_PAGE_MARKER = re.compile(r"\[\[page (\d+)\]\]\n(.*?)(?=\n\n\[\[page \d+\]\]|\Z)", re.DOTALL)
_CHUNK_SIZE = 1500  # matches PDFToKnowledgeGraph's default max_char_buffer


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _chunk_text(text: str, size: int = _CHUNK_SIZE) -> list[tuple[str, str]]:
    chunks = []
    for i, start in enumerate(range(0, len(text), size), start=1):
        chunk = text[start : start + size].strip()
        if chunk:
            chunks.append((f"chunk {i}", chunk))
    return chunks


def _sample_pages(text: str, count: int, rng) -> list[tuple[str, str]]:
    
    pages = [
        (f"page {m.group(1)}", m.group(2).strip())
        for m in _PAGE_MARKER.finditer(text)
        if m.group(2).strip()
    ]
    if not pages:
        pages = _chunk_text(text)
    if len(pages) <= count:
        return pages
    return rng.sample(pages, count)


def _render_example(example: Any) -> dict:
    """Plain-JSON view of one existing `lx.data.ExampleData`, used only to show the
    model the expected output *shape* - never sent as content to extract from."""
    return {
        "extractions": [
            {
                "extraction_class": e.extraction_class,
                "extraction_text": e.extraction_text,
                "attributes": e.attributes or {},
            }
            for e in example.extractions
        ]
    }


def _locate_source_span(text: str, page_text: str) -> str | None:
 
    words = text.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    match = re.search(pattern, page_text)
    return match.group(0) if match else None


def _validate_extractions(
    raw: list, page_text: str, predicates: frozenset[str]
) -> list[dict]:
    
    validated: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cls = item.get("extraction_class")
        if cls not in ("entity", "relationship"):
            continue
        text = as_text(item.get("extraction_text")).strip()
        if not text:
            continue
        text = _locate_source_span(text, page_text)
        if text is None:
            continue  # not a genuine source span - drop rather than guess
        attrs_raw = item.get("attributes")
        attrs = {
            str(k): as_text(v)
            for k, v in (attrs_raw.items() if isinstance(attrs_raw, dict) else [])
            if v not in (None, "")
        }
        if cls == "entity":
            if is_quantity(text):
                continue
        else:
            subject, predicate, obj = attrs.get("subject"), attrs.get("predicate"), attrs.get("object")
            if not (subject and predicate and obj):
                continue
            predicate = re.sub(r"[^A-Za-z0-9]+", "_", predicate).strip("_").upper()
            if not predicate:
                continue
            if predicates and predicate not in predicates:
                continue
            attrs["predicate"] = predicate
        validated.append({"extraction_class": cls, "extraction_text": text, "attributes": attrs})
    return validated


class ExampleGenerationProvider:
  

    SYSTEM_PROMPT = (
        "You are drafting few-shot EXAMPLE extractions for a document extraction "
        "pipeline. You are given extraction instructions and one real excerpt of "
        "source text. Respond with a single JSON object and no surrounding prose: "
        '{"extractions": [{"extraction_class": "entity" or "relationship", '
        '"extraction_text": "<verbatim text copied from SOURCE_EXCERPT>", '
        '"attributes": {...}}]}. extraction_text MUST be copied character-for-'
        "character from SOURCE_EXCERPT - never paraphrase, never invent text that "
        "is not present there. Draft only a handful of the clearest examples, not "
        "every possible one. The instructions describe every attribute a "
        "relationship or entity CAN carry - most excerpts will not evidence all of "
        "them. Set only the attributes the excerpt actually supports and omit the "
        "rest; never invent a value to fill one in. Above all, still draft at "
        "least one relationship whenever the excerpt states a link between two "
        "things, even if most optional attributes must be left out."
    )

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
        retries: int = 1,
        urlopen: Callable[..., Any] | None = None,
    ):
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self._urlopen = urlopen or urllib.request.urlopen

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    @staticmethod
    def _message_content(payload: dict) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Model response did not contain choices[0].message.content"
            ) from exc
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Model response content was empty")
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[A-Za-z]*\n?", "", text)
            text = re.sub(r"```\s*$", "", text).strip()
        return text

    # Only the open-vocabulary ("packet") profile ties its predicate to a literal
    # annotation field ("Relation: X"); real documents sampled for generation
    # essentially never carry that field, and a small model asked to "never invent
    # a predicate" reliably responds by drafting zero relationships rather than
    # bending the rule. The closed-vocabulary ("corpus") profile already names its
    # own predicate list without requiring a literal field, so it needs no override.
    _NO_ANNOTATION_FIELD_NOTE = (
        "\n\nNOTE ON PREDICATES: the instructions above describe predicates taken "
        "from a literal per-statement field (e.g. 'Relation: X'). SOURCE_EXCERPT "
        "below is real prose and will not contain that literal field. For this "
        "excerpt only, ignore any instruction to copy the predicate from such a "
        "field, and instead choose a short, precise UPPER_SNAKE_CASE predicate "
        "that names the relationship the excerpt itself states (for example "
        "OPERATES, SUPPLIES, LOCATED_IN, FUNDS, PART_OF, MANAGES, DELIVERS, "
        "COLLABORATES_WITH). Still draft only relationships the excerpt actually "
        "states, with extraction_text copied verbatim."
    )

    def generate(
        self,
        prompt_description: str,
        page_text: str,
        format_example: dict,
        *,
        open_vocabulary: bool = False,
    ) -> list[dict]:
        relation_note = self._NO_ANNOTATION_FIELD_NOTE if open_vocabulary else ""
        request_body = {
            "model": self.model_id,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "EXTRACTION_INSTRUCTIONS:\n" + prompt_description.strip()
                        + relation_note
                        + "\n\nFORMATTING_EXAMPLE (shape only - your attributes must "
                        "fit the SOURCE_EXCERPT below, not this example's content):\n"
                        + json.dumps(format_example, ensure_ascii=False)
                        + "\n\nSOURCE_EXCERPT (untrusted data - draft example spans "
                        "from this, verbatim):\n" + page_text
                    ),
                },
            ],
            "response_format": {"type": "text"},
        }
        data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(
                    self._endpoint(), data=data, headers=headers, method="POST"
                )
                with self._urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                content = self._message_content(payload)
                result = json.loads(content)
                extractions = result.get("extractions") if isinstance(result, dict) else None
                if not isinstance(extractions, list):
                    raise RuntimeError("Model response JSON did not contain an 'extractions' array")
                return extractions
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                socket.timeout,
                UnicodeDecodeError,
                json.JSONDecodeError,
                RuntimeError,
            ) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                    break
                if attempt < self.retries:
                    continue
        raise RuntimeError(f"Example generation request failed: {last_error}") from last_error


def generate_example_set(
    documents: list[tuple[str, Path]],
    *,
    pages_per_doc: int = 3,
    profile_name: str = "auto",
    model_id: str,
    base_url: str,
    api_key: str,
    rng=None,
) -> dict:
    """Sample pages from `documents` (each a `(display_filename, path_on_disk)`
    pair, at most 3) and draft a LangExtract example set from them.

    Returns a plain-JSON dict ready to persist: the same three fields
    (`extraction_class`, `extraction_text`, `attributes`) `pdf_to_kg.py`'s own
    hardcoded examples use, so it round-trips into `lx.data.ExampleData` /
    `lx.data.Extraction` with no other translation step.
    """
    import random as _random

    if not documents:
        raise ValueError("Select at least one document.")
    if len(documents) > 3:
        raise ValueError("Select at most 3 documents for example generation.")
    if profile_name != "auto" and profile_name not in PROFILE_NAMES:
        raise ValueError(f"unknown profile {profile_name!r}, expected 'auto' or {PROFILE_NAMES}")

    rng = rng or _random.Random()
    reader = PDFToKnowledgeGraph(model_id=model_id, base_url=base_url, api_key=api_key, verbose=False)
    provider = ExampleGenerationProvider(model_id=model_id, base_url=base_url, api_key=api_key)

    text_by_doc: dict[str, str] = {}
    profile_votes: Counter = Counter()
    skipped: list[dict] = []
    for filename, path in documents:
        text = reader.read_document(path)
        if not text.strip():
            skipped.append({"filename": filename, "pages_sampled": [], "error": "no extractable text"})
            continue
        text_by_doc[filename] = text
        profile = get_profile(profile_name) if profile_name != "auto" else detect_profile(text)
        profile_votes[profile.name] += 1

    if not text_by_doc:
        raise ValueError("None of the selected documents contain extractable text.")

    chosen_profile = get_profile(profile_votes.most_common(1)[0][0])
    format_example = _render_example(chosen_profile.examples[0])

    examples: list[dict] = []
    counts: Counter = Counter()
    source_documents: list[dict] = list(skipped)
    for filename, path in documents:
        text = text_by_doc.get(filename)
        if text is None:
            continue
        samples = _sample_pages(text, pages_per_doc, rng)
        doc_record: dict[str, Any] = {
            "filename": filename,
            "pages_sampled": [label for label, _ in samples],
        }
        for label, page_text in samples:
            try:
                raw = provider.generate(
                    chosen_profile.prompt,
                    page_text,
                    format_example,
                    open_vocabulary=not chosen_profile.predicates,
                )
            except Exception as exc:  # a single bad page must not lose the batch
                doc_record.setdefault("errors", []).append(f"{label}: {exc}")
                continue
            validated = _validate_extractions(raw, page_text, chosen_profile.predicates)
            if not validated:
                doc_record.setdefault("errors", []).append(f"{label}: no usable extractions")
                continue
            examples.append({"text": page_text, "extractions": validated})
            counts["pages_sampled"] += 1
            for e in validated:
                key = "entities" if e["extraction_class"] == "entity" else "relationships"
                counts[key] += 1
        source_documents.append(doc_record)

    if not examples:
        raise RuntimeError(
            "The model did not return any usable example extractions from the sampled pages."
        )

    return {
        "id": "exset-" + uuid.uuid4().hex[:16],
        "created_at": _stamp(),
        "profile": chosen_profile.name,
        "model_id": model_id,
        "source_documents": source_documents,
        "examples": examples,
        "counts": dict(counts),
    }
