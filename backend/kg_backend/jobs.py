"""Optional background document extraction jobs.

The web application itself has no third-party runtime dependency. Extraction is
enabled only when the original extraction stack is installed and an
OpenAI-compatible local model endpoint is available.

Both PDF source packets and plain-text corpus documents are accepted; the
pipeline picks its extraction profile from the document's own content.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import threading
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema_service import SchemaService
from .store import GraphStore

# "schema" extracts against the selected slice of the UK/NATO targeting schema:
# two passes, canonical type and predicate ids, validated before projection.
# "heuristic" is the original per-corpus prompt pipeline in building_kg/pdf_to_kg.py.
VALID_EXTRACTION_MODES = ("schema", "heuristic")

# Bounds for the two LangExtract settings an analyst can override per job
# (backend/web's "Extract from documents" panel). Mirrors PDFToKnowledgeGraph
# and SchemaGuidedExtractor's own constructor defaults - extraction_passes=2/1,
# max_char_buffer=1500 - so leaving the UI untouched runs exactly what the
# extractor would have done anyway. The ranges exist to stop a stray value
# (0 passes, a 1-character chunk) from turning into a silent no-op or a
# call storm against the local model.
MIN_EXTRACTION_PASSES, MAX_EXTRACTION_PASSES = 1, 5
MIN_CHAR_BUFFER, MAX_CHAR_BUFFER = 300, 8000


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_extraction_settings(
    extraction_passes: int | None, max_char_buffer: int | None
) -> tuple[int | None, int | None]:
    """`None` means "leave the extractor's own default alone"; anything else
    must fall inside the bounds above."""
    if extraction_passes is not None:
        if isinstance(extraction_passes, bool) or not isinstance(extraction_passes, int) or not (
            MIN_EXTRACTION_PASSES <= extraction_passes <= MAX_EXTRACTION_PASSES
        ):
            raise ValueError(
                "extraction_passes must be an integer between "
                f"{MIN_EXTRACTION_PASSES} and {MAX_EXTRACTION_PASSES}."
            )
    if max_char_buffer is not None:
        if isinstance(max_char_buffer, bool) or not isinstance(max_char_buffer, int) or not (
            MIN_CHAR_BUFFER <= max_char_buffer <= MAX_CHAR_BUFFER
        ):
            raise ValueError(
                "max_char_buffer must be an integer between "
                f"{MIN_CHAR_BUFFER} and {MAX_CHAR_BUFFER} characters."
            )
    return extraction_passes, max_char_buffer


def _examples_from_stored(stored: dict[str, Any]) -> list:
    """Rebuild `lx.data.ExampleData`/`Extraction` objects from a persisted example
    set. Mirrors the shape `building_kg/pdf_to_kg.py`'s own hardcoded
    `_default_examples()`/`_corpus_examples()` build: only extraction_class,
    extraction_text and attributes are ever populated on an example."""
    import langextract as lx

    return [
        lx.data.ExampleData(
            text=example["text"],
            extractions=[
                lx.data.Extraction(
                    extraction_class=extraction["extraction_class"],
                    extraction_text=extraction["extraction_text"],
                    attributes=extraction.get("attributes") or {},
                )
                for extraction in example["extractions"]
            ],
        )
        for example in stored["examples"]
    ]


class ExtractionJobs:
    def __init__(
        self,
        store: GraphStore,
        runtime_dir: str | Path,
        schema_service: SchemaService | None = None,
    ):
        self.store = store
        self.schema_service = schema_service
        self.runtime_dir = Path(runtime_dir).resolve()
        self.upload_dir = self.runtime_dir / "uploads"
        self.output_dir = self.runtime_dir / "extractions"
        self.job_dir = self.runtime_dir / "extraction-jobs"
        self.example_dir = self.runtime_dir / "example-sets"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.example_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kg-extract")
        self._load_existing()

    def _job_path(self, job_id: str) -> Path:
        return self.job_dir / f"{job_id}.json"

    def _persist(self, job: dict[str, Any]) -> None:
        path = self._job_path(job["id"])
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    def _load_existing(self) -> None:
        """Restore job history; interrupted in-process work fails explicitly."""
        for path in sorted(self.job_dir.glob("*.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") in {"queued", "running"}:
                job.update(
                    status="failed",
                    updated_at=_stamp(),
                    message="The backend restarted before this extraction completed.",
                )
                self._persist(job)
            self._jobs[job["id"]] = job

    def dependency_status(self) -> dict[str, Any]:
        modules = {
            "langextract": importlib.util.find_spec("langextract") is not None,
            "fitz": importlib.util.find_spec("fitz") is not None,
            "networkx": importlib.util.find_spec("networkx") is not None,
        }
        schema_ready = bool(self.schema_service and self.schema_service.available)
        base_url = os.getenv("KG_LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
        parsed = urllib.parse.urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        endpoint_reachable = False
        if all(modules.values()):
            try:
                with socket.create_connection((host, port), timeout=0.6):
                    endpoint_reachable = True
            except OSError:
                endpoint_reachable = False
        dependencies_available = all(modules.values())
        return {
            "available": dependencies_available,
            "ready": dependencies_available and endpoint_reachable,
            "modules": modules,
            "model_id": os.getenv("KG_MODEL_ID", "google/gemma-4-e4b"),
            "base_url": base_url,
            "endpoint_reachable": endpoint_reachable,
            "modes": list(VALID_EXTRACTION_MODES),
            "default_mode": "schema" if schema_ready else "heuristic",
            "schema_available": schema_ready,
        }

    @staticmethod
    def _detect_suffix(payload: bytes) -> str:
        """Decide the document kind from the bytes, not from the client's filename."""
        if payload.startswith(b"%PDF"):
            return ".pdf"
        if not payload.strip():
            raise ValueError("The uploaded file is empty.")
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(
                "Upload a PDF or a UTF-8 text document."
            ) from None
        return ".txt"

    @staticmethod
    def _safe_filename(filename: str, suffix: str) -> str:
        name = Path(filename or f"document{suffix}").name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
        if not name.lower().endswith(suffix):
            name = f"{Path(name).stem or 'document'}{suffix}"
        return name[:120] or f"document{suffix}"

    def submit_document(
        self,
        payload: bytes,
        filename: str,
        mode: str = "",
        example_set_id: str = "",
        extraction_passes: int | None = None,
        max_char_buffer: int | None = None,
    ) -> dict[str, Any]:
        extraction_passes, max_char_buffer = _validate_extraction_settings(
            extraction_passes, max_char_buffer
        )
        status = self.dependency_status()
        if not status["available"]:
            missing = [name for name, present in status["modules"].items() if not present]
            raise RuntimeError(
                "Extraction dependencies are not installed: " + ", ".join(missing)
            )
        if not status["endpoint_reachable"]:
            raise RuntimeError(
                f"The configured extraction model did not answer at {status['base_url']}."
            )
        mode = (mode or status["default_mode"]).strip().lower()
        if mode not in VALID_EXTRACTION_MODES:
            raise ValueError(
                "Extraction mode must be one of: " + ", ".join(VALID_EXTRACTION_MODES) + "."
            )
        if mode == "schema" and not status["schema_available"]:
            raise RuntimeError(
                "Schema-guided extraction needs the targeting schema document, which "
                "did not load."
            )
        example_set_id = (example_set_id or "").strip()
        if example_set_id:
            if mode != "heuristic":
                raise ValueError("example_set_id only applies to heuristic extraction")
            # Freeze the choice at submission, same reasoning as the schema
            # selection freeze below: a queued job's inputs must not shift under it.
            if self.get_example_set(example_set_id) is None:
                raise ValueError(f"Unknown example set: {example_set_id}")
        selection = None
        if mode == "schema":
            # Freeze the selection at submission. An analyst changing the
            # selection mid-run must not silently change what a queued job
            # extracts, and the job record has to say what it was run against.
            selection = self.schema_service.current_request()
            resolved = self.schema_service.resolve(selection)
            if not resolved.entity_type_ids:
                raise ValueError(
                    "The saved schema selection leaves no extractable entity type."
                )
        if len(payload) > 25 * 1024 * 1024:
            raise ValueError("Uploads are limited to 25 MiB.")
        suffix = self._detect_suffix(payload)
        job_id = uuid.uuid4().hex[:12]
        safe_name = self._safe_filename(filename, suffix)
        upload_path = self.upload_dir / f"{job_id}-{safe_name}"
        upload_path.write_bytes(payload)
        job = {
            "id": job_id,
            "job_type": "extraction",
            "filename": safe_name,
            "kind": suffix.lstrip("."),
            "mode": mode,
            "status": "queued",
            "created_at": _stamp(),
            "updated_at": _stamp(),
            "message": "Waiting for the local extraction worker.",
            "result": None,
        }
        if selection is not None:
            job["selection"] = self.schema_service.resolve(selection).as_dict()["counts"]
        if example_set_id:
            job["example_set_id"] = example_set_id
        # Frozen at submission for the same reason as the schema selection
        # above: a queued job's inputs must not shift under it, and the job
        # record has to say what it actually ran with.
        if extraction_passes is not None or max_char_buffer is not None:
            job["extraction_settings"] = {
                "extraction_passes": extraction_passes,
                "max_char_buffer": max_char_buffer,
            }
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        self._executor.submit(
            self._run,
            job_id,
            upload_path,
            mode,
            selection,
            example_set_id,
            extraction_passes,
            max_char_buffer,
        )
        return deepcopy(job)

    # Retained for callers written against the PDF-only API.
    submit_pdf = submit_document

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(values, updated_at=_stamp())
            self._persist(job)

    def _run(
        self,
        job_id: str,
        upload_path: Path,
        mode: str = "heuristic",
        selection: dict[str, Any] | None = None,
        example_set_id: str = "",
        extraction_passes: int | None = None,
        max_char_buffer: int | None = None,
    ) -> None:
        self._update(
            job_id,
            status="running",
            message=(
                "Running the schema-guided entity and relationship passes."
                if mode == "schema"
                else "Extracting entities and relationships with the configured local model."
            ),
        )
        try:
            with self._lock:
                filename = self._jobs[job_id]["filename"]
            if mode == "schema":
                graph_data, extras = self._run_schema(
                    upload_path, selection or {}, filename, extraction_passes, max_char_buffer
                )
            else:
                graph_data, extras = self._run_heuristic(
                    upload_path, filename, example_set_id, extraction_passes, max_char_buffer
                )
            output_path = self.output_dir / f"{job_id}.json"
            output_path.write_text(
                json.dumps(graph_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # This is the only trusted import path allowed to retain
            # ``host_derived``.  The schema extractor creates source-document
            # and assertion scaffolding deterministically in-process; client
            # graph imports use the store's fail-closed default instead.
            summary = self.store.import_graph(
                graph_data, mode="merge", allow_host_derived=True
            )
            self._update(
                job_id,
                status="completed",
                message="Extraction completed and candidates were merged into the review queue.",
                result={
                    "nodes_added_or_updated": len(graph_data.get("nodes", [])),
                    "relationships_considered": len(graph_data.get("links", [])),
                    "graph": summary,
                    **extras,
                },
            )
        except Exception as exc:  # boundary: provider/dependency errors become job status
            self._update(
                job_id,
                status="failed",
                message=f"Extraction failed: {type(exc).__name__}: {exc}",
            )

    def _run_schema(
        self,
        upload_path: Path,
        selection: dict[str, Any],
        filename: str = "",
        extraction_passes: int | None = None,
        max_char_buffer: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from building_kg.schema_extractor import SchemaGuidedExtractor

        settings_kwargs: dict[str, Any] = {}
        if extraction_passes is not None:
            settings_kwargs["extraction_passes"] = extraction_passes
        if max_char_buffer is not None:
            settings_kwargs["max_char_buffer"] = max_char_buffer

        extractor = SchemaGuidedExtractor(
            schema=self.schema_service.schema,
            selection_request=selection,
            model_id=os.getenv("KG_MODEL_ID", "google/gemma-4-e4b"),
            base_url=os.getenv("KG_LM_STUDIO_URL", "http://127.0.0.1:1234/v1"),
            api_key=os.getenv("KG_LM_STUDIO_API_KEY", "lm-studio"),
            verbose=False,
            **settings_kwargs,
        )
        # The upload is stored job-prefixed to keep names unique on disk; the
        # graph must carry the name the analyst actually uploaded.
        graph, report = extractor.process_document(upload_path, filename)
        graph_data = extractor.to_node_link(graph)
        full_report = report.as_dict()
        # The full report travels with the extraction file so a run's selection,
        # prompt size and per-pass rejection reasons stay auditable afterwards.
        graph_data.setdefault("graph", {})["extraction_report"] = full_report
        # The job record is polled every couple of seconds while a run is in
        # flight, so it carries the summary rather than the whole exclusion list.
        return graph_data, {
            "extraction_report": {
                "document": full_report["document"],
                "source_document": full_report["source_document"],
                "selection": full_report["selection"]["counts"],
                "prompts": full_report["prompts"],
                "passes": full_report["passes"],
                "mentions": full_report["mentions"],
                "assertions": full_report["assertions"],
            }
        }

    def _run_heuristic(
        self,
        upload_path: Path,
        filename: str = "",
        example_set_id: str = "",
        extraction_passes: int | None = None,
        max_char_buffer: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        import networkx as nx

        from building_kg.pdf_to_kg import PDFToKnowledgeGraph

        kwargs: dict[str, Any] = {}
        if example_set_id:
            stored = self.get_example_set(example_set_id)
            if stored is None:
                raise RuntimeError(f"Example set {example_set_id!r} is no longer available")
            kwargs["profile"] = stored["profile"]
            kwargs["examples"] = _examples_from_stored(stored)
        if extraction_passes is not None:
            kwargs["extraction_passes"] = extraction_passes
        if max_char_buffer is not None:
            kwargs["max_char_buffer"] = max_char_buffer

        extractor = PDFToKnowledgeGraph(
            model_id=os.getenv("KG_MODEL_ID", "google/gemma-4-e4b"),
            base_url=os.getenv("KG_LM_STUDIO_URL", "http://127.0.0.1:1234/v1"),
            api_key=os.getenv("KG_LM_STUDIO_API_KEY", "lm-studio"),
            drop_analytic_rules=True,
            verbose=False,
            **kwargs,
        )
        graph = extractor.process_document(upload_path, source_name=filename)
        # Set by process_document as graph-level metadata: which of "packet" /
        # "corpus" / "general" actually ran, and how many raw relationship
        # extractions the vocabulary/quantity filter then dropped. Surfaced on
        # the job result the same way _run_schema surfaces its extraction_report,
        # so a lopsided entity/relationship count is visible in the UI instead
        # of only in server logs nobody was reading with verbose=False.
        extraction_profile = dict(graph.graph.get("extraction_profile") or {})
        try:
            graph_data = nx.node_link_data(graph, edges="links")
        except TypeError:
            graph_data = nx.node_link_data(graph)
            if "links" not in graph_data and "edges" in graph_data:
                graph_data["links"] = graph_data.pop("edges")
        return graph_data, {"extraction_profile": extraction_profile}

    def submit_example_generation(
        self,
        documents: list[tuple[str, Path]],
        *,
        pages_per_doc: int = 3,
        profile: str = "auto",
    ) -> dict[str, Any]:
        """Draft a corpus-tailored LangExtract example set from 1-3 real documents
        (each a `(display_filename, path_on_disk)` pair), so heuristic extraction
        can be few-shotted on the analyst's own corpus instead of the two
        hardcoded synthetic example sets. Runs on the same single-worker executor
        as document extraction, so the two never contend for the local model."""
        status = self.dependency_status()
        if not status["available"]:
            missing = [name for name, present in status["modules"].items() if not present]
            raise RuntimeError(
                "Extraction dependencies are not installed: " + ", ".join(missing)
            )
        if not status["endpoint_reachable"]:
            raise RuntimeError(
                f"The configured extraction model did not answer at {status['base_url']}."
            )
        if not documents:
            raise ValueError("Select at least one document.")
        if len(documents) > 3:
            raise ValueError("Select at most 3 documents for example generation.")

        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "job_type": "example_generation",
            "filename": ", ".join(name for name, _ in documents),
            "mode": "heuristic",
            "status": "queued",
            "created_at": _stamp(),
            "updated_at": _stamp(),
            "message": "Waiting for the local extraction worker.",
            "result": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        self._executor.submit(
            self._run_example_generation, job_id, documents, pages_per_doc, profile
        )
        return deepcopy(job)

    def _run_example_generation(
        self,
        job_id: str,
        documents: list[tuple[str, Path]],
        pages_per_doc: int,
        profile: str,
    ) -> None:
        self._update(
            job_id,
            status="running",
            message="Drafting example extractions from the selected documents.",
        )
        try:
            from building_kg.example_generator import generate_example_set

            result = generate_example_set(
                documents,
                pages_per_doc=pages_per_doc,
                profile_name=profile,
                model_id=os.getenv("KG_MODEL_ID", "google/gemma-4-e4b"),
                base_url=os.getenv("KG_LM_STUDIO_URL", "http://127.0.0.1:1234/v1"),
                api_key=os.getenv("KG_LM_STUDIO_API_KEY", "lm-studio"),
            )
            example_path = self.example_dir / f"{result['id']}.json"
            example_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            self._update(
                job_id,
                status="completed",
                message=(
                    f"Drafted {len(result['examples'])} example(s) from "
                    f"{len(documents)} document(s)."
                ),
                result={
                    "example_set_id": result["id"],
                    "profile": result["profile"],
                    "counts": result["counts"],
                    "source_documents": result["source_documents"],
                },
            )
        except Exception as exc:  # boundary: provider/dependency errors become job status
            self._update(
                job_id,
                status="failed",
                message=f"Example generation failed: {type(exc).__name__}: {exc}",
            )

    def list_example_sets(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self.example_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(
                {
                    "id": data.get("id"),
                    "created_at": data.get("created_at"),
                    "profile": data.get("profile"),
                    "source_documents": [
                        d.get("filename") for d in data.get("source_documents", [])
                    ],
                    "counts": data.get("counts", {}),
                }
            )
        rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        return rows[: max(1, min(limit, 100))]

    def get_example_set(self, example_set_id: str) -> dict[str, Any] | None:
        # example_set_id reaches here from HTTP request bodies/paths - sanitise
        # before it touches the filesystem.
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", example_set_id or "")
        if not safe_id:
            return None
        path = self.example_dir / f"{safe_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return deepcopy(self._jobs[job_id])

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(
                self._jobs.values(), key=lambda row: row["created_at"], reverse=True
            )
            return [deepcopy(row) for row in rows[: max(1, min(limit, 100))]]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
