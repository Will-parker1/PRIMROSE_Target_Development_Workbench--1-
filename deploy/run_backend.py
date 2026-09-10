#!/usr/bin/env python3
"""Authenticated, persistent container entry point for the existing Python app."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from projects import ProjectRegistry  # noqa: E402
from server import Application, make_handler  # noqa: E402


def configured_path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser().resolve()


def initialise_graph(graph_path: Path) -> None:
    """Seed a new persistent volume without replacing an existing live graph."""
    json_sidecar = graph_path.with_suffix(".json")
    if graph_path.exists() or json_sidecar.exists():
        return
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BACKEND_ROOT / "graph.graphml", graph_path)


def main() -> None:
    token = os.getenv("PRIMROSE_BACKEND_TOKEN", "").strip()
    if len(token) < 32:
        raise SystemExit(
            "PRIMROSE_BACKEND_TOKEN must be configured with at least 32 characters; "
            "the container refuses unauthenticated startup."
        )

    data_root = configured_path("PRIMROSE_DATA_DIR", Path("/data"))
    graph_path = configured_path("PRIMROSE_GRAPH_PATH", data_root / "graph.graphml")
    state_path = configured_path(
        "PRIMROSE_STATE_PATH", data_root / "runtime" / "review_state.json"
    )
    schema_path = configured_path(
        "PRIMROSE_SCHEMA_PATH",
        BACKEND_ROOT / "UK_NATO_Targeting_LangExtract_Gemma_Context_IESv5_v1.0.json",
    )
    initialise_graph(graph_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    projects_root = configured_path(
        "PRIMROSE_PROJECTS_DIR", data_root / "runtime" / "projects"
    )

    application = Application(graph_path, state_path, schema_path)
    registry = ProjectRegistry(
        application,
        "Default",
        projects_root,
        schema_path,
        application_factory=Application,
    )
    base_handler = make_handler(registry, BACKEND_ROOT / "web")

    class AuthenticatedHandler(base_handler):
        def _is_health_check(self) -> bool:
            return urllib.parse.urlparse(self.path).path.rstrip("/") == "/healthz"

        def _authorised(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            scheme, separator, credential = supplied.partition(" ")
            return bool(
                separator
                and scheme.casefold() == "bearer"
                and hmac.compare_digest(credential.strip(), token)
            )

        def _require_authorisation(self) -> bool:
            if self._authorised():
                return True
            self._error(
                HTTPStatus.UNAUTHORIZED,
                "Backend authentication required",
                "Use the authenticated PRIMROSE Sites proxy.",
            )
            return False

        def do_GET(self) -> None:  # noqa: N802
            if self._is_health_check():
                self._json({"status": "ok", "service": "primrose-python-backend"})
                return
            if self._require_authorisation():
                super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            if self._is_health_check():
                self._bytes(b"", content_type="application/json; charset=utf-8")
                return
            if self._require_authorisation():
                super().do_HEAD()

        def do_POST(self) -> None:  # noqa: N802
            if self._require_authorisation():
                super().do_POST()

    host = os.getenv("PRIMROSE_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), AuthenticatedHandler)
    print(
        f"PRIMROSE Python backend listening on {host}:{port}; "
        f"persistent data: {data_root}",
        flush=True,
    )

    def stop(_signum: int, _frame: object) -> None:
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
