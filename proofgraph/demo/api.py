from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from proofgraph.demo.authorization import (
    resolve_demo_session,
    serialize_session,
    signed_session_cookie,
)
from proofgraph.demo.models import DemoSession
from proofgraph.demo.services import bootstrap_demo_session, reset_demo_session
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.serialization import serialize_canvas

View = Callable[..., HttpResponse]


def _api_errors(view: View) -> View:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> HttpResponse:
        try:
            return view(*args, **kwargs)
        except GraphAPIError as error:
            return JsonResponse(error.as_payload(), status=error.status)

    return wrapped


def _set_session_cookie(response: JsonResponse, session: DemoSession) -> None:
    response.set_cookie(
        settings.DEMO_COOKIE_NAME,
        signed_session_cookie(session.id),
        expires=session.expires_at,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path="/",
    )


def _payload(session: DemoSession) -> dict[str, Any]:
    return {
        "session": serialize_session(session),
        "canvas": serialize_canvas(session.active_canvas),
    }


@ensure_csrf_cookie
@require_http_methods(["GET"])
@_api_errors
def demo_bootstrap(request: HttpRequest) -> JsonResponse:
    session, _created = bootstrap_demo_session(request)
    response = JsonResponse(_payload(session))
    _set_session_cookie(response, session)
    return response


@require_http_methods(["POST"])
@_api_errors
def demo_reset(request: HttpRequest) -> JsonResponse:
    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GraphAPIError(
            status=400,
            code="invalid_json",
            message="Request body must contain valid JSON.",
        ) from error
    if body != {}:
        raise GraphAPIError(
            status=422,
            code="invalid_demo_reset_request",
            message="Demo reset does not accept request fields.",
        )
    session = resolve_demo_session(request)
    assert session is not None
    session = reset_demo_session(session.id)
    response = JsonResponse(_payload(session))
    _set_session_cookie(response, session)
    return response
