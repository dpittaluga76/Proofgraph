from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from proofgraph.demo.authorization import _cookie_session_id
from proofgraph.demo.cleanup import fence_and_terminalize_run
from proofgraph.demo.models import DemoSession
from proofgraph.demo.seed import create_seeded_canvas
from proofgraph.demo.telemetry import emit_demo_telemetry
from proofgraph.generation.models import GenerationRun, RunStatus
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.lifecycle import delete_canvas


def bootstrap_demo_session(request: HttpRequest) -> tuple[DemoSession, bool]:
    session_id = _cookie_session_id(request)
    if session_id is not None:
        session = DemoSession.objects.filter(pk=session_id).first()
        if (
            session is not None
            and session.expires_at > timezone.now()
            and session.active_canvas_id is not None
        ):
            return session, False
        if session is not None and session.expires_at <= timezone.now():
            emit_demo_telemetry(
                "demo.session_expired",
                demo_session_id=session.id,
                canvas_id=session.active_canvas_id,
                expires_at=session.expires_at,
            )

    now = timezone.now()
    with transaction.atomic():
        canvas = create_seeded_canvas()
        session = DemoSession.objects.create(
            active_canvas=canvas,
            quota_window_started_at=now,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.DEMO_SESSION_SECONDS),
        )
    emit_demo_telemetry(
        "demo.session_created",
        demo_session_id=session.id,
        canvas_id=canvas.id,
        expires_at=session.expires_at,
    )
    return session, True


def reset_demo_session(session_id: uuid.UUID) -> DemoSession:
    cancelled_run_count = 0
    with transaction.atomic():
        session = DemoSession.objects.select_for_update().filter(pk=session_id).first()
        if session is None:
            raise GraphAPIError(
                status=401,
                code="demo_session_required",
                message="Start a demo session before resetting it.",
            )
        if session.expires_at <= timezone.now():
            raise GraphAPIError(
                status=401,
                code="demo_session_expired",
                message="This demo session expired. Reload to start a fresh isolated session.",
            )
        previous_canvas_id = session.active_canvas_id
        if previous_canvas_id is not None:
            active_runs = list(
                GenerationRun.objects.select_for_update()
                .filter(
                    demo_session=session,
                    canvas_id=previous_canvas_id,
                    status__in=[
                        RunStatus.QUEUED,
                        RunStatus.RUNNING,
                        RunStatus.PATCH_READY,
                    ],
                )
                .order_by("created_at", "id")
            )
            for run in active_runs:
                fence_and_terminalize_run(
                    run,
                    timezone.now(),
                    cancelled=True,
                    reason="demo_reset",
                )
            cancelled_run_count = len(active_runs)
        canvas = create_seeded_canvas()
        session.active_canvas = canvas
        session.save(update_fields=["active_canvas"])
        if previous_canvas_id is not None:
            delete_canvas(previous_canvas_id)
    emit_demo_telemetry(
        "demo.reset",
        demo_session_id=session.id,
        previous_canvas_id=previous_canvas_id,
        canvas_id=canvas.id,
        expires_at=session.expires_at,
        hybrid_run_count=session.hybrid_run_count,
        cancelled_run_count=cancelled_run_count,
    )
    return session
