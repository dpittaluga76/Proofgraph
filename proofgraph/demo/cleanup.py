from __future__ import annotations

import uuid
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Now
from django.utils import timezone

from proofgraph.demo.models import DemoSession
from proofgraph.demo.telemetry import emit_demo_telemetry
from proofgraph.generation.events import append_event_locked
from proofgraph.generation.models import GenerationEventType, GenerationRun, RunStatus
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.graph.lifecycle import delete_canvas


def cleanup_expired_demo_sessions(limit: int | None = None) -> int:
    limit = max(limit or settings.DEMO_CLEANUP_BATCH_SIZE, 1)
    cleaned = 0
    attempted: set[uuid.UUID] = set()
    while len(attempted) < limit:
        cleaned_session = False
        cleaned_canvas_id = None
        cancellation_requested = False
        with transaction.atomic():
            session = (
                DemoSession.objects.select_for_update(skip_locked=True)
                .filter(expires_at__lte=Now())
                .exclude(id__in=attempted)
                .order_by("expires_at", "id")
                .first()
            )
            if session is not None:
                attempted.add(session.id)
                (
                    cleaned_session,
                    cleaned_canvas_id,
                    cancellation_requested,
                ) = _cleanup_locked_session(session)
        if session is None:
            break
        if cleaned_session:
            cleaned += 1
            emit_demo_telemetry(
                "demo.session_cleaned",
                demo_session_id=session.id,
                canvas_id=cleaned_canvas_id,
                cancellation_requested=cancellation_requested,
            )
    return cleaned


def _cleanup_locked_session(
    session: DemoSession,
) -> tuple[bool, uuid.UUID | None, bool]:
    now = timezone.now()
    cancellation_requested = False
    runs = list(
        GenerationRun.objects.select_for_update()
        .filter(
            demo_session=session,
            status__in=[RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PATCH_READY],
        )
        .order_by("created_at", "id")
    )
    has_live_owner = False
    for run in runs:
        if run.status == RunStatus.QUEUED:
            fence_and_terminalize_run(run, now, cancelled=True, reason="demo_session_expired")
            continue
        lease_is_expired = run.lease_expires_at is None or run.lease_expires_at <= now
        if run.status == RunStatus.PATCH_READY and lease_is_expired:
            fence_and_terminalize_run(run, now, cancelled=False, reason="demo_session_expired")
            continue
        if run.status == RunStatus.RUNNING and lease_is_expired:
            fence_and_terminalize_run(run, now, cancelled=True, reason="demo_session_expired")
            continue
        has_live_owner = True
        if run.cancel_requested_at is None:
            run.cancel_requested_at = now
            run.save(update_fields=["cancel_requested_at"])
            cancellation_requested = True

    if has_live_owner:
        transaction.on_commit(
            lambda: emit_demo_telemetry(
                "demo.cleanup_waiting_for_fence",
                demo_session_id=session.id,
                canvas_id=session.active_canvas_id,
                active_run_count=len(runs),
            )
        )
        return False, None, cancellation_requested

    cleaned_canvas_id = session.active_canvas_id
    session.active_canvas = None
    session.save(update_fields=["active_canvas"])
    if cleaned_canvas_id is not None:
        delete_canvas(cleaned_canvas_id)
    session.delete()
    return True, cleaned_canvas_id, cancellation_requested


def fence_and_terminalize_run(
    run: GenerationRun,
    now: datetime,
    *,
    cancelled: bool,
    reason: str,
) -> None:
    run.status = RunStatus.CANCELLED if cancelled else RunStatus.COMPLETED
    run.cancel_requested_at = now if cancelled else run.cancel_requested_at
    run.completed_at = now
    run.worker_id = None
    run.lease_token = None
    run.heartbeat_at = None
    run.lease_expires_at = None
    run.lease_epoch += 1
    run.save(
        update_fields=[
            "status",
            "cancel_requested_at",
            "completed_at",
            "worker_id",
            "lease_token",
            "heartbeat_at",
            "lease_expires_at",
            "lease_epoch",
        ]
    )
    append_event_locked(
        run,
        GenerationEventType.RUN_CANCELLED if cancelled else GenerationEventType.RUN_COMPLETED,
        {"reason": reason, "attempt": run.attempt},
        terminal_once=True,
    )
    transaction.on_commit(
        lambda: emit_telemetry(
            "run.cancelled" if cancelled else "run.completed",
            run_id=run.id,
            canvas_id=run.canvas_id,
            demo_session_id=run.demo_session_id,
            operation_key=run.idempotency_key,
            lease_epoch=run.lease_epoch,
            attempt=run.attempt,
            reason=reason,
            terminalized_by="demo_cleanup",
            duration_ms=(
                int((run.completed_at - run.started_at).total_seconds() * 1_000)
                if run.started_at is not None and run.completed_at is not None
                else None
            ),
        )
    )
