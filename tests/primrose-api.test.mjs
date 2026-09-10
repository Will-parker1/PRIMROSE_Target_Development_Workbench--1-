import assert from "node:assert/strict";
import test from "node:test";

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("proxy-test", `${process.pid}-${Date.now()}-${Math.random()}`);
  return (await import(workerUrl.href)).default;
}

function environment(backend, extra = {}) {
  return {
    ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
    ...(backend ? { CUSTOMER_HTTP_PRIMROSE_BACKEND: { fetch: backend } } : {}),
    ...extra,
  };
}

const executionContext = { waitUntil() {}, passThroughOnException() {} };

test("fails closed when no Python backend is configured", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/health"),
    environment(null),
    executionContext,
  );
  assert.equal(response.status, 503);
  const payload = await response.json();
  assert.equal(payload.error, "Python backend unavailable");
  assert.doesNotMatch(JSON.stringify(payload), /synthetic|demo|snapshot/i);
});

test("proxies same-origin requests through the private binding with an allowlist", async () => {
  let received;
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/summary?limit=2", {
      headers: {
        accept: "application/json",
        cookie: "must-not-leave-the-site=1",
        origin: "http://localhost",
        "oai-authenticated-user-email": "analyst@example.test",
        "x-untrusted-header": "drop-me",
      },
    }),
    environment(
      async (request) => {
        received = request;
        return new Response('{"status":"ok"}', {
          headers: {
            "content-disposition": 'attachment; filename="summary.json"',
            "content-type": "application/json",
            server: "do-not-expose",
            "set-cookie": "backend-cookie=1",
          },
        });
      },
      { PRIMROSE_BACKEND_TOKEN: "test-backend-secret" },
    ),
    executionContext,
  );

  assert.equal(response.status, 200);
  assert.ok(received);
  assert.equal(received.url, "https://primrose-backend.internal/api/summary?limit=2");
  assert.equal(received.headers.get("authorization"), "Bearer test-backend-secret");
  assert.equal(received.headers.get("x-primrose-authenticated-user"), "analyst@example.test");
  assert.equal(received.headers.get("x-primrose-proxy"), "sites");
  assert.equal(received.headers.get("cookie"), null);
  assert.equal(received.headers.get("x-untrusted-header"), null);
  assert.equal(response.headers.get("content-disposition"), 'attachment; filename="summary.json"');
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("server"), null);
  assert.equal(response.headers.get("x-primrose-source"), "python-backend");
  assert.deepEqual(await response.json(), { status: "ok" });
});

test("uses a validated HTTPS backend URL when no private binding exists", async () => {
  const originalFetch = globalThis.fetch;
  let received;
  globalThis.fetch = async (request) => {
    received = request;
    return Response.json({ results: [] });
  };
  try {
    const worker = await loadWorker();
    const response = await worker.fetch(
      new Request("https://site.example/api/search?q=relay", {
        headers: { origin: "https://site.example" },
      }),
      environment(null, {
        PRIMROSE_BACKEND_URL: "https://backend.example",
        PRIMROSE_BACKEND_TOKEN: "public-endpoint-secret",
      }),
      executionContext,
    );
    assert.equal(response.status, 200);
    assert.equal(received.url, "https://backend.example/api/search?q=relay");
    assert.equal(received.headers.get("authorization"), "Bearer public-endpoint-secret");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects a non-loopback plaintext backend URL", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("https://site.example/api/health"),
    environment(null, { PRIMROSE_BACKEND_URL: "http://backend.example" }),
    executionContext,
  );
  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /No valid Python backend connection/);
});

test("preserves PDF bytes, filename and extraction mode", async () => {
  const original = Uint8Array.from([
    0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37, 0x0a, 0x00, 0xff, 0x80, 0x0a,
  ]);
  let receivedBytes;
  let receivedFilename;
  let receivedMode;
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/extractions?mode=schema", {
      method: "POST",
      headers: {
        "content-type": "application/pdf",
        origin: "http://localhost",
        "x-extraction-mode": "schema",
        "x-filename": "../packet name.pdf",
      },
      body: original,
    }),
    environment(async (request) => {
      receivedBytes = new Uint8Array(await request.arrayBuffer());
      receivedFilename = request.headers.get("x-filename");
      receivedMode = request.headers.get("x-extraction-mode");
      return Response.json({ status: "queued" }, { status: 202 });
    }),
    executionContext,
  );

  assert.equal(response.status, 202);
  assert.deepEqual(receivedBytes, original);
  assert.equal(receivedFilename, "packet_name.pdf");
  assert.equal(receivedMode, "schema");
});

test("rejects cross-origin state-changing requests before reaching the backend", async () => {
  let calls = 0;
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/review", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://attacker.invalid" },
      body: "{}",
    }),
    environment(async () => {
      calls += 1;
      return Response.json({ unexpected: true });
    }),
    executionContext,
  );
  assert.equal(response.status, 403);
  assert.equal(calls, 0);
});

test("rejects declared request bodies larger than 25 MiB without forwarding them", async () => {
  let calls = 0;
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/extractions", {
      method: "POST",
      headers: {
        "content-length": String(25 * 1024 * 1024 + 1),
        "content-type": "application/pdf",
        origin: "http://localhost",
      },
      body: new Uint8Array([0x25, 0x50, 0x44, 0x46]),
    }),
    environment(async () => {
      calls += 1;
      return Response.json({ unexpected: true });
    }),
    executionContext,
  );
  assert.equal(response.status, 413);
  assert.equal(calls, 0);
});

test("preserves the original 30 MiB graph-import allowance", async () => {
  let calls = 0;
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/graph/import?mode=merge", {
      method: "POST",
      headers: {
        "content-length": String(29 * 1024 * 1024),
        "content-type": "application/json",
        origin: "http://localhost",
      },
      body: "{}",
    }),
    environment(async () => {
      calls += 1;
      return Response.json({ status: "imported" });
    }),
    executionContext,
  );
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
});
