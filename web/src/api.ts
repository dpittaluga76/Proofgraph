import type { GraphCanvas, GraphEdge, GraphNode } from "./graph";

type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
};

export type DependencyConflictDetails = {
  incident_edges: Array<{ id: string; version: number }>;
  referencing_constraints: Array<{ id: string; version: number }>;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.error?.message ?? `Request failed with status ${status}.`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.error?.code ?? "request_failed";
    this.details = payload.error?.details;
  }
}

function csrfToken(): string | null {
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : null;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retryNetworkOnce = false,
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const token = csrfToken();
    if (token) {
      headers.set("X-CSRFToken", token);
    }
  }

  const requestInit: RequestInit = {
    ...init,
    credentials: "same-origin",
    headers,
  };
  let response: Response;
  try {
    response = await fetch(path, requestInit);
  } catch (error) {
    if (!retryNetworkOnce) throw error;
    response = await fetch(path, requestInit);
  }
  if (!response.ok) {
    let payload: ApiErrorPayload = {};
    try {
      payload = (await response.json()) as ApiErrorPayload;
    } catch {
      // Preserve the status-only fallback when a proxy returns a non-JSON error.
    }
    throw new ApiError(response.status, payload);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function checkRuntime(): Promise<void> {
  await request<{ status: "ok"; database: "ok" }>("/api/health");
}

export async function createCanvas(title: string): Promise<GraphCanvas> {
  const response = await request<{ canvas: GraphCanvas }>("/api/canvases", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  return response.canvas;
}

export async function getCanvas(canvasId: string): Promise<GraphCanvas> {
  const response = await request<{ canvas: GraphCanvas }>(
    `/api/canvases/${encodeURIComponent(canvasId)}`,
  );
  clearUnresolvedOperations(canvasId);
  return response.canvas;
}

export async function renameCanvas(
  canvasId: string,
  title: string,
): Promise<GraphCanvas> {
  const response = await request<{ canvas: GraphCanvas }>(
    `/api/canvases/${encodeURIComponent(canvasId)}`,
    { method: "PATCH", body: JSON.stringify({ title }) },
  );
  return response.canvas;
}

export type GraphOperation = Record<string, unknown> & {
  op: string;
  operation_key: string;
};

type UnresolvedOperation = { canvasId: string; operation: GraphOperation };

const unresolvedOperations = new Map<string, UnresolvedOperation>();

function clearUnresolvedOperations(canvasId: string): void {
  for (const [fingerprint, unresolved] of unresolvedOperations) {
    if (unresolved.canvasId === canvasId)
      unresolvedOperations.delete(fingerprint);
  }
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (typeof value !== "object" || value === null) return value;

  const object = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.keys(object)
      .sort()
      .map((key) => [key, canonicalize(object[key])]),
  );
}

function operationFingerprint(
  canvasId: string,
  operation: GraphOperation,
): string {
  const payload = Object.fromEntries(
    Object.entries(operation).filter(([key]) => key !== "operation_key"),
  );
  return JSON.stringify(canonicalize({ canvasId, payload }));
}

export type NodeOperationResult = {
  canvas_revision: number;
  node: GraphNode;
};

export type EdgeOperationResult = {
  canvas_revision: number;
  edge: GraphEdge;
};

export type DeleteNodeResult = {
  canvas_revision: number;
  deleted_node_id: string;
};

export type DeleteEdgeResult = {
  canvas_revision: number;
  deleted_edge_id: string;
};

export async function applyOperation<T>(
  canvasId: string,
  operation: GraphOperation,
): Promise<T> {
  const fingerprint = operationFingerprint(canvasId, operation);
  const requestOperation =
    unresolvedOperations.get(fingerprint)?.operation ?? operation;
  unresolvedOperations.set(fingerprint, {
    canvasId,
    operation: requestOperation,
  });
  try {
    const result = await request<T>(
      `/api/canvases/${encodeURIComponent(canvasId)}/operations`,
      { method: "POST", body: JSON.stringify(requestOperation) },
      true,
    );
    unresolvedOperations.delete(fingerprint);
    return result;
  } catch (error) {
    if (error instanceof ApiError) unresolvedOperations.delete(fingerprint);
    throw error;
  }
}

export function operationKey(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
    /[xy]/g,
    (character) => {
      const random = Math.floor(Math.random() * 16);
      const value = character === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    },
  );
}
