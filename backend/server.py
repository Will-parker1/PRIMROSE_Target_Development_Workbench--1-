#!/usr/bin/env python3
"""Local web server for the AI Enabled Knowledge Graph application.

The core application uses only the Python standard library. Optional document/LLM
extraction continues to use the dependencies declared in requirements.txt.
"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import signal
import sys
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from kg_backend import (
    ExtractionJobs,
    GraphRAGEngine,
    GraphStore,
    GraphValidationError,
    QueryEngine,
    SchemaService,
    VALID_MODES,
)
from primrose import PrimroseWorkbench
from projects import ProjectRegistry
from target_development import TargetScreeningError


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_GRAPH = ROOT / "graph.graphml"
DEFAULT_STATE = ROOT / "runtime" / "review_state.json"
DEFAULT_PROJECTS_ROOT = ROOT / "runtime" / "projects"
DEFAULT_CORPUS = ROOT / "Data" / "corpus"
MAX_JSON_BODY = 30 * 1024 * 1024


class Application:
    def __init__(self, graph_path: Path, state_path: Path, schema_path: Path | None = None):
        self.store = GraphStore(graph_path, state_path)
        self.query = QueryEngine(self.store)
        self.graphrag = GraphRAGEngine(self.store)
        self.schema = SchemaService(state_path.parent, schema_path)
        self.jobs = ExtractionJobs(self.store, state_path.parent, self.schema)
        self.workbench = PrimroseWorkbench(self.store)

    def answer(self, question: str, mode: str = "") -> dict[str, Any]:
        """Route a question to the deterministic engine or the local-model path."""
        mode = (mode or self.graphrag.default_mode()).strip().casefold()
        if mode not in VALID_MODES:
            raise ValueError(
                "mode must be one of: " + ", ".join(sorted(VALID_MODES)) + "."
            )
        if mode == "deterministic":
            return self.query.ask(question)
        if mode == "graphrag":
            return self.graphrag.ask(question)
        status = self.graphrag.dependency_status()
        if not status["available"]:
            response = self.query.ask(question)
            response["trace"] += (
                " GraphRAG was requested but the optional LangChain toolchain is not "
                "installed, so the deterministic engine answered instead."
            )
            return response
        if not status["endpoint_reachable"]:
            response = self.query.ask(question)
            response["trace"] += (
                f" GraphRAG was requested but no local model answered at "
                f"{status['base_url']}, so the deterministic engine answered instead."
            )
            return response
        try:
            return self.graphrag.ask(question)
        except RuntimeError as exc:
            response = self.query.ask(question)
            response["trace"] += f" The local model was unavailable ({exc}), so the deterministic engine answered instead."
            return response


def make_handler(
    registry: ProjectRegistry,
    web_root: Path = WEB_ROOT,
    corpus_root: Path = DEFAULT_CORPUS,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AIEnabledKG/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("[web] " + fmt % args + "\n")

        def _headers(
            self,
            status: int,
            content_type: str,
            length: int,
            *,
            disposition: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()

        def _bytes(
            self,
            payload: bytes,
            status: int = HTTPStatus.OK,
            content_type: str = "application/octet-stream",
            *,
            disposition: str | None = None,
        ) -> None:
            self._headers(status, content_type, len(payload), disposition=disposition)
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _json(
            self,
            payload: Any,
            status: int = HTTPStatus.OK,
            *,
            disposition: str | None = None,
        ) -> None:
            encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            self._bytes(
                encoded,
                status,
                "application/json; charset=utf-8",
                disposition=disposition,
            )

        def _error(self, status: int, message: str, detail: str = "") -> None:
            self._json(
                {"error": message, "detail": detail, "status": int(status)}, status
            )

        def _read_body(self, maximum: int = MAX_JSON_BODY) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid Content-Length header") from exc
            if length <= 0:
                return b""
            if length > maximum:
                raise OverflowError(f"Request body exceeds {maximum} bytes")
            return self.rfile.read(length)

        def _read_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ValueError("Content-Type must be application/json")
            body = self._read_body()
            if not body:
                return {}
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Request body is not valid UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON request body must be an object")
            return value

        def _check_origin(self) -> None:
            origin = self.headers.get("Origin")
            if not origin:
                return
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.scheme not in {"http", "https"} or parsed_origin.netloc != self.headers.get("Host"):
                raise PermissionError("Cross-origin state changes are not allowed.")

        def _check_service_auth(self) -> None:
            """Require the Worker-to-backend credential when one is configured.

            Local use remains zero-configuration on the default loopback bind.
            A remotely reachable deployment must set PRIMROSE_BACKEND_TOKEN and
            the same secret on its same-origin proxy.
            """
            expected = os.getenv("PRIMROSE_BACKEND_TOKEN", "")
            if not expected:
                return
            supplied = self.headers.get("Authorization", "")
            prefix = "Bearer "
            candidate = supplied[len(prefix) :] if supplied.startswith(prefix) else ""
            if not candidate or not hmac.compare_digest(candidate, expected):
                raise PermissionError("Backend service authentication failed.")

        @staticmethod
        def _int(params: dict[str, list[str]], name: str, default: int) -> int:
            try:
                return int(params.get(name, [str(default)])[0])
            except (TypeError, ValueError):
                return default

        @staticmethod
        def _optional_int(params: dict[str, list[str]], name: str) -> int | None:
            """Unlike `_int`, an absent or blank value means "no override" (the
            caller's own default applies), while a present-but-invalid value is
            a genuine client error - it must raise, not silently fall back, or
            an analyst's mistyped setting would run unnoticed."""
            raw = (params.get(name, [""])[0] or "").strip()
            if not raw:
                return None
            return int(raw)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(parsed.query)
            application = registry.active()
            try:
                if path.startswith("/api"):
                    self._check_service_auth()
                if path == "/api/health":
                    self._json(
                        {
                            "status": "ok",
                            "application": "AI Enabled Knowledge Graph",
                            "graph_loaded": True,
                            "extraction": application.jobs.dependency_status(),
                            "reasoning": application.graphrag.dependency_status(),
                            "schema": application.schema.status(),
                            "schema_mission_llm": application.schema.mission_llm.status(),
                            "gemma_explanations": application.workbench.gemma4.status(),
                            "target_development": application.workbench.target_jobs.status(),
                            "optional_adapters": application.workbench.adapters(),
                        }
                    )
                elif path == "/api/projects":
                    self._json(registry.list())
                elif path == "/api/corpus":
                    self._json(self._corpus_listing())
                elif path == "/api/schema":
                    self._json(application.schema.catalogue())
                elif path == "/api/schema/selection":
                    self._json(application.schema.preview())
                elif path == "/api/schema/prompt":
                    self._json(
                        application.schema.prompt_text(params.get("pass", ["ENTITY"])[0])
                    )
                elif path == "/api/summary":
                    self._json(application.store.summary())
                elif path == "/api/workbench/bootstrap":
                    self._json(application.workbench.bootstrap())
                elif path == "/api/workbench/queues":
                    self._json(
                        application.workbench.queues(
                            limit=self._int(params, "limit", 50)
                        )
                    )
                elif path == "/api/workbench/methods":
                    self._json(application.workbench.methods())
                elif path.startswith("/api/workbench/methods/"):
                    method_id = urllib.parse.unquote(path.split("/", 4)[4])
                    self._json(application.workbench.method(method_id))
                elif path == "/api/workbench/profiles":
                    self._json(application.workbench.profiles())
                elif path == "/api/workbench/adapters":
                    self._json(application.workbench.adapters())
                elif path == "/api/workbench/graph":
                    self._json(
                        application.workbench.graph(
                            focus=params.get("focus", [""])[0],
                            depth=self._int(params, "depth", 1),
                            limit=self._int(params, "limit", 60),
                        )
                    )
                elif path.startswith("/api/workbench/objects/"):
                    parts = path.split("/", 5)
                    if len(parts) != 6:
                        raise KeyError("Malformed workbench object path")
                    kind = urllib.parse.unquote(parts[4])
                    item_id = urllib.parse.unquote(parts[5])
                    self._json(application.workbench.object_detail(kind, item_id))
                elif path == "/api/target-development/meta":
                    self._json(application.workbench.target_development_meta())
                elif path == "/api/target-development/candidates":
                    self._json(
                        application.workbench.target_development.candidates(
                            query=params.get("q", [""])[0],
                            limit=self._int(params, "limit", 50),
                            offset=self._int(params, "offset", 0),
                        )
                    )
                elif path == "/api/target-development/jobs":
                    self._json(
                        {
                            "items": application.workbench.target_jobs.list(
                                self._int(params, "limit", 50)
                            )
                        }
                    )
                elif path.startswith("/api/target-development/jobs/"):
                    job_id = urllib.parse.unquote(path.split("/", 4)[4])
                    self._json(application.workbench.target_jobs.get(job_id))
                elif path == "/api/documents":
                    self._json(
                        {
                            "items": application.store.documents(
                                self._int(params, "limit", 200)
                            )
                        }
                    )
                elif path == "/api/review":
                    self._json(
                        application.store.review_queue(
                            kind=params.get("kind", ["all"])[0],
                            status=params.get("status", ["unreviewed"])[0],
                            query=params.get("q", [""])[0],
                            document=params.get("document", [""])[0],
                            limit=self._int(params, "limit", 20),
                            offset=self._int(params, "offset", 0),
                        )
                    )
                elif path == "/api/search":
                    self._json(
                        {
                            "results": application.store.search(
                                params.get("q", [""])[0],
                                self._int(params, "limit", 12),
                            )
                        }
                    )
                elif path == "/api/graph":
                    self._json(
                        application.store.graph_slice(
                            focus=params.get("focus", [""])[0],
                            depth=self._int(params, "depth", 1),
                            limit=self._int(params, "limit", 60),
                            statuses=params.get(
                                "statuses", ["accepted,unreviewed"]
                            )[0],
                        )
                    )
                elif path.startswith("/api/items/"):
                    parts = path.split("/", 4)
                    if len(parts) != 5:
                        raise KeyError("Malformed item path")
                    kind = urllib.parse.unquote(parts[3])
                    item_id = urllib.parse.unquote(parts[4])
                    self._json(application.store.item(kind, item_id))
                elif path == "/api/audit":
                    self._json(
                        {"items": application.store.audit(self._int(params, "limit", 50))}
                    )
                elif path == "/api/jobs":
                    self._json({"items": application.jobs.list()})
                elif path.startswith("/api/jobs/"):
                    job_id = urllib.parse.unquote(path.split("/", 3)[3])
                    self._json(application.jobs.get(job_id))
                elif path == "/api/examples":
                    self._json(
                        {"items": application.jobs.list_example_sets(self._int(params, "limit", 50))}
                    )
                elif path.startswith("/api/examples/"):
                    example_id = urllib.parse.unquote(path.split("/", 3)[3])
                    example_set = application.jobs.get_example_set(example_id)
                    if example_set is None:
                        raise KeyError(f"Unknown example set: {example_id}")
                    self._json(example_set)
                elif path == "/api/export/graph":
                    requested_status = params.get("status", ["all"])[0].strip().lower()
                    statuses = None if requested_status == "all" else {
                        part.strip() for part in requested_status.split(",") if part.strip()
                    }
                    filename = (
                        "ai_enabled_knowledge_graph.json"
                        if statuses is None
                        else "ai_enabled_knowledge_graph_accepted.json"
                    )
                    self._json(
                        application.store.export_graph(
                            include_review=True,
                            statuses=statuses,
                        ),
                        disposition=f'attachment; filename="{filename}"',
                    )
                elif path == "/api/export/reviews":
                    self._json(
                        application.store.export_reviews(),
                        disposition='attachment; filename="knowledge_graph_reviews.json"',
                    )
                elif path == "/api":
                    self._json(
                        {
                            "name": "AI Enabled Knowledge Graph API",
                            "version": "1.0",
                            "endpoints": [
                                "GET /api/projects",
                                "POST /api/projects",
                                "POST /api/projects/{id}/activate",
                                "GET /api/corpus",
                                "POST /api/corpus/ingest",
                                "GET /api/summary",
                                "GET /api/review",
                                "POST /api/review",
                                "GET /api/graph",
                                "GET /api/search",
                                "POST /api/query (mode: deterministic | graphrag | auto)",
                                "POST /api/graph/import",
                                "POST /api/extractions (mode: schema | heuristic, example_set_id?)",
                                "GET /api/documents",
                                "GET /api/examples",
                                "GET /api/examples/{id}",
                                "POST /api/examples/generate",
                                "GET /api/schema",
                                "GET /api/schema/selection",
                                "POST /api/schema/selection",
                                "POST /api/schema/selection/preview",
                                "POST /api/schema/suggest",
                                "POST /api/schema/suggest-llm",
                                "POST /api/schema/selection/reset",
                                "GET /api/schema/prompt?pass=ENTITY|RELATIONSHIP",
                                "GET /api/workbench/bootstrap",
                                "GET /api/workbench/queues",
                                "GET /api/workbench/methods",
                                "GET /api/workbench/profiles",
                                "GET /api/workbench/adapters",
                                "GET /api/workbench/graph",
                                "GET /api/workbench/objects/node/{id}",
                                "POST /api/workbench/explanations",
                                "GET /api/target-development/meta",
                                "GET /api/target-development/candidates",
                                "POST /api/target-development/preview",
                                "GET/POST /api/target-development/jobs",
                                "GET /api/target-development/jobs/{id}",
                            ],
                        }
                    )
                elif path.startswith("/api/"):
                    self._error(HTTPStatus.NOT_FOUND, "API route not found")
                else:
                    self._serve_static(path)
            except PermissionError as exc:
                self._error(HTTPStatus.FORBIDDEN, "Request forbidden", str(exc))
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, "Item not found", str(exc))
            except (ValueError, GraphValidationError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, "Invalid request", str(exc))
            except Exception as exc:  # final HTTP boundary
                self.log_error("Unhandled GET error: %s: %s", type(exc).__name__, exc)
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "The request could not be completed",
                )

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            params = urllib.parse.parse_qs(parsed.query)
            application = registry.active()
            try:
                if path.startswith("/api"):
                    self._check_service_auth()
                self._check_origin()
                if path == "/api/projects":
                    body = self._read_json()
                    self._json(registry.create(str(body.get("name", ""))), HTTPStatus.CREATED)
                elif path.startswith("/api/projects/") and path.endswith("/activate"):
                    project_id = urllib.parse.unquote(path.split("/", 3)[3][: -len("/activate")])
                    self._json(registry.activate(project_id))
                elif path == "/api/corpus/ingest":
                    body = self._read_json()
                    self._json(self._corpus_ingest(application, body))
                elif path == "/api/examples/generate":
                    body = self._read_json()
                    self._json(
                        self._examples_generate(application, body), HTTPStatus.ACCEPTED
                    )
                elif path == "/api/review":
                    body = self._read_json()
                    proxy_analyst = self.headers.get(
                        "X-PRIMROSE-Authenticated-User", ""
                    ).strip()
                    result = application.store.decide(
                        str(body.get("kind", "")),
                        str(body.get("id", "")),
                        str(body.get("decision", "")),
                        str(body.get("reason", "")),
                        proxy_analyst[:320]
                        if proxy_analyst
                        else str(body.get("analyst", "Local analyst")),
                    )
                    self._json(result)
                elif path == "/api/query":
                    body = self._read_json()
                    self._json(
                        application.answer(
                            str(body.get("question", "")),
                            str(body.get("mode", params.get("mode", [""])[0])),
                        )
                    )
                elif path == "/api/graph/import":
                    body = self._read_json()
                    graph_data = body.get("graph", body)
                    mode = params.get("mode", [str(body.get("mode", "replace"))])[0]
                    self._json(application.store.import_graph(graph_data, mode=mode))
                elif path == "/api/schema/selection":
                    self._json(application.schema.save(self._read_json()))
                elif path == "/api/schema/selection/preview":
                    self._json(
                        application.schema.preview(
                            application.schema.clean_request(self._read_json())
                        )
                    )
                elif path == "/api/schema/suggest":
                    body = self._read_json()
                    self._json(
                        application.schema.suggest(
                            str(body.get("topic", "")),
                            max_entity_types=int(body.get("max_entity_types", 24)),
                        )
                    )
                elif path == "/api/schema/suggest-llm":
                    body = self._read_json()
                    self._json(
                        application.schema.suggest_from_mission(
                            str(body.get("mission", "")),
                            max_entity_types=int(body.get("max_entity_types", 40)),
                        )
                    )
                elif path == "/api/schema/selection/reset":
                    self._json(application.schema.reset())
                elif path == "/api/extractions":
                    payload = self._read_body(25 * 1024 * 1024)
                    filename = self.headers.get("X-Filename", "document")
                    mode = params.get("mode", [self.headers.get("X-Extraction-Mode", "")])[0]
                    example_set_id = params.get("example_set_id", [""])[0]
                    self._json(
                        application.jobs.submit_document(
                            payload,
                            filename,
                            mode,
                            example_set_id,
                            extraction_passes=self._optional_int(params, "extraction_passes"),
                            max_char_buffer=self._optional_int(params, "max_char_buffer"),
                        ),
                        HTTPStatus.ACCEPTED,
                    )
                elif path == "/api/target-development/preview":
                    body = self._read_json()
                    self._json(
                        application.workbench.target_development.preview(
                            str(body.get("target_id", "")),
                            phase=str(body.get("phase", "basic")),
                            depth=int(body.get("depth", 2)),
                            limit=int(body.get("limit", 90)),
                            include_prompts=bool(body.get("include_prompts", False)),
                        )
                    )
                elif path == "/api/target-development/jobs":
                    body = self._read_json()
                    self._json(
                        application.workbench.target_jobs.submit(
                            str(body.get("target_id", "")),
                            phase=str(body.get("phase", "basic")),
                            depth=int(body.get("depth", 2)),
                            limit=int(body.get("limit", 90)),
                        ),
                        HTTPStatus.ACCEPTED,
                    )
                elif path == "/api/workbench/explanations":
                    body = self._read_json()
                    self._json(
                        application.workbench.explain(
                            str(body.get("id", "")),
                            str(body.get("question", "")),
                        )
                    )
                else:
                    self._error(HTTPStatus.NOT_FOUND, "API route not found")
            except OverflowError as exc:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large", str(exc))
            except PermissionError as exc:
                self._error(HTTPStatus.FORBIDDEN, "Request forbidden", str(exc))
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, "Item not found", str(exc))
            except TargetScreeningError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "Target development stopped by screening", str(exc))
            except RuntimeError as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Optional service unavailable", str(exc))
            except (ValueError, GraphValidationError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, "Invalid request", str(exc))
            except Exception as exc:  # final HTTP boundary
                self.log_error("Unhandled POST error: %s: %s", type(exc).__name__, exc)
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "The request could not be completed",
                )

        def _corpus_listing(self) -> dict[str, Any]:
            root = corpus_root
            if not root.is_dir():
                return {"available": False, "directory": str(root), "items": []}
            items = [
                {"filename": entry.name, "size": entry.stat().st_size}
                for entry in sorted(root.iterdir())
                if entry.is_file() and not entry.name.startswith(".")
            ]
            return {"available": True, "directory": str(root), "items": items}

        @staticmethod
        def _resolve_corpus_files(names: list) -> tuple[list[tuple[str, Path]], list[dict[str, str]]]:
            root = corpus_root.resolve()
            if not root.is_dir():
                raise RuntimeError(f"Corpus directory not found: {root}")
            resolved: list[tuple[str, Path]] = []
            errors: list[dict[str, str]] = []
            for raw_name in names:
                filename = str(raw_name)
                candidate = (root / filename).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    errors.append({"filename": filename, "error": "Invalid corpus filename"})
                    continue
                if not candidate.is_file():
                    errors.append({"filename": filename, "error": "File not found in corpus"})
                    continue
                resolved.append((filename, candidate))
            return resolved, errors

        def _corpus_ingest(self, application: Application, body: dict[str, Any]) -> dict[str, Any]:
            requested = body.get("files")
            if not isinstance(requested, list) or not requested:
                raise ValueError("files must be a non-empty list of corpus filenames")
            if len(requested) > 200:
                raise ValueError("Select at most 200 files per batch")
            mode = str(body.get("mode", ""))
            example_set_id = str(body.get("example_set_id", ""))
            extraction_passes = body.get("extraction_passes")
            max_char_buffer = body.get("max_char_buffer")
            status = application.jobs.dependency_status()
            if not status["available"]:
                missing = [name for name, present in status["modules"].items() if not present]
                raise RuntimeError(
                    "Extraction dependencies are not installed: " + ", ".join(missing)
                )
            if not status["endpoint_reachable"]:
                raise RuntimeError(
                    f"The configured extraction model did not answer at {status['base_url']}."
                )
            resolved, errors = self._resolve_corpus_files(requested)
            submitted: list[dict[str, Any]] = []
            for filename, candidate in resolved:
                try:
                    job = application.jobs.submit_document(
                        candidate.read_bytes(),
                        filename,
                        mode,
                        example_set_id,
                        extraction_passes=extraction_passes,
                        max_char_buffer=max_char_buffer,
                    )
                    submitted.append(job)
                except (RuntimeError, ValueError, OSError) as exc:
                    errors.append({"filename": filename, "error": str(exc)})
            return {"submitted": submitted, "errors": errors, "total_submitted": len(submitted)}

        def _examples_generate(self, application: Application, body: dict[str, Any]) -> dict[str, Any]:
            requested = body.get("files")
            if not isinstance(requested, list) or not requested:
                raise ValueError("files must be a non-empty list of corpus filenames")
            if len(requested) > 3:
                raise ValueError("Select at most 3 files for example generation")
            pages_per_doc = int(body.get("pages_per_doc", 3))
            profile = str(body.get("profile", "auto"))
            resolved, errors = self._resolve_corpus_files(requested)
            if errors:
                raise ValueError(
                    "; ".join(f"{e['filename']}: {e['error']}" for e in errors)
                )
            return application.jobs.submit_example_generation(
                resolved, pages_per_doc=pages_per_doc, profile=profile
            )

        def _serve_static(self, path: str) -> None:
            relative = "index.html" if path in {"", "/"} else urllib.parse.unquote(path.lstrip("/"))
            candidate = (web_root / relative).resolve()
            try:
                candidate.relative_to(web_root.resolve())
            except ValueError:
                self._error(HTTPStatus.FORBIDDEN, "Invalid static path")
                return
            if not candidate.is_file():
                # Hash routing keeps browser navigation at /. Missing assets should
                # be a real 404 rather than HTML returned as JavaScript or CSS.
                if Path(relative).suffix:
                    self._error(HTTPStatus.NOT_FOUND, "Static asset not found")
                    return
                candidate = web_root / "index.html"
            if not candidate.is_file():
                self._error(HTTPStatus.NOT_FOUND, "Frontend is not installed")
                return
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
                mime += "; charset=utf-8"
            self._bytes(candidate.read_bytes(), content_type=mime)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="directory of documents browsable/loadable from the Sources tab (default: Data/corpus)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="targeting extraction schema JSON (default: the profile in the project root)",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--query-mode",
        choices=sorted(VALID_MODES),
        help=(
            "Default answering mode for Ask graph. 'graphrag' and 'auto' use the local "
            "LM Studio model; the environment variable KG_QUERY_MODE does the same."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.query_mode:
        os.environ["KG_QUERY_MODE"] = args.query_mode
    application = Application(args.graph, args.state, args.schema)
    registry = ProjectRegistry(
        application,
        "Default",
        DEFAULT_PROJECTS_ROOT,
        args.schema,
        application_factory=Application,
    )
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(registry, corpus_root=args.corpus)
    )
    address = f"http://{args.host}:{server.server_address[1]}"
    print(f"AI Enabled Knowledge Graph running at {address}")
    schema_status = application.schema.status()
    if schema_status["available"]:
        counts = schema_status["selection"]["counts"]
        print(
            f"Extraction schema: {schema_status['title']} v{schema_status['version']} · "
            f"{counts['entity_types']} entity types and {counts['relationship_types']} "
            f"predicates selected"
        )
    else:
        print(f"Extraction schema not loaded: {schema_status['error']}")
    reasoning = application.graphrag.dependency_status()
    print(
        f"Ask graph default mode: {reasoning['default_mode']} · local model "
        f"{reasoning['model_id']} at {reasoning['base_url']} "
        f"({'LangChain available' if reasoning['available'] else 'LangChain not installed'})"
    )
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        registry.close_all()
        server.server_close()


if __name__ == "__main__":
    main()
