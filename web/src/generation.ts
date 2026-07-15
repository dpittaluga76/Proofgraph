import type {
  GenerationEvent,
  GenerationRun,
  GenerationRunStatus,
} from "./api";

export type ProgressConnection =
  "idle" | "connecting" | "connected" | "disconnected";

export type ProvisionalSource = {
  source_id: string;
  url: string | null;
  sanitized_excerpt: string | null;
  cache_hit: boolean;
};

export type ProvisionalClaim = {
  claim_id: string;
  claim: string | null;
  classification: string | null;
  evidence_type: string | null;
  strength: string | null;
  source_ids: string[];
};

export type RunProgress = {
  run_id: string;
  status: GenerationRunStatus;
  current_stage: string | null;
  attempt: number;
  max_attempts: number;
  cancellation_state: "not_requested" | "requested" | "cancelled";
  error: GenerationRun["error"];
  patch_id: string | null;
  provisional_sources: ProvisionalSource[];
  provisional_claims: ProvisionalClaim[];
  last_canvas_sequence: number;
};

export type GenerationProgressState = {
  canvas_id: string | null;
  cursor: number;
  connection: ProgressConnection;
  runs: Record<string, RunProgress>;
};

export type GenerationProgressAction =
  | { type: "reset"; canvasId: string | null }
  | { type: "connection"; connection: ProgressConnection }
  | {
      type: "track";
      runId: string;
      status?: GenerationRunStatus;
    }
  | { type: "reconcile"; run: GenerationRun }
  | { type: "event"; event: GenerationEvent };

export const INITIAL_GENERATION_PROGRESS: GenerationProgressState = {
  canvas_id: null,
  cursor: 0,
  connection: "idle",
  runs: {},
};

function emptyRun(
  runId: string,
  status: GenerationRunStatus = "queued",
): RunProgress {
  return {
    run_id: runId,
    status,
    current_stage: null,
    attempt: 0,
    max_attempts: 3,
    cancellation_state: "not_requested",
    error: null,
    patch_id: null,
    provisional_sources: [],
    provisional_claims: [],
    last_canvas_sequence: 0,
  };
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function upsertById<T, K extends keyof T>(items: T[], key: K, item: T): T[] {
  const index = items.findIndex((candidate) => candidate[key] === item[key]);
  if (index < 0) return [...items, item];
  return items.map((candidate, candidateIndex) =>
    candidateIndex === index ? item : candidate,
  );
}

function applyEvent(run: RunProgress, event: GenerationEvent): RunProgress {
  const payload = event.payload;
  const next = {
    ...run,
    last_canvas_sequence: event.canvas_sequence,
  };

  switch (event.event_type) {
    case "run.started":
    case "run.resumed":
      return {
        ...next,
        status: "running",
        attempt: numberValue(payload.attempt, next.attempt),
        cancellation_state: "not_requested",
        error: null,
      };
    case "run.retry_requested":
      return {
        ...next,
        status: "queued",
        current_stage: null,
        cancellation_state: "not_requested",
        error: null,
      };
    case "stage.started":
      return {
        ...next,
        status: "running",
        current_stage: stringValue(payload.stage),
      };
    case "stage.progress":
      return {
        ...next,
        current_stage: stringValue(payload.stage) ?? next.current_stage,
      };
    case "research.source_found": {
      const sourceId = stringValue(payload.source_id);
      if (!sourceId || payload.provisional !== true) return next;
      const source: ProvisionalSource = {
        source_id: sourceId,
        url: stringValue(payload.url),
        sanitized_excerpt: stringValue(payload.sanitized_excerpt),
        cache_hit: payload.cache_hit === true,
      };
      return {
        ...next,
        provisional_sources: upsertById(
          next.provisional_sources,
          "source_id",
          source,
        ),
      };
    }
    case "evidence.extracted": {
      const claimId = stringValue(payload.claim_id);
      if (!claimId || payload.provisional !== true) return next;
      const claim: ProvisionalClaim = {
        claim_id: claimId,
        claim: stringValue(payload.claim),
        classification: stringValue(payload.classification),
        evidence_type: stringValue(payload.evidence_type),
        strength: stringValue(payload.strength),
        source_ids: stringList(payload.source_ids),
      };
      return {
        ...next,
        provisional_claims: upsertById(
          next.provisional_claims,
          "claim_id",
          claim,
        ),
      };
    }
    case "patch.ready":
      return {
        ...next,
        status: "patch_ready",
        current_stage: null,
        patch_id: stringValue(payload.patch_id),
      };
    case "run.completed":
      return {
        ...next,
        status: "completed",
        current_stage: null,
        patch_id: stringValue(payload.patch_id) ?? next.patch_id,
      };
    case "run.failed":
      return {
        ...next,
        status: "failed",
        current_stage: null,
        cancellation_state: "not_requested",
        error: {
          code: stringValue(payload.code) ?? "generation_failed",
          message: stringValue(payload.message) ?? "The generation run failed.",
          retryable: payload.retryable === true,
          stage: stringValue(payload.stage),
          details:
            typeof payload.details === "object" && payload.details !== null
              ? (payload.details as Record<string, unknown>)
              : {},
        },
      };
    case "run.cancelled":
      return {
        ...next,
        status: "cancelled",
        current_stage: null,
        cancellation_state: "cancelled",
      };
    default:
      return next;
  }
}

export function generationProgressReducer(
  state: GenerationProgressState,
  action: GenerationProgressAction,
): GenerationProgressState {
  if (action.type === "reset") {
    return {
      ...INITIAL_GENERATION_PROGRESS,
      canvas_id: action.canvasId,
      connection: action.canvasId ? "connecting" : "idle",
    };
  }
  if (action.type === "connection") {
    return { ...state, connection: action.connection };
  }
  if (action.type === "track") {
    const current = state.runs[action.runId];
    return {
      ...state,
      runs: {
        ...state.runs,
        [action.runId]: current ?? emptyRun(action.runId, action.status),
      },
    };
  }
  if (action.type === "reconcile") {
    const current =
      state.runs[action.run.run_id] ?? emptyRun(action.run.run_id);
    return {
      ...state,
      runs: {
        ...state.runs,
        [action.run.run_id]: {
          ...current,
          status: action.run.status,
          current_stage: action.run.current_stage,
          attempt: action.run.attempt,
          max_attempts: action.run.max_attempts,
          cancellation_state: action.run.cancellation_state,
          error: action.run.error,
          patch_id: action.run.ready_patch_id ?? current.patch_id,
        },
      },
    };
  }

  if (action.event.canvas_sequence <= state.cursor) return state;
  const current =
    state.runs[action.event.run_id] ?? emptyRun(action.event.run_id, "running");
  return {
    ...state,
    cursor: action.event.canvas_sequence,
    runs: {
      ...state.runs,
      [action.event.run_id]: applyEvent(current, action.event),
    },
  };
}

export function isRunPlaceholderVisible(run: RunProgress): boolean {
  return run.status === "queued" || run.status === "running";
}

export function canRetryRun(run: RunProgress): boolean {
  return (
    run.status === "failed" &&
    run.error?.retryable === true &&
    run.attempt < run.max_attempts
  );
}
