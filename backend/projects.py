"""Registry of independent knowledge-graph projects.

Every piece of per-project state -- graph, review decisions, ontology
selection, extraction jobs, PRIMROSE queues, target-development jobs -- is
already fully determined by the ``(graph_path, state_path)`` pair an
``Application`` (see server.py) is built from, with no other shared state
anywhere in the application layer. A "project" is therefore nothing more than
one such pair plus the ``Application`` it produces.

This registry always holds a "default" project -- the one the process was
started with via ``--graph``/``--state`` -- so default boot behaviour is
unchanged, plus any additional projects created at runtime, and tracks which
one requests should currently be routed to.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ApplicationFactory = Callable[[Path, Path, "Path | None"], Any]

EMPTY_GRAPH: dict[str, Any] = {
    "directed": False,
    "multigraph": False,
    "graph": {},
    "nodes": [],
    "links": [],
}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:40] or "project"


def _new_project_id(name: str) -> str:
    return f"{_slugify(name)}-{uuid.uuid4().hex[:6]}"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRegistry:
    """Holds one Application per project and tracks which one is active."""

    def __init__(
        self,
        default_app: Any,
        default_name: str,
        projects_root: Path,
        schema_path: Path | None,
        application_factory: ApplicationFactory,
    ):
        self._factory = application_factory
        self._schema_path = schema_path
        self._projects_root = Path(projects_root)
        self._lock = threading.RLock()
        self._apps: dict[str, Any] = {"default": default_app}
        self._meta: dict[str, dict[str, Any]] = {
            "default": {"id": "default", "name": default_name, "created_at": _stamp()}
        }
        self.active_id = "default"

    def _describe_locked(self, project_id: str) -> dict[str, Any]:
        app = self._apps[project_id]
        meta = self._meta[project_id]
        summary = app.store.summary()
        return {
            "id": project_id,
            "name": meta["name"],
            "created_at": meta["created_at"],
            "active": project_id == self.active_id,
            "nodes": summary.get("nodes", 0),
            "relationships": summary.get("relationships", 0),
            "graph_name": summary.get("graph_name", meta["name"]),
            "updated_at": summary.get("updated_at", meta["created_at"]),
        }

    def list(self) -> dict[str, Any]:
        with self._lock:
            items = [self._describe_locked(project_id) for project_id in self._apps]
            return {"items": items, "active_id": self.active_id}

    def describe(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            if project_id not in self._apps:
                raise KeyError(f"Unknown project: {project_id}")
            return self._describe_locked(project_id)

    def create(self, name: str) -> dict[str, Any]:
        clean_name = (name or "").strip() or "Untitled project"
        with self._lock:
            project_id = _new_project_id(clean_name)
            while project_id in self._apps:
                project_id = _new_project_id(clean_name)
            project_dir = self._projects_root / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            graph_path = project_dir / "graph.json"
            state_path = project_dir / "review_state.json"
            graph_payload = {**EMPTY_GRAPH, "graph": {"name": clean_name}}
            graph_path.write_text(
                json.dumps(graph_payload, ensure_ascii=False), encoding="utf-8"
            )
            app = self._factory(graph_path, state_path, self._schema_path)
            self._apps[project_id] = app
            self._meta[project_id] = {
                "id": project_id,
                "name": clean_name,
                "created_at": _stamp(),
            }
            return self._describe_locked(project_id)

    def activate(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            if project_id not in self._apps:
                raise KeyError(f"Unknown project: {project_id}")
            self.active_id = project_id
            return self._describe_locked(project_id)

    def active(self) -> Any:
        with self._lock:
            return self._apps[self.active_id]

    def close_all(self) -> None:
        with self._lock:
            for app in self._apps.values():
                app.jobs.close()
                app.workbench.target_jobs.close()
