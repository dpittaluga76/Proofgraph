from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from proofgraph.generation.composition import get_composition
from proofgraph.generation.context import canonical_json
from proofgraph.generation.events import append_event_locked
from proofgraph.generation.models import (
    GenerationEventType,
    GenerationStage,
    GraphPatch,
    RunOperation,
    RunStatus,
    StageStatus,
)
from proofgraph.generation.ports import DurableComposition
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.queue import LeaseLostError, RunLease, lock_fenced_run
from proofgraph.generation.retention import RetentionPolicyError, validate_retained_payload
from proofgraph.generation.schemas import (
    ProgressEventEnvelope,
    RunErrorEnvelope,
    RunExecutionConfiguration,
    StageResultEnvelope,
)
from proofgraph.generation.telemetry import emit_telemetry, telemetry_context

OPERATION_STAGE_PLANS: dict[str, tuple[str, ...]] = {
    RunOperation.GENERATE_STRATEGIES: ("planning", "constructing_patch"),
    RunOperation.RESEARCH_EVIDENCE: (
        "planning",
        "researching",
        "extracting",
        "clustering",
        "constructing_patch",
    ),
    RunOperation.SYNTHESIZE_OPPORTUNITIES: (
        "synthesizing",
        "critiquing",
        "constructing_patch",
    ),
}

REGENERATION_STAGE_PLANS: dict[str, tuple[str, ...]] = {
    "strategy": ("planning", "constructing_patch"),
    "claim": ("planning", "researching", "extracting", "clustering", "constructing_patch"),
    "opportunity": ("synthesizing", "critiquing", "constructing_patch"),
    "assumption": ("synthesizing", "critiquing", "constructing_patch"),
    "risk": ("synthesizing", "critiquing", "constructing_patch"),
    "validation_experiment": ("synthesizing", "critiquing", "constructing_patch"),
}


class StageExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


@dataclass(frozen=True)
class StageCheckpoint:
    stage: GenerationStage
    reused: bool
    result: StageResultEnvelope | None


@dataclass(frozen=True)
class StagePlanStep:
    stage_name: str
    phase: str | None = None
    targets: tuple[dict[str, Any], ...] = ()


def _plan_steps(
    stage_names: tuple[str, ...],
    *,
    phase: str | None = None,
    targets: tuple[dict[str, Any], ...] = (),
) -> tuple[StagePlanStep, ...]:
    return tuple(StagePlanStep(stage_name, phase, targets) for stage_name in stage_names)


def _regeneration_phase(kind: str) -> str:
    if kind == "strategy":
        return "strategy"
    if kind == "claim":
        return "claim_evidence"
    return "opportunity_family"


def stage_plan_for_run(run: Any) -> tuple[StagePlanStep, ...]:
    if run.operation != RunOperation.REGENERATE_STALE:
        return _plan_steps(OPERATION_STAGE_PLANS[run.operation])
    regeneration = run.context_manifest.get("regeneration") or {}
    targets = tuple(regeneration.get("targets") or ())
    if regeneration.get("scope") != "branch":
        kind = targets[0]["kind"]
        stage_names = REGENERATION_STAGE_PLANS[kind]
        phase_steps = _plan_steps(
            stage_names[:-1],
            phase=_regeneration_phase(kind),
            targets=targets,
        )
        return (*phase_steps, StagePlanStep(stage_names[-1], "patch", targets))

    steps: list[StagePlanStep] = []
    ordered_phases = (
        ("strategy", {"strategy"}, ("planning",)),
        (
            "claim_evidence",
            {"claim"},
            ("planning", "researching", "extracting", "clustering"),
        ),
        (
            "opportunity_family",
            {"opportunity", "assumption", "risk", "validation_experiment"},
            ("synthesizing", "critiquing"),
        ),
    )
    for phase, supported_kinds, stage_names in ordered_phases:
        phase_targets = tuple(target for target in targets if target["kind"] in supported_kinds)
        if phase_targets:
            steps.extend(_plan_steps(stage_names, phase=phase, targets=phase_targets))
    steps.append(StagePlanStep("constructing_patch", "patch", targets))
    return tuple(steps)


def _stage_version(stage_name: str, configuration: RunExecutionConfiguration) -> str:
    if stage_name == "clustering":
        return "deterministic_clusterer_v1"
    return f"{configuration.pipeline_version}:{stage_name}:v2"


def _stage_input_hash(
    stage_name: str,
    stage_input: dict[str, Any],
    configuration: RunExecutionConfiguration,
) -> str:
    material = {
        "semantic_input": stage_input,
        "stage": stage_name,
        "stage_version": _stage_version(stage_name, configuration),
        "provider_identity": configuration.provider_identity,
        "execution_profile": configuration.model_dump(mode="json"),
        "fixture_version": configuration.fixture_version,
    }
    return hashlib.sha256(canonical_json(material).encode()).hexdigest()


def _finalize_cancelled_run(run: Any, *, reason: str, stage_id: Any | None = None) -> None:
    if stage_id is not None:
        stage = (
            GenerationStage.objects.select_for_update()
            .filter(pk=stage_id, run=run, status=StageStatus.RUNNING)
            .first()
        )
        if stage is not None:
            stage.status = StageStatus.FAILED
            stage.error = {
                "code": "run_cancelled",
                "message": "The run was cancelled while this stage was active.",
                "retryable": False,
                "stage": stage.name,
                "details": {},
            }
            stage.completed_at = timezone.now()
            stage.save(update_fields=["status", "error", "completed_at"])
    run.status = RunStatus.CANCELLED
    run.completed_at = timezone.now()
    run.worker_id = None
    run.lease_token = None
    run.heartbeat_at = None
    run.lease_expires_at = None
    run.save(
        update_fields=[
            "status",
            "completed_at",
            "worker_id",
            "lease_token",
            "heartbeat_at",
            "lease_expires_at",
        ]
    )
    append_event_locked(
        run,
        GenerationEventType.RUN_CANCELLED,
        {"reason": reason, "attempt": run.attempt},
        terminal_once=True,
    )


def _cancel_if_requested(lease: RunLease, *, stage_id: Any | None = None) -> bool:
    with transaction.atomic():
        run = lock_fenced_run(lease)
        if run.cancel_requested_at is None:
            return False
        _finalize_cancelled_run(
            run,
            reason="worker_observed_cancellation",
            stage_id=stage_id,
        )
    emit_telemetry("run.cancelled", run_id=lease.run_id, lease_epoch=lease.lease_epoch)
    return True


def _begin_stage(
    lease: RunLease,
    stage_name: str,
    input_hash: str,
) -> StageCheckpoint:
    with transaction.atomic():
        run = lock_fenced_run(lease)
        completed = GenerationStage.objects.filter(
            run=run,
            name=stage_name,
            input_hash=input_hash,
            status=StageStatus.COMPLETED,
        ).first()
        if completed is not None:
            return StageCheckpoint(
                completed,
                True,
                StageResultEnvelope.model_validate_json(json.dumps(completed.output)),
            )

        stage = (
            GenerationStage.objects.select_for_update()
            .filter(run=run, name=stage_name, input_hash=input_hash)
            .first()
        )
        now = timezone.now()
        if stage is None:
            stage = GenerationStage.objects.create(
                run=run,
                name=stage_name,
                input_hash=input_hash,
                status=StageStatus.RUNNING,
                attempt=1,
                started_at=now,
            )
        else:
            stage.status = StageStatus.RUNNING
            stage.attempt += 1
            stage.error = None
            stage.output = None
            stage.openai_response_id = None
            stage.started_at = now
            stage.completed_at = None
            stage.save(
                update_fields=[
                    "status",
                    "attempt",
                    "error",
                    "output",
                    "openai_response_id",
                    "started_at",
                    "completed_at",
                ]
            )
        run.current_stage = stage_name
        run.save(update_fields=["current_stage"])
        append_event_locked(
            run,
            GenerationEventType.STAGE_STARTED,
            {"stage": stage_name, "stage_attempt": stage.attempt, "input_hash": input_hash},
        )
    emit_telemetry(
        "stage.started",
        run_id=lease.run_id,
        stage=stage_name,
        stage_attempt=stage.attempt,
    )
    return StageCheckpoint(stage, False, None)


def _complete_stage(
    lease: RunLease,
    stage_id: Any,
    result: StageResultEnvelope,
) -> None:
    serialized = result.model_dump(mode="json")
    validate_retained_payload(serialized)
    with transaction.atomic():
        run = lock_fenced_run(lease)
        stage = GenerationStage.objects.select_for_update().get(
            pk=stage_id,
            run=run,
            status=StageStatus.RUNNING,
        )
        stage.status = StageStatus.COMPLETED
        stage.output = serialized
        stage.openai_response_id = result.model_response_id
        stage.error = None
        stage.completed_at = timezone.now()
        stage.save(
            update_fields=[
                "status",
                "output",
                "openai_response_id",
                "error",
                "completed_at",
            ]
        )
        for progress in result.progress_events:
            append_event_locked(run, progress.event_type, progress.payload)
        append_event_locked(
            run,
            GenerationEventType.STAGE_PROGRESS,
            {"stage": result.stage_name, "state": "completed"},
        )
    duration_ms = None
    if stage.started_at is not None and stage.completed_at is not None:
        duration_ms = int((stage.completed_at - stage.started_at).total_seconds() * 1_000)
    emit_telemetry(
        "stage.completed",
        run_id=lease.run_id,
        canvas_id=lease.canvas_id,
        stage=result.stage_name,
        stage_attempt=stage.attempt,
        duration_ms=duration_ms,
        provider_identity=result.provider_identity,
        model_response_id=result.model_response_id,
        token_usage=result.token_usage.model_dump(mode="json") if result.token_usage else None,
    )


def _append_progress_event(lease: RunLease, progress: ProgressEventEnvelope) -> None:
    with transaction.atomic():
        run = lock_fenced_run(lease)
        append_event_locked(run, progress.event_type, progress.payload)


def _fail_run(
    lease: RunLease,
    error: RunErrorEnvelope,
    *,
    stage_id: Any | None = None,
) -> None:
    try:
        with transaction.atomic():
            run = lock_fenced_run(lease)
            persisted_error = error
            if error.retryable and run.attempt >= run.max_attempts:
                persisted_error = RunErrorEnvelope(
                    code="attempts_exhausted",
                    message="The run exhausted its maximum worker attempts.",
                    retryable=False,
                    stage=error.stage,
                    details={"last_error": error.model_dump(mode="json")},
                )
            if stage_id is not None:
                stage = (
                    GenerationStage.objects.select_for_update()
                    .filter(
                        pk=stage_id,
                        run=run,
                        status=StageStatus.RUNNING,
                    )
                    .first()
                )
                if stage is not None:
                    stage.status = StageStatus.FAILED
                    stage.error = error.model_dump(mode="json")
                    stage.completed_at = timezone.now()
                    stage.save(update_fields=["status", "error", "completed_at"])
            run.status = RunStatus.FAILED
            run.error = persisted_error.model_dump(mode="json")
            run.completed_at = timezone.now()
            run.worker_id = None
            run.lease_token = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.save(
                update_fields=[
                    "status",
                    "error",
                    "completed_at",
                    "worker_id",
                    "lease_token",
                    "heartbeat_at",
                    "lease_expires_at",
                ]
            )
            append_event_locked(
                run,
                GenerationEventType.RUN_FAILED,
                {**run.error, "attempt": run.attempt},
                terminal_once=True,
            )
    except LeaseLostError:
        return
    emit_telemetry(
        "run.failed",
        run_id=lease.run_id,
        code=persisted_error.code,
        retryable=persisted_error.retryable,
        stage=persisted_error.stage,
    )


def _persist_patch_then_complete(lease: RunLease, patch_output: Any) -> bool:
    if not isinstance(patch_output, dict):
        raise StageExecutionError(
            "invalid_patch_output",
            "Patch construction must return one typed patch object.",
            retryable=False,
        )
    operations = patch_output.get("operations")
    if not isinstance(operations, list) or not all(isinstance(item, dict) for item in operations):
        raise StageExecutionError(
            "invalid_patch_output",
            "Patch construction must return a list of typed operations.",
            retryable=False,
        )
    validate_retained_payload(operations)
    with transaction.atomic():
        run = lock_fenced_run(lease)
        if run.cancel_requested_at is not None:
            _finalize_cancelled_run(run, reason="cancelled_before_finalization")
            patch = None
        else:
            patch, _created = GraphPatch.objects.get_or_create(
                run=run,
                defaults={
                    "canvas_id": run.canvas_id,
                    "base_canvas_revision": run.base_canvas_revision,
                    "operations": operations,
                    "regeneration_target_ids": patch_output.get("regeneration_target_ids", []),
                    "permitted_stale_resolution_ids": patch_output.get(
                        "permitted_stale_resolution_ids", []
                    ),
                },
            )
            run.status = RunStatus.PATCH_READY
            run.save(update_fields=["status"])
            append_event_locked(
                run,
                GenerationEventType.PATCH_READY,
                {"patch_id": str(patch.id), "operation_count": len(operations)},
            )
    if patch is None:
        emit_telemetry("run.cancelled", run_id=lease.run_id, lease_epoch=lease.lease_epoch)
        return False
    emit_telemetry("patch.ready", run_id=lease.run_id, patch_id=patch.id)

    with transaction.atomic():
        run = lock_fenced_run(lease, statuses=(RunStatus.PATCH_READY,))
        run.status = RunStatus.COMPLETED
        run.completed_at = timezone.now()
        run.worker_id = None
        run.lease_token = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.save(
            update_fields=[
                "status",
                "completed_at",
                "worker_id",
                "lease_token",
                "heartbeat_at",
                "lease_expires_at",
            ]
        )
        append_event_locked(
            run,
            GenerationEventType.RUN_COMPLETED,
            {"patch_id": str(patch.id), "attempt": run.attempt},
            terminal_once=True,
        )
    emit_telemetry("run.completed", run_id=lease.run_id, patch_id=patch.id)
    return True


def process_claimed_run(
    lease: RunLease,
    *,
    composition: DurableComposition | None = None,
) -> None:
    composition = composition or get_composition()
    with transaction.atomic():
        run = lock_fenced_run(lease)
        configuration = RunExecutionConfiguration.model_validate(run.execution_configuration)
        stage_plan = stage_plan_for_run(run)
        context_snapshot = run.context_snapshot
        context_manifest = run.context_manifest
        context_hash = run.context_hash
        base_canvas_revision = run.base_canvas_revision

    prior_outputs: dict[str, Any] = {}
    final_patch_output: dict[str, Any] | None = None
    for step in stage_plan:
        stage_name = step.stage_name
        checkpoint: StageCheckpoint | None = None
        try:
            if _cancel_if_requested(lease):
                return
            stage_input = {
                "run_id": str(lease.run_id),
                "context_snapshot": context_snapshot,
                "context_manifest": context_manifest,
                "context_hash": context_hash,
                "base_canvas_revision": base_canvas_revision,
                "regeneration_phase": step.phase,
                "target_workset": list(step.targets),
                "prior_stage_outputs": prior_outputs,
            }
            input_hash = _stage_input_hash(stage_name, stage_input, configuration)
            checkpoint = _begin_stage(lease, stage_name, input_hash)
            if checkpoint.reused:
                assert checkpoint.result is not None
                result = checkpoint.result
                emit_telemetry("stage.reused", run_id=lease.run_id, stage=stage_name)
            else:
                try:
                    with telemetry_context(
                        run_id=str(lease.run_id),
                        canvas_id=str(run.canvas_id),
                        stage=stage_name,
                        worker_id=lease.worker_id,
                        lease_epoch=lease.lease_epoch,
                        attempt=run.attempt,
                        execution_profile_id=configuration.profile_id,
                    ):
                        result = composition.executor.execute(
                            stage_name=stage_name,
                            stage_input=stage_input,
                            configuration=configuration,
                            progress_callback=lambda progress: _append_progress_event(
                                lease, progress
                            ),
                        )
                except ProviderExecutionError as error:
                    raise StageExecutionError(
                        error.code,
                        error.message,
                        retryable=error.retryable,
                        details=error.details,
                    ) from error
                except (PydanticValidationError, RetentionPolicyError) as error:
                    raise StageExecutionError(
                        "invalid_stage_output",
                        str(error) or "The stage output failed envelope validation.",
                        retryable=False,
                    ) from error
            try:
                result = composition.output_validator.validate(
                    stage_name,
                    result,
                    stage_input=stage_input,
                )
                if result.provider_identity != configuration.provider_identity:
                    raise ValueError("stage provider identity does not match the frozen profile")
            except ValueError as error:
                raise StageExecutionError(
                    "invalid_stage_output",
                    str(error) or "The stage output failed validation.",
                    retryable=False,
                ) from error
            if not checkpoint.reused:
                if _cancel_if_requested(lease, stage_id=checkpoint.stage.id):
                    return
                _complete_stage(lease, checkpoint.stage.id, result)
            output_key = f"{step.phase}:{stage_name}" if step.phase else stage_name
            prior_outputs[output_key] = {
                "phase": step.phase,
                "target_node_ids": [target["node_id"] for target in step.targets],
                "output": result.output,
            }
            if stage_name == "constructing_patch":
                final_patch_output = result.output
        except LeaseLostError:
            return
        except StageExecutionError as error:
            _fail_run(
                lease,
                RunErrorEnvelope(
                    code=error.code,
                    message=error.message,
                    retryable=error.retryable,
                    stage=stage_name,
                    details=error.details,
                ),
                stage_id=checkpoint.stage.id if checkpoint is not None else None,
            )
            return
        except Exception as error:
            _fail_run(
                lease,
                RunErrorEnvelope(
                    code="stage_execution_failed",
                    message=str(error) or "Stage execution failed.",
                    retryable=True,
                    stage=stage_name,
                    details={"exception_type": type(error).__name__},
                ),
                stage_id=checkpoint.stage.id if checkpoint is not None else None,
            )
            return

    try:
        if final_patch_output is None:
            raise StageExecutionError(
                "missing_patch_output",
                "The stage plan completed without a patch-construction result.",
                retryable=False,
            )
        if final_patch_output.get("base_canvas_revision") != base_canvas_revision:
            raise StageExecutionError(
                "invalid_patch_revision",
                "Patch construction returned a different base canvas revision.",
                retryable=False,
                details={
                    "expected": base_canvas_revision,
                    "actual": final_patch_output.get("base_canvas_revision"),
                },
            )
        _persist_patch_then_complete(lease, final_patch_output)
    except LeaseLostError:
        return
    except StageExecutionError as error:
        _fail_run(
            lease,
            RunErrorEnvelope(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                stage="constructing_patch",
                details=error.details,
            ),
        )
