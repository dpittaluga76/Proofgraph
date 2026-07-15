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

export type PatchDecision = {
  decision_id: string;
  operation_index: number;
  decision: "accepted" | "rejected" | "skipped_conflict";
  reason: string | null;
  actor_type: string;
  actor_id: string | null;
  graph_operation_id: string | null;
  decided_at: string;
};

export type QualityDimension = {
  rating: string;
  rationale: string;
};

export type PatchOperationReview = {
  change_type: "addition" | "update" | "deletion" | "position";
  entity_type: "node" | "edge";
  semantic_role: string | null;
  title: string | null;
  provenance_node_ids: string[];
  assumptions: Array<Record<string, unknown>>;
  risks: Array<Record<string, unknown>>;
  contradiction: unknown;
  quality_dimensions: Record<string, QualityDimension> | null;
  distribution_rationale: unknown;
  defensibility_rationale: unknown;
};

export type PatchOperationCandidate = {
  operation_index: number;
  operation_id: string;
  candidate: Record<string, unknown>;
  dependency_operation_ids: string[];
  dependency_operation_indices: number[];
  missing_dependency_operation_ids: string[];
  review: PatchOperationReview;
};

export type GraphPatch = {
  patch_id: string;
  run_id: string;
  canvas_id: string;
  base_canvas_revision: number;
  status: "pending" | "applied" | "partially_applied" | "rejected";
  operations: PatchOperationCandidate[];
  regeneration_target_ids: string[];
  permitted_stale_resolution_ids: string[];
  client_id_map: Record<string, string>;
  decisions: PatchDecision[];
  regenerated_by_run_id: string | null;
  created_at: string;
  decided_at: string | null;
  applied_at: string | null;
};

export type PatchApplyResult = {
  patch: GraphPatch;
  canvas_revision: number;
  client_id_map: Record<string, string>;
  conflicts: Array<{
    operation_id: string;
    code: string;
    message: string;
    details: unknown;
  }>;
};

export type GenerationOperation =
  | "generate_strategies"
  | "research_evidence"
  | "synthesize_opportunities"
  | "regenerate_stale";

export type GenerationRunStatus =
  "queued" | "running" | "patch_ready" | "completed" | "failed" | "cancelled";

export type GenerationRunError = {
  code: string;
  message: string;
  retryable: boolean;
  stage: string | null;
  details: Record<string, unknown>;
};

export type GenerationRun = {
  run_id: string;
  canvas_id: string;
  operation: GenerationOperation;
  status: GenerationRunStatus;
  current_stage: string | null;
  attempt: number;
  max_attempts: number;
  cancellation_state: "not_requested" | "requested" | "cancelled";
  error: GenerationRunError | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  ready_patch_id: string | null;
};

export type GenerationRunRequest = {
  operation: GenerationOperation;
  selected_node_ids: string[];
  expected_node_versions: Record<string, number>;
  instruction: string | null;
  execution_profile_id: "demo_hybrid_v1" | "replay_v1";
  idempotency_key: string;
  regeneration_scope: "node" | "branch" | null;
};

export type GenerationRunCreated = {
  run_id: string;
  status: GenerationRunStatus;
  events_url: string;
};

export type RuntimeStatus = {
  status: "ok";
  database: "ok";
  demo_mode: boolean;
};

export type DemoSession = {
  expires_at: string;
  hybrid_run_count: number;
  hybrid_run_limit: number;
  primary_profile: "demo_hybrid_v1";
  fallback_profile: "replay_v1";
};

export type DemoBootstrap = {
  session: DemoSession;
  canvas: GraphCanvas;
};

export type GenerationEvent = {
  run_id: string;
  canvas_sequence: number;
  run_sequence: number;
  event_type:
    | "run.started"
    | "run.resumed"
    | "run.retry_requested"
    | "stage.started"
    | "stage.progress"
    | "research.query_created"
    | "research.source_found"
    | "evidence.extracted"
    | "candidate.generated"
    | "candidate.critiqued"
    | "patch.ready"
    | "run.completed"
    | "run.failed"
    | "run.cancelled";
  payload: Record<string, unknown>;
  timestamp: string;
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

let demoBootstrapInFlight: Promise<DemoBootstrap> | null = null;

export async function checkRuntime(): Promise<RuntimeStatus> {
  return request<RuntimeStatus>("/api/health");
}

export async function bootstrapDemo(): Promise<DemoBootstrap> {
  demoBootstrapInFlight ??= request<DemoBootstrap>("/api/demo/bootstrap");
  try {
    return await demoBootstrapInFlight;
  } finally {
    demoBootstrapInFlight = null;
  }
}

export async function resetDemo(): Promise<DemoBootstrap> {
  return request<DemoBootstrap>("/api/demo/reset", {
    method: "POST",
    body: JSON.stringify({}),
  });
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

export async function createGenerationRun(
  canvasId: string,
  input: GenerationRunRequest,
): Promise<GenerationRunCreated> {
  return request<GenerationRunCreated>(
    `/api/canvases/${encodeURIComponent(canvasId)}/generation-runs`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function getGenerationRun(runId: string): Promise<GenerationRun> {
  return request<GenerationRun>(
    `/api/generation-runs/${encodeURIComponent(runId)}`,
  );
}

export async function cancelGenerationRun(
  runId: string,
): Promise<GenerationRun> {
  return request<GenerationRun>(
    `/api/generation-runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export async function retryGenerationRun(
  runId: string,
): Promise<GenerationRun> {
  return request<GenerationRun>(
    `/api/generation-runs/${encodeURIComponent(runId)}/retry`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export async function getGraphPatch(patchId: string): Promise<GraphPatch> {
  const response = await request<{ patch: GraphPatch }>(
    `/api/graph-patches/${encodeURIComponent(patchId)}`,
  );
  return response.patch;
}

export async function rejectGraphPatch(patchId: string): Promise<GraphPatch> {
  const response = await request<{ patch: GraphPatch }>(
    `/api/graph-patches/${encodeURIComponent(patchId)}/reject`,
    { method: "POST", body: JSON.stringify({}) },
  );
  return response.patch;
}

export async function applyGraphPatch(
  patchId: string,
  selectedOperationIds: string[] | null,
  applyNonconflictingOnly = false,
): Promise<PatchApplyResult> {
  return request<PatchApplyResult>(
    `/api/graph-patches/${encodeURIComponent(patchId)}/apply`,
    {
      method: "POST",
      body: JSON.stringify({
        selected_operation_ids: selectedOperationIds,
        apply_nonconflicting_only: applyNonconflictingOnly,
      }),
    },
  );
}

export async function regenerateGraphPatch(
  patchId: string,
  instruction: string,
  idempotencyKey: string,
): Promise<{ patch: GraphPatch; regeneration_run: { run_id: string } }> {
  return request<{
    patch: GraphPatch;
    regeneration_run: { run_id: string };
  }>(`/api/graph-patches/${encodeURIComponent(patchId)}/regenerate`, {
    method: "POST",
    body: JSON.stringify({
      instruction,
      idempotency_key: idempotencyKey,
    }),
  });
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
  stale_node_ids?: string[];
  newly_stale_node_ids?: string[];
};

export type EdgeOperationResult = {
  canvas_revision: number;
  edge: GraphEdge;
  stale_node_ids?: string[];
  newly_stale_node_ids?: string[];
};

export type DeleteNodeResult = {
  canvas_revision: number;
  deleted_node_id: string;
  stale_node_ids?: string[];
  newly_stale_node_ids?: string[];
};

export type DeleteEdgeResult = {
  canvas_revision: number;
  deleted_edge_id: string;
  stale_node_ids?: string[];
  newly_stale_node_ids?: string[];
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
