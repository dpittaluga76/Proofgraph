import { describe, expect, it } from "vitest";

import type { GenerationEvent, GenerationRun } from "./api";
import {
  canRetryRun,
  generationProgressReducer,
  INITIAL_GENERATION_PROGRESS,
  isRunPlaceholderVisible,
} from "./generation";

function event(
  canvasSequence: number,
  eventType: GenerationEvent["event_type"],
  payload: Record<string, unknown> = {},
  runId = "run-a",
): GenerationEvent {
  return {
    run_id: runId,
    canvas_sequence: canvasSequence,
    run_sequence: canvasSequence,
    event_type: eventType,
    payload,
    timestamp: "2026-07-15T12:00:00Z",
  };
}

describe("generationProgressReducer", () => {
  it("replays interleaved runs once and retains provisional evidence outside graph state", () => {
    let state = generationProgressReducer(INITIAL_GENERATION_PROGRESS, {
      type: "reset",
      canvasId: "canvas-a",
    });
    state = generationProgressReducer(state, {
      type: "event",
      event: event(1, "run.started", { attempt: 1 }, "run-a"),
    });
    state = generationProgressReducer(state, {
      type: "event",
      event: event(2, "run.started", { attempt: 1 }, "run-b"),
    });
    state = generationProgressReducer(state, {
      type: "event",
      event: event(3, "research.source_found", {
        provisional: true,
        source_id: "source-a",
        sanitized_excerpt: "Previously retrieved synthetic evidence.",
        cache_hit: true,
      }),
    });
    state = generationProgressReducer(state, {
      type: "event",
      event: event(4, "evidence.extracted", {
        provisional: true,
        claim_id: "claim-a",
        claim: "Questionnaires delay deals.",
        classification: "observed",
        strength: "strong",
        source_ids: ["source-a"],
      }),
    });

    const replayed = generationProgressReducer(state, {
      type: "event",
      event: event(4, "evidence.extracted", {
        provisional: true,
        claim_id: "duplicate",
      }),
    });

    expect(replayed).toBe(state);
    expect(state.cursor).toBe(4);
    expect(state.runs["run-a"].provisional_sources).toHaveLength(1);
    expect(state.runs["run-a"].provisional_claims[0].claim).toBe(
      "Questionnaires delay deals.",
    );
    expect(state.runs["run-b"].status).toBe("running");
  });

  it("removes terminal runs from placeholder state and preserves safe retry eligibility", () => {
    let state = generationProgressReducer(INITIAL_GENERATION_PROGRESS, {
      type: "track",
      runId: "run-a",
      status: "queued",
    });
    expect(isRunPlaceholderVisible(state.runs["run-a"])).toBe(true);

    state = generationProgressReducer(state, {
      type: "event",
      event: event(1, "run.failed", {
        code: "provider_timeout",
        message: "Provider timed out.",
        retryable: true,
        stage: "researching",
        attempt: 1,
      }),
    });
    expect(isRunPlaceholderVisible(state.runs["run-a"])).toBe(false);
    expect(canRetryRun(state.runs["run-a"])).toBe(true);

    state = generationProgressReducer(state, {
      type: "event",
      event: event(2, "run.retry_requested", { next_attempt: 2 }),
    });
    expect(state.runs["run-a"].status).toBe("queued");
    expect(state.runs["run-a"].error).toBeNull();
  });

  it("reconciles cancellation and patch readiness from authoritative run status", () => {
    const run: GenerationRun = {
      run_id: "run-a",
      canvas_id: "canvas-a",
      operation: "research_evidence",
      status: "running",
      current_stage: "extracting",
      attempt: 2,
      max_attempts: 3,
      cancellation_state: "requested",
      error: null,
      created_at: "2026-07-15T12:00:00Z",
      started_at: "2026-07-15T12:00:01Z",
      completed_at: null,
      ready_patch_id: null,
    };
    let state = generationProgressReducer(INITIAL_GENERATION_PROGRESS, {
      type: "reconcile",
      run,
    });
    expect(state.runs["run-a"].cancellation_state).toBe("requested");

    state = generationProgressReducer(state, {
      type: "event",
      event: event(1, "patch.ready", { patch_id: "patch-a" }),
    });
    expect(state.runs["run-a"].patch_id).toBe("patch-a");
    expect(isRunPlaceholderVisible(state.runs["run-a"])).toBe(false);
  });
});
