from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from proofgraph.demo.models import DemoGlobalQuotaWindow, DemoSession
from proofgraph.demo.telemetry import emit_demo_telemetry
from proofgraph.graph.exceptions import GraphAPIError

ALLOWED_DEMO_PROFILES = frozenset({"demo_hybrid_v1", "replay_v1"})


def validate_demo_profile(session: DemoSession, profile_id: str) -> None:
    if profile_id in ALLOWED_DEMO_PROFILES:
        return
    emit_demo_telemetry(
        "demo.profile_rejected",
        demo_session_id=session.id,
        canvas_id=session.active_canvas_id,
        profile_id=profile_id,
    )
    raise GraphAPIError(
        status=403,
        code="demo_profile_not_allowed",
        message="Anonymous demo sessions may use hybrid demo or deterministic replay only.",
        details={"allowed_profiles": sorted(ALLOWED_DEMO_PROFILES)},
    )


def consume_hybrid_quota(session: DemoSession) -> None:
    now = timezone.now()
    if now >= session.quota_window_started_at + timedelta(
        seconds=settings.DEMO_QUOTA_WINDOW_SECONDS
    ):
        session.quota_window_started_at = now
        session.hybrid_run_count = 0

    from proofgraph.generation.models import GenerationRun, RunStatus

    active_runs = GenerationRun.objects.filter(
        demo_session=session,
        status__in=[RunStatus.QUEUED, RunStatus.RUNNING],
        execution_configuration__profile_id="demo_hybrid_v1",
    ).count()
    if active_runs >= settings.DEMO_SESSION_CONCURRENT_RUN_LIMIT:
        _quota_error(
            "demo.concurrent_quota_rejected",
            "demo_concurrent_run_limit",
            "This demo session already has the maximum number of active runs.",
            session,
            active_runs=active_runs,
        )
    if session.hybrid_run_count >= settings.DEMO_SESSION_HYBRID_RUN_LIMIT:
        _quota_error(
            "demo.session_quota_rejected",
            "demo_session_quota_exhausted",
            "This demo session used its hybrid-run allowance for the current hour.",
            session,
            hybrid_run_count=session.hybrid_run_count,
        )

    window_start = now.replace(minute=0, second=0, microsecond=0)
    DemoGlobalQuotaWindow.objects.get_or_create(window_started_at=window_start)
    global_window = DemoGlobalQuotaWindow.objects.select_for_update().get(
        window_started_at=window_start
    )
    if global_window.hybrid_run_count >= settings.DEMO_GLOBAL_HYBRID_RUN_LIMIT:
        emit_demo_telemetry(
            "demo.circuit_breaker_open",
            demo_session_id=session.id,
            canvas_id=session.active_canvas_id,
            profile_id="demo_hybrid_v1",
            global_hybrid_run_count=global_window.hybrid_run_count,
        )
        _quota_error(
            "demo.global_quota_rejected",
            "demo_global_quota_exhausted",
            "The public hybrid demo is temporarily at capacity.",
            session,
            global_hybrid_run_count=global_window.hybrid_run_count,
        )

    session.hybrid_run_count += 1
    session.save(update_fields=["quota_window_started_at", "hybrid_run_count"])
    global_window.hybrid_run_count += 1
    global_window.save(update_fields=["hybrid_run_count"])


def emit_replay_selected(session: DemoSession) -> None:
    transaction.on_commit(
        lambda: emit_demo_telemetry(
            "demo.replay_selected",
            demo_session_id=session.id,
            canvas_id=session.active_canvas_id,
            profile_id="replay_v1",
        )
    )


def _quota_error(
    event: str,
    code: str,
    message: str,
    session: DemoSession,
    **fields: int,
) -> None:
    emit_demo_telemetry(
        event,
        demo_session_id=session.id,
        canvas_id=session.active_canvas_id,
        profile_id="demo_hybrid_v1",
        **fields,
    )
    raise GraphAPIError(
        status=429,
        code=code,
        message=message,
        details={"fallback_profile": "replay_v1", **fields},
    )
