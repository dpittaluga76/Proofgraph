from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core import signing
from django.http import HttpRequest
from django.utils import timezone

from proofgraph.demo.models import DemoSession
from proofgraph.graph.exceptions import GraphAPIError

COOKIE_SALT = "proofgraph.demo.session.v1"


def signed_session_cookie(session_id: uuid.UUID) -> str:
    return signing.dumps(str(session_id), key=settings.SECRET_KEY, salt=COOKIE_SALT)


def _cookie_session_id(request: HttpRequest) -> uuid.UUID | None:
    value = request.COOKIES.get(settings.DEMO_COOKIE_NAME)
    if not value:
        return None
    try:
        unsigned = signing.loads(value, key=settings.SECRET_KEY, salt=COOKIE_SALT)
        return uuid.UUID(unsigned)
    except (signing.BadSignature, TypeError, ValueError):
        return None


def demo_authorization_active(request: HttpRequest) -> bool:
    return settings.DEMO_PUBLIC_MODE or settings.DEMO_COOKIE_NAME in request.COOKIES


def resolve_demo_session(
    request: HttpRequest,
    *,
    required: bool = True,
) -> DemoSession | None:
    if not demo_authorization_active(request):
        return None
    cached = getattr(request, "proofgraph_demo_session", None)
    if isinstance(cached, DemoSession):
        return cached
    session_id = _cookie_session_id(request)
    if session_id is None:
        if not required:
            return None
        raise GraphAPIError(
            status=401,
            code="demo_session_required",
            message="Start or resume a demo session before accessing this resource.",
        )
    session = DemoSession.objects.filter(pk=session_id).first()
    if session is None:
        if not required:
            return None
        raise GraphAPIError(
            status=401,
            code="demo_session_required",
            message="Start or resume a demo session before accessing this resource.",
        )
    if session.expires_at <= timezone.now():
        from proofgraph.demo.telemetry import emit_demo_telemetry

        emit_demo_telemetry(
            "demo.session_expired",
            demo_session_id=session.id,
            canvas_id=session.active_canvas_id,
            expires_at=session.expires_at,
        )
        raise GraphAPIError(
            status=401,
            code="demo_session_expired",
            message="This demo session expired. Reload to start a fresh isolated session.",
        )
    request.proofgraph_demo_session = session
    return session


def authorize_canvas(request: HttpRequest, canvas_id: uuid.UUID) -> DemoSession | None:
    session = resolve_demo_session(request)
    if session is not None and session.active_canvas_id != canvas_id:
        raise _resource_not_found()
    return session


def authorize_run(request: HttpRequest, run_id: uuid.UUID) -> DemoSession | None:
    session = resolve_demo_session(request)
    if session is None:
        return None
    from proofgraph.generation.models import GenerationRun

    if not GenerationRun.objects.filter(pk=run_id, demo_session=session).exists():
        raise _resource_not_found()
    return session


def authorize_patch(request: HttpRequest, patch_id: uuid.UUID) -> DemoSession | None:
    session = resolve_demo_session(request)
    if session is None:
        return None
    from proofgraph.generation.models import GraphPatch

    if not GraphPatch.objects.filter(pk=patch_id, run__demo_session=session).exists():
        raise _resource_not_found()
    return session


def authorize_source(request: HttpRequest, source_id: uuid.UUID) -> DemoSession | None:
    session = resolve_demo_session(request)
    if session is None:
        return None
    from proofgraph.graph.models import Node, NodeKind

    if not Node.objects.filter(
        pk=source_id,
        canvas_id=session.active_canvas_id,
        kind=NodeKind.SOURCE,
    ).exists():
        raise _resource_not_found()
    return session


def authorize_ingestion(request: HttpRequest, ingestion_id: uuid.UUID) -> DemoSession | None:
    session = resolve_demo_session(request)
    if session is None:
        return None
    from proofgraph.generation.models import SourceIngestionRequest

    if not SourceIngestionRequest.objects.filter(
        pk=ingestion_id,
        canvas_id=session.active_canvas_id,
    ).exists():
        raise _resource_not_found()
    return session


def _resource_not_found() -> GraphAPIError:
    return GraphAPIError(
        status=404,
        code="resource_not_found",
        message="The requested resource was not found.",
    )


def serialize_session(session: DemoSession) -> dict[str, Any]:
    return {
        "expires_at": session.expires_at.isoformat(),
        "hybrid_run_count": session.hybrid_run_count,
        "hybrid_run_limit": settings.DEMO_SESSION_HYBRID_RUN_LIMIT,
        "primary_profile": "demo_hybrid_v1",
        "fallback_profile": "replay_v1",
    }
