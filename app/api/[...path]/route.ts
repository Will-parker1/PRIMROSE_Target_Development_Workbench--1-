import { getRuntimeBindings } from "@/app/lib/runtime-bindings";

export const dynamic = "force-dynamic";

const MAX_UPLOAD_BODY_BYTES = 25 * 1024 * 1024;
const MAX_API_BODY_BYTES = 30 * 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 20_000;
const UPLOAD_TIMEOUT_MS = 60_000;
const EXPORT_TIMEOUT_MS = 60_000;
const MODEL_TIMEOUT_MS = 150_000;

const REQUEST_HEADER_ALLOWLIST = new Set([
  "accept",
  "accept-language",
  "content-type",
  "if-none-match",
  "x-extraction-mode",
  "x-filename",
]);

const RESPONSE_HEADER_ALLOWLIST = new Set([
  "content-disposition",
  "content-type",
  "etag",
  "last-modified",
]);

function runtimeValue(key: string): string {
  const bindingValue = getRuntimeBindings()[key];
  if (typeof bindingValue === "string") return bindingValue.trim();
  return process.env[key]?.trim() ?? "";
}

function jsonError(status: number, error: string, detail: string) {
  return Response.json(
    { error, detail, status },
    {
      status,
      headers: {
        "cache-control": "no-store",
        "content-type": "application/json; charset=utf-8",
        "x-content-type-options": "nosniff",
      },
    },
  );
}

function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).origin === new URL(request.url).origin;
  } catch {
    return false;
  }
}

function safeFilename(value: string): string {
  const basename = value.split(/[\\/]/).pop() ?? "document";
  return basename.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^\.+/, "").slice(0, 120) || "document";
}

function timeoutFor(path: string[], method: string): number {
  const route = path.join("/");
  if (method === "POST" && route === "extractions") return UPLOAD_TIMEOUT_MS;
  if (route === "query" || route === "workbench/explanations" || route === "schema/suggest-llm") {
    return MODEL_TIMEOUT_MS;
  }
  if (route.startsWith("export/") || route === "graph/import") return EXPORT_TIMEOUT_MS;
  return DEFAULT_TIMEOUT_MS;
}

function publicBackendTarget(path: string[], requestUrl: URL): URL | null {
  const configured = runtimeValue("PRIMROSE_BACKEND_URL");
  if (!configured) return null;

  let backend: URL;
  try {
    backend = new URL(configured);
  } catch {
    throw new Error("invalid_backend_configuration");
  }
  const loopback = new Set(["127.0.0.1", "localhost", "::1"]).has(backend.hostname);
  if (backend.protocol !== "https:" && !(backend.protocol === "http:" && loopback)) {
    throw new Error("insecure_backend_configuration");
  }
  if (backend.username || backend.password) {
    throw new Error("credentialed_backend_url");
  }

  const target = new URL(`/api/${path.map(encodeURIComponent).join("/")}`, backend);
  target.search = requestUrl.search;
  return target;
}

function internalBackendTarget(path: string[], requestUrl: URL): URL {
  const target = new URL(
    `/api/${path.map(encodeURIComponent).join("/")}`,
    "https://primrose-backend.internal",
  );
  target.search = requestUrl.search;
  return target;
}

function requestHeaders(request: Request): Headers {
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    const lower = name.toLowerCase();
    if (!REQUEST_HEADER_ALLOWLIST.has(lower)) continue;
    if (lower === "x-filename") {
      headers.set(lower, safeFilename(value));
    } else if (lower === "x-extraction-mode") {
      const mode = value.trim().toLowerCase();
      if (mode === "schema" || mode === "heuristic") headers.set(lower, mode);
    } else {
      headers.set(lower, value);
    }
  }

  const token = runtimeValue("PRIMROSE_BACKEND_TOKEN");
  if (token) headers.set("authorization", `Bearer ${token}`);

  const analyst = request.headers.get("oai-authenticated-user-email")?.trim();
  if (analyst) headers.set("x-primrose-authenticated-user", analyst.slice(0, 320));
  headers.set("x-primrose-proxy", "sites");
  return headers;
}

function responseHeaders(response: Response): Headers {
  const headers = new Headers({
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-primrose-source": "python-backend",
  });
  for (const [name, value] of response.headers) {
    const lower = name.toLowerCase();
    if (!RESPONSE_HEADER_ALLOWLIST.has(lower)) continue;
    headers.set(lower, value.replace(/[\r\n]/g, ""));
  }
  if (!headers.has("content-type")) {
    headers.set("content-type", "application/octet-stream");
  }
  return headers;
}

function bodyLimitFor(path: string[]): number {
  return path.join("/") === "extractions" ? MAX_UPLOAD_BODY_BYTES : MAX_API_BODY_BYTES;
}

async function bodyBytes(request: Request, path: string[]): Promise<ArrayBuffer | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const limit = bodyLimitFor(path);
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > limit) {
    throw new RangeError("request_too_large");
  }
  const bytes = await request.arrayBuffer();
  if (bytes.byteLength > limit) throw new RangeError("request_too_large");
  return bytes;
}

async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  if (!isSameOrigin(request)) {
    return jsonError(403, "Request forbidden", "Cross-origin API requests are not allowed.");
  }

  const { path } = await context.params;
  if (!Array.isArray(path) || path.length === 0) {
    return jsonError(404, "API route not found", "No backend API path was supplied.");
  }

  const privateBackend = getRuntimeBindings().CUSTOMER_HTTP_PRIMROSE_BACKEND;
  const incomingUrl = new URL(request.url);
  let target: URL;
  try {
    target = privateBackend
      ? internalBackendTarget(path, incomingUrl)
      : publicBackendTarget(path, incomingUrl) ?? (() => {
          throw new Error("backend_not_configured");
        })();
  } catch {
    return jsonError(
      503,
      "Python backend unavailable",
      "No valid Python backend connection is configured for this deployment.",
    );
  }

  let body: ArrayBuffer | undefined;
  try {
    body = await bodyBytes(request, path);
  } catch (error) {
    if (error instanceof RangeError) {
      const limitMiB = bodyLimitFor(path) / (1024 * 1024);
      return jsonError(413, "Request too large", `This request is limited to ${limitMiB} MiB.`);
    }
    return jsonError(400, "Invalid request", "The request body could not be read.");
  }

  const outbound = new Request(target, {
    method: request.method,
    headers: requestHeaders(request),
    body,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(timeoutFor(path, request.method)),
  });

  try {
    const response = privateBackend
      ? await privateBackend.fetch(outbound)
      : await fetch(outbound);
    return new Response(request.method === "HEAD" ? null : response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders(response),
    });
  } catch {
    return jsonError(
      503,
      "Python backend unavailable",
      "The configured Python backend did not complete the request.",
    );
  }
}

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxy(request, context);
}

export async function HEAD(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxy(request, context);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxy(request, context);
}
