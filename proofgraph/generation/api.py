from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from pydantic import ValidationError

from proofgraph.generation.patch_application import apply_graph_patch
from proofgraph.generation.patches import (
    get_graph_patch,
    regenerate_graph_patch,
    reject_graph_patch,
    serialize_graph_patch,
)
from proofgraph.generation.retention import RetentionPolicyError
from proofgraph.generation.schemas import (
    GenerationRunRequest,
    PatchApplyRequest,
    PatchRegenerationRequest,
    SourceIngestionEnvelope,
)
from proofgraph.generation.services import (
    cancel_generation_run,
    create_generation_run,
    get_generation_run,
    retry_generation_run,
    serialize_generation_run,
)
from proofgraph.generation.source_ingestion import (
    create_source,
    get_source,
    get_source_ingestion,
    serialize_ingestion,
    serialize_source_node,
)
from proofgraph.graph.exceptions import GraphAPIError

View = Callable[..., HttpResponse]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _api_errors(view: View) -> View:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> HttpResponse:
        try:
            return view(*args, **kwargs)
        except GraphAPIError as error:
            return JsonResponse(error.as_payload(), status=error.status)
        except ValidationError as error:
            retention_error = next(
                (
                    item.get("ctx", {}).get("error")
                    for item in error.errors()
                    if isinstance(item.get("ctx", {}).get("error"), RetentionPolicyError)
                ),
                None,
            )
            if retention_error is not None:
                return JsonResponse(
                    {
                        "error": {
                            "code": "retention_policy_violation",
                            "message": str(retention_error),
                        }
                    },
                    status=422,
                )
            return JsonResponse(
                {
                    "error": {
                        "code": "invalid_generation_request",
                        "message": "The generation request is invalid.",
                        "details": {"validation": json.loads(error.json(include_url=False))},
                    }
                },
                status=422,
            )
        except RetentionPolicyError as error:
            return JsonResponse(
                {
                    "error": {
                        "code": "retention_policy_violation",
                        "message": str(error),
                    }
                },
                status=422,
            )

    return wrapped


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


@require_http_methods(["POST"])
@_api_errors
def generation_run_collection(request: HttpRequest, canvas_id: uuid.UUID) -> JsonResponse:
    payload = _json_object(request)
    envelope = GenerationRunRequest.model_validate_json(json.dumps(payload))
    result = create_generation_run(canvas_id, envelope)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["GET"])
@_api_errors
def generation_run_detail(_request: HttpRequest, run_id: uuid.UUID) -> JsonResponse:
    return JsonResponse(serialize_generation_run(get_generation_run(run_id)))


@require_http_methods(["POST"])
@_api_errors
def generation_run_cancel(_request: HttpRequest, run_id: uuid.UUID) -> JsonResponse:
    result = cancel_generation_run(run_id)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["POST"])
@_api_errors
def generation_run_retry(_request: HttpRequest, run_id: uuid.UUID) -> JsonResponse:
    result = retry_generation_run(run_id)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["GET"])
@_api_errors
def graph_patch_detail(_request: HttpRequest, patch_id: uuid.UUID) -> JsonResponse:
    return JsonResponse({"patch": serialize_graph_patch(get_graph_patch(patch_id))})


@require_http_methods(["POST"])
@_api_errors
def graph_patch_reject(request: HttpRequest, patch_id: uuid.UUID) -> JsonResponse:
    payload = _json_object(request)
    if payload:
        raise GraphAPIError(
            status=422,
            code="invalid_patch_rejection_request",
            message="Patch rejection does not accept request fields.",
            details={"fields": sorted(payload)},
        )
    result = reject_graph_patch(patch_id)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["POST"])
@_api_errors
def graph_patch_apply(request: HttpRequest, patch_id: uuid.UUID) -> JsonResponse:
    payload = _json_object(request)
    try:
        envelope = PatchApplyRequest.model_validate_json(json.dumps(payload))
    except ValidationError as error:
        raise GraphAPIError(
            status=422,
            code="invalid_patch_apply_request",
            message="The patch-apply request is invalid.",
            details={"validation": json.loads(error.json(include_url=False))},
        ) from error
    result = apply_graph_patch(patch_id, envelope)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["POST"])
@_api_errors
def graph_patch_regenerate(request: HttpRequest, patch_id: uuid.UUID) -> JsonResponse:
    payload = _json_object(request)
    try:
        envelope = PatchRegenerationRequest.model_validate_json(json.dumps(payload))
    except ValidationError as error:
        raise GraphAPIError(
            status=422,
            code="invalid_patch_regeneration_request",
            message="The patch-regeneration request is invalid.",
            details={"validation": json.loads(error.json(include_url=False))},
        ) from error
    result = regenerate_graph_patch(patch_id, envelope)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["POST"])
@_api_errors
def source_collection(request: HttpRequest, canvas_id: uuid.UUID) -> JsonResponse:
    payload = _json_object(request)
    try:
        envelope = SourceIngestionEnvelope.model_validate_json(json.dumps(payload))
    except ValidationError as error:
        raise GraphAPIError(
            status=422,
            code="invalid_source_request",
            message="The source-ingestion request is invalid.",
            details={"validation": json.loads(error.json(include_url=False))},
        ) from error
    result = create_source(canvas_id, envelope)
    return JsonResponse(result.payload, status=result.status)


@require_http_methods(["GET"])
@_api_errors
def source_ingestion_detail(
    _request: HttpRequest,
    ingestion_id: uuid.UUID,
) -> JsonResponse:
    return JsonResponse(serialize_ingestion(get_source_ingestion(ingestion_id)))


@require_http_methods(["GET"])
@_api_errors
def source_detail(_request: HttpRequest, source_id: uuid.UUID) -> JsonResponse:
    return JsonResponse({"source": serialize_source_node(get_source(source_id))})
