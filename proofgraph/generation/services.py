from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from proofgraph.generation.composition import get_composition
from proofgraph.generation.context import canonical_json, validate_explicit_selection
from proofgraph.generation.events import append_event_locked
from proofgraph.generation.models import (
    CanvasEventCursor,
    GenerationEventType,
    GenerationRun,
    RunStatus,
)
from proofgraph.generation.schemas import GenerationRunRequest
from proofgraph.generation.telemetry import emit_patch_regeneration_terminal, emit_telemetry
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, Node


@dataclass(frozen=True)
class ServiceResult:
    payload: dict[str, Any]
    status: int


def generation_request_fingerprint(request: GenerationRunRequest) -> str:
    semantic = request.model_dump(mode="json", exclude={"idempotency_key"})
    semantic["selected_node_ids"] = sorted(semantic["selected_node_ids"])
    semantic["expected_node_versions"] = dict(sorted(semantic["expected_node_versions"].items()))
    return hashlib.sha256(canonical_json(semantic).encode()).hexdigest()


def _run_created_payload(run: GenerationRun, after: int) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "status": run.status,
        "events_url": f"/api/canvases/{run.canvas_id}/events?after={after}",
    }


def create_generation_run(
    canvas_id: uuid.UUID,
    request: GenerationRunRequest,
) -> ServiceResult:
    composition = get_composition()
    fingerprint = generation_request_fingerprint(request)

    with transaction.atomic():
        canvas = Canvas.objects.select_for_update().filter(pk=canvas_id).first()
        if canvas is None:
            raise GraphAPIError(status=404, code="canvas_not_found", message="Canvas not found.")

        existing = GenerationRun.objects.filter(
            canvas=canvas,
            idempotency_key=request.idempotency_key,
        ).first()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise GraphAPIError(
                    status=409,
                    code="idempotency_key_conflict",
                    message="The idempotency key was already used for a different request.",
                )
            return ServiceResult(
                _run_created_payload(existing, existing.events_after_sequence),
                202,
            )

        configuration = composition.profile_resolver.resolve(
            request.execution_profile_id,
            product_request=True,
        )
        selected_nodes = list(
            Node.objects.select_for_update()
            .filter(canvas=canvas, id__in=request.selected_node_ids)
            .order_by("id")
        )
        validate_explicit_selection(request, selected_nodes)
        context = composition.context_factory.build(
            canvas=canvas,
            request=request,
            selected_nodes=selected_nodes,
        )
        events_after_sequence = CanvasEventCursor.objects.get(canvas=canvas).last_sequence
        run = GenerationRun.objects.create(
            canvas=canvas,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            status=RunStatus.QUEUED,
            base_canvas_revision=canvas.revision,
            context_snapshot=context.snapshot,
            context_manifest=context.manifest,
            context_hash=context.context_hash,
            events_after_sequence=events_after_sequence,
            selected_node_ids=sorted(str(node_id) for node_id in request.selected_node_ids),
            expected_node_versions={
                str(node_id): request.expected_node_versions[node_id]
                for node_id in sorted(request.expected_node_versions, key=str)
            },
            execution_configuration=configuration.model_dump(mode="json"),
        )
        result = ServiceResult(_run_created_payload(run, run.events_after_sequence), 202)

    emit_telemetry("run.queued", run_id=run.id, canvas_id=canvas_id, operation=run.operation)
    return result


def get_generation_run(run_id: uuid.UUID) -> GenerationRun:
    run = GenerationRun.objects.filter(pk=run_id).first()
    if run is None:
        raise GraphAPIError(status=404, code="generation_run_not_found", message="Run not found.")
    return run


def serialize_generation_run(run: GenerationRun) -> dict[str, Any]:
    try:
        patch_id = str(run.patch.id)
    except GenerationRun.patch.RelatedObjectDoesNotExist:
        patch_id = None

    cancellation_state = "not_requested"
    if run.status == RunStatus.CANCELLED:
        cancellation_state = "cancelled"
    elif run.cancel_requested_at is not None:
        cancellation_state = "requested"

    return {
        "run_id": str(run.id),
        "canvas_id": str(run.canvas_id),
        "operation": run.operation,
        "status": run.status,
        "current_stage": run.current_stage,
        "attempt": run.attempt,
        "max_attempts": run.max_attempts,
        "cancellation_state": cancellation_state,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "ready_patch_id": patch_id,
    }


def cancel_generation_run(run_id: uuid.UUID) -> ServiceResult:
    now = timezone.now()
    with transaction.atomic():
        run = GenerationRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None:
            raise GraphAPIError(
                status=404, code="generation_run_not_found", message="Run not found."
            )
        if run.status == RunStatus.CANCELLED:
            return ServiceResult(serialize_generation_run(run), 200)
        if run.status == RunStatus.QUEUED:
            run.status = RunStatus.CANCELLED
            run.cancel_requested_at = now
            run.completed_at = now
            run.save(update_fields=["status", "cancel_requested_at", "completed_at"])
            append_event_locked(
                run,
                GenerationEventType.RUN_CANCELLED,
                {"reason": "cancelled_before_claim", "attempt": run.attempt},
                terminal_once=True,
            )
            result = ServiceResult(serialize_generation_run(run), 200)
        elif run.status == RunStatus.RUNNING:
            if run.cancel_requested_at is None:
                run.cancel_requested_at = now
                run.save(update_fields=["cancel_requested_at"])
            result = ServiceResult(serialize_generation_run(run), 202)
        else:
            raise GraphAPIError(
                status=409,
                code="run_not_cancellable",
                message="The run is no longer cancellable.",
                details={"status": run.status},
            )
    emit_telemetry("run.cancel_requested", run_id=run.id, status=run.status)
    if run.status == RunStatus.CANCELLED:
        emit_patch_regeneration_terminal(
            run_id=run.id,
            canvas_id=run.canvas_id,
            status=RunStatus.CANCELLED,
        )
    return result


def retry_generation_run(run_id: uuid.UUID) -> ServiceResult:
    with transaction.atomic():
        run = GenerationRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None:
            raise GraphAPIError(
                status=404, code="generation_run_not_found", message="Run not found."
            )
        error = run.error if isinstance(run.error, dict) else {}
        if (
            run.status != RunStatus.FAILED
            or error.get("retryable") is not True
            or run.worker_id is not None
            or run.lease_token is not None
            or run.lease_expires_at is not None
            or run.attempt >= run.max_attempts
        ):
            raise GraphAPIError(
                status=409,
                code="run_not_retryable",
                message="The run is not eligible for a safe retry.",
                details={
                    "status": run.status,
                    "attempt": run.attempt,
                    "max_attempts": run.max_attempts,
                },
            )
        run.status = RunStatus.QUEUED
        run.current_stage = None
        run.cancel_requested_at = None
        run.error = None
        run.completed_at = None
        run.save(
            update_fields=[
                "status",
                "current_stage",
                "cancel_requested_at",
                "error",
                "completed_at",
            ]
        )
        append_event_locked(
            run,
            GenerationEventType.RUN_RETRY_REQUESTED,
            {"next_attempt": run.attempt + 1},
        )
        result = ServiceResult(serialize_generation_run(run), 202)
    emit_telemetry("run.retry_requested", run_id=run.id, next_attempt=run.attempt + 1)
    return result
