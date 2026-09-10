# Owner-only backend deployment

This container runs the restored Python application behind a small
infrastructure authentication boundary. Its extraction and graph-reasoning
workflow is preserved; narrow fail-closed integrity and hosting controls are
documented in `docs/ARCHITECTURE.md`. Graph state, review decisions, uploads,
extraction outputs, backups, and ontology selection persist in the named Docker
volume mounted at `/data`.

The browser uses the same Python contracts as the local application: PDF/text
ingestion, schema scoping, review, GraphRAG, PRIMROSE diagnostics and asynchronous
target-development generation. Model endpoints must implement an OpenAI-compatible
chat-completions interface; native provider protocols need an adapter.

## Run

1. Copy `deploy/backend.env.example` to `deploy/backend.env`.
2. Replace the placeholder model identifier and token. The token must contain at
   least 32 characters.
3. Start the backend with `docker compose -f compose.owner.yml up --build -d`.
4. Confirm `http://127.0.0.1:8080/healthz` returns a minimal healthy response.
5. Publish port 8080 only through an authenticated HTTPS tunnel. The Compose
   file deliberately binds it to loopback and does not expose it to the LAN.
6. Configure the Site with the tunnel HTTPS URL as `PRIMROSE_BACKEND_URL` and
   the matching `PRIMROSE_BACKEND_TOKEN` as a secret, then redeploy the Site.

The Site can instead use a registered private HTTP binding named
`primrose_backend`. At deployment that appears to the Worker as
`CUSTOMER_HTTP_PRIMROSE_BACKEND`, and no public backend URL is required.

## Security boundary

- `/healthz` is intentionally minimal and unauthenticated for container health
  checks.
- Every other backend route requires the bearer token.
- The browser never receives that token; the same-origin Site proxy adds it.
- Never publish the raw container port directly to the internet.
- Model credentials stay in `deploy/backend.env` on the Python host, not in the
  browser or Site configuration.

## Optional full reasoning stack

The default image installs the packages required by PDF ingestion, schema-guided
extraction, GraphRAG, and the served graph metrics. The spaCy, FAISS,
sentence-transformer, Neo4j, pandas, and PyVis experiments are not imported by
the web server. To include them, set `PRIMROSE_INSTALL_FULL_REASONING` to `true`
in `compose.owner.yml`; expect a much larger image.
