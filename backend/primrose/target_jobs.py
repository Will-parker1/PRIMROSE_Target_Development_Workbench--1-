"""Asynchronous, fail-closed model generation for target-development records.

The protected :mod:`target_development` implementation remains the source of
screening, dossier construction and prompting.  This adapter only moves its
sequential model calls off the HTTP request thread and persists the resulting
job record so a browser can poll it safely.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from target_development import (
    DOCTRINE,
    VERDICT_DEVELOPABLE,
    TargetScreeningError,
    default_generator,
    develop,
    screen_entity,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TargetDevelopmentJobs:
    """Run one model-backed target-development record at a time.

    A basic record makes several sequential provider calls.  Keeping one worker
    avoids loading multiple local-model conversations at once, while the
    persisted sidecars make completed records survive a service restart.
    """

    def __init__(
        self,
        store: Any,
        runtime_dir: str | Path,
        *,
        generator_factory: Callable[[dict[str, str]], Callable[[dict[str, str]], str]] | None = None,
    ):
        self.store = store
        self.output_dir = Path(runtime_dir).resolve() / "target-development"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._generator_factory = generator_factory
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="target-development")
        self._load_existing()

    # ---------------------------------------------------------------- config

    @staticmethod
    def config() -> dict[str, str]:
        """Return server-side model configuration without exposing a secret."""
        return {
            "model_id": os.getenv(
                "TARGET_DEVELOPMENT_MODEL_ID",
                os.getenv("PRIMROSE_GEMMA4_MODEL_ID", os.getenv("KG_MODEL_ID", "google/gemma-4-e4b")),
            ),
            "base_url": os.getenv(
                "TARGET_DEVELOPMENT_BASE_URL",
                os.getenv(
                    "PRIMROSE_GEMMA4_BASE_URL",
                    os.getenv("KG_LM_STUDIO_URL", "http://127.0.0.1:1234/v1"),
                ),
            ),
            "api_key": os.getenv(
                "TARGET_DEVELOPMENT_API_KEY",
                os.getenv(
                    "PRIMROSE_GEMMA4_API_KEY",
                    os.getenv("KG_LM_STUDIO_API_KEY", "lm-studio"),
                ),
            ),
        }

    @staticmethod
    def _endpoint_reachable(base_url: str, timeout: float = 0.6) -> bool:
        parsed = urllib.parse.urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def status(self) -> dict[str, Any]:
        config = self.config()
        modules = {
            "langchain_openai": importlib.util.find_spec("langchain_openai") is not None,
            "langchain_core": importlib.util.find_spec("langchain_core") is not None,
        }
        dependencies = all(modules.values()) or self._generator_factory is not None
        endpoint = (
            True
            if self._generator_factory is not None
            else dependencies and self._endpoint_reachable(config["base_url"])
        )
        return {
            "status": "ready" if dependencies and endpoint else "unavailable",
            "available": bool(dependencies and endpoint),
            "mode": "asynchronous-model-generation",
            "model_id": config["model_id"],
            "base_url": config["base_url"],
            "dependencies": modules,
            "endpoint_reachable": bool(endpoint),
            "provider_contract": "OpenAI-compatible chat completions",
            "credential_configured": bool(config["api_key"]),
            "reason": (
                "Ready to generate section narratives."
                if dependencies and endpoint
                else "Install the optional LangChain dependencies and connect the configured model endpoint."
            ),
        }

    # -------------------------------------------------------------- persistence

    def _path(self, job_id: str) -> Path:
        return self.output_dir / f"{job_id}.json"

    def _persist(self, job: dict[str, Any]) -> None:
        path = self._path(job["id"])
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    def _load_existing(self) -> None:
        for path in sorted(self.output_dir.glob("*.json")):
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
                    message="The backend restarted before this generation completed.",
                    error="interrupted",
                )
                self._persist(job)
            self._jobs[job["id"]] = job

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(values, updated_at=_stamp())
            self._persist(job)

    # ------------------------------------------------------------------- API

    def submit(
        self,
        target_id: str,
        *,
        phase: str = "basic",
        depth: int = 2,
        limit: int = 90,
    ) -> dict[str, Any]:
        target_id = str(target_id).strip()
        if not target_id:
            raise ValueError("target_id is required")
        if self.store.resolve_node(target_id) != target_id:
            raise ValueError("target_id must be an exact stable entity id")
        if phase not in DOCTRINE:
            raise ValueError(f"phase must be one of: {', '.join(sorted(DOCTRINE))}")
        depth = max(1, min(int(depth), 3))
        limit = max(10, min(int(limit), 150))

        item = self.store.item("node", target_id)
        screening = screen_entity(item)
        if item["status"] == "rejected":
            raise TargetScreeningError("Rejected graph records cannot enter target development.")
        if screening.verdict != VERDICT_DEVELOPABLE:
            raise TargetScreeningError(screening.rationale)

        model_status = self.status()
        if not model_status["available"]:
            raise RuntimeError(model_status["reason"])

        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "target_id": target_id,
            "target_label": item["label"],
            "phase": phase,
            "depth": depth,
            "limit": limit,
            "status": "queued",
            "created_at": _stamp(),
            "updated_at": _stamp(),
            "message": "Waiting for the target-development model worker.",
            "progress": {
                "completed_sections": 0,
                "total_sections": len(DOCTRINE[phase]["sections"]),
            },
            "model": {
                "model_id": model_status["model_id"],
                "base_url": model_status["base_url"],
                "provider_contract": model_status["provider_contract"],
            },
            "result": None,
            "error": "",
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        self._executor.submit(self._run, job_id)
        return deepcopy(job)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = deepcopy(self._jobs[job_id])
        self._update(job_id, status="running", message="Generating section narratives.")
        config = self.config()
        try:
            factory = self._generator_factory or (
                lambda value: default_generator(
                    value["model_id"], value["base_url"], value["api_key"]
                )
            )
            generator = factory(config)
            completed_sections = 0

            def tracked_generator(prompt: dict[str, str]) -> str:
                nonlocal completed_sections
                try:
                    return generator(prompt)
                finally:
                    completed_sections += 1
                    total = len(DOCTRINE[job["phase"]]["sections"])
                    self._update(
                        job_id,
                        message=f"Generated section {completed_sections} of {total}.",
                        progress={
                            "completed_sections": completed_sections,
                            "total_sections": total,
                        },
                    )
            record = develop(
                self.store,
                job["target_id"],
                phase=job["phase"],
                generator=tracked_generator,
                depth=job["depth"],
                limit=job["limit"],
                allow_personnel=False,
            )
            # Prompts and the duplicated evidence block are intentionally not
            # returned through the normal API.  Graph facts and per-section
            # narratives remain available for review and export.
            record.get("evidence", {}).pop("block", None)
            for section in record.get("sections", []):
                section.pop("prompt", None)
            errors = [section["error"] for section in record["sections"] if section.get("error")]
            status = "completed_with_errors" if errors else "completed"
            self._update(
                job_id,
                status=status,
                message=(
                    f"Generation completed with {len(errors)} section error(s)."
                    if errors
                    else "Generation completed."
                ),
                result=record,
                error="\n".join(errors),
            )
        except Exception as exc:  # provider and protected-core boundary
            self._update(
                job_id,
                status="failed",
                message="Target-development generation failed.",
                error=f"{type(exc).__name__}: {exc}",
            )

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return deepcopy(self._jobs[job_id])

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = sorted(
                self._jobs.values(),
                key=lambda item: (item.get("created_at", ""), item["id"]),
                reverse=True,
            )
            return [deepcopy(item) for item in rows[:limit]]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
