import json
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.lifecycle import delete_canvas
from proofgraph.graph.models import Canvas, GraphOperation
from proofgraph.graph.operations import apply_graph_operation
from proofgraph.graph.serialization import serialize_canvas, serialize_graph_operation

View = Callable[..., HttpResponse]


def _api_errors(view: View) -> View:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> HttpResponse:
        try:
            return view(*args, **kwargs)
        except GraphAPIError as error:
            return JsonResponse(error.as_payload(), status=error.status)

    return wrapped


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _json_object(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json.loads(
            request.body or b"{}",
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise GraphAPIError(
            status=400,
            code="invalid_json",
            message="Request body must contain valid JSON.",
        ) from error
    if not isinstance(payload, dict):
        raise GraphAPIError(
            status=422,
            code="invalid_object",
            message="Request body must be a JSON object.",
        )
    return payload


def _validate_canvas_payload(payload: dict[str, Any]) -> str:
    unknown_fields = sorted(set(payload) - {"title"})
    if unknown_fields:
        raise GraphAPIError(
            status=422,
            code="unknown_field",
            message="The request contains unknown canvas fields.",
            details={"fields": unknown_fields},
        )
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise GraphAPIError(
            status=422,
            code="invalid_title",
            message="title must be a non-empty string.",
        )
    return title.strip()


def _locked_canvas_or_error(canvas_id: uuid.UUID) -> Canvas:
    canvas = Canvas.objects.select_for_update().filter(pk=canvas_id).first()
    if canvas is None:
        raise GraphAPIError(status=404, code="canvas_not_found", message="Canvas not found.")
    return canvas


@require_http_methods(["POST"])
@_api_errors
def canvas_collection(request: HttpRequest) -> JsonResponse:
    title = _validate_canvas_payload(_json_object(request))
    canvas = Canvas.objects.create(title=title)
    return JsonResponse({"canvas": serialize_canvas(canvas)}, status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
@_api_errors
def canvas_detail(request: HttpRequest, canvas_id: uuid.UUID) -> HttpResponse:
    if request.method == "GET":
        with transaction.atomic():
            canvas_payload = serialize_canvas(_locked_canvas_or_error(canvas_id))
        return JsonResponse({"canvas": canvas_payload})

    if request.method == "PATCH":
        title = _validate_canvas_payload(_json_object(request))
        with transaction.atomic():
            canvas = _locked_canvas_or_error(canvas_id)
            canvas.title = title
            canvas.updated_at = timezone.now()
            canvas.save(update_fields=["title", "updated_at"])
            canvas_payload = serialize_canvas(canvas)
        return JsonResponse({"canvas": canvas_payload})

    delete_canvas(canvas_id)
    return HttpResponse(status=204)


def _after_revision(request: HttpRequest) -> int:
    raw_value = request.GET.get("after", "0")
    try:
        revision = int(raw_value)
    except ValueError as error:
        raise GraphAPIError(
            status=422,
            code="invalid_revision",
            message="after must be a non-negative integer.",
        ) from error
    if revision < 0 or str(revision) != raw_value:
        raise GraphAPIError(
            status=422,
            code="invalid_revision",
            message="after must be a non-negative integer.",
        )
    return revision


@require_http_methods(["GET", "POST"])
@_api_errors
def canvas_operations(request: HttpRequest, canvas_id: uuid.UUID) -> JsonResponse:
    if request.method == "POST":
        result = apply_graph_operation(canvas_id, _json_object(request))
        return JsonResponse(result)

    after = _after_revision(request)
    with transaction.atomic():
        canvas = _locked_canvas_or_error(canvas_id)
        operations = GraphOperation.objects.filter(
            canvas=canvas,
            canvas_revision__gt=after,
        ).order_by("canvas_revision", "id")
        payload = {
            "canvas_revision": canvas.revision,
            "operations": [serialize_graph_operation(operation) for operation in operations],
        }
    return JsonResponse(payload)
