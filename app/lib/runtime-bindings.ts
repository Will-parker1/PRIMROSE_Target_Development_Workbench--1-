import { AsyncLocalStorage } from "node:async_hooks";

export type RuntimeBindings = Record<string, unknown> & {
  CUSTOMER_HTTP_PRIMROSE_BACKEND?: {
    fetch(input: Request): Promise<Response>;
  };
};

const storage = new AsyncLocalStorage<RuntimeBindings>();

export function withRuntimeBindings<T>(
  bindings: RuntimeBindings,
  operation: () => T,
): T {
  return storage.run(bindings, operation);
}

export function getRuntimeBindings(): RuntimeBindings {
  return storage.getStore() ?? {};
}

