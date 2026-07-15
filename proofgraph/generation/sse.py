from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse

from proofgraph.demo.authorization import authorize_canvas
from proofgraph.generation.events import encode_sse_event
from proofgraph.generation.models import GenerationEvent
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas


def _fetch_event_batch(canvas_id: uuid.UUID, after: int) -> list[GenerationEvent]:
    try:
        return list(
            GenerationEvent.objects.filter(
                canvas_id=canvas_id,
                canvas_sequence__gt=after,
            ).order_by("canvas_sequence")[:100]
        )
    finally:
        connections["default"].close()


def _canvas_exists(canvas_id: uuid.UUID) -> bool:
    try:
        return Canvas.objects.filter(pk=canvas_id).exists()
    finally:
        connections["default"].close()


def _after_sequence(request: HttpRequest) -> int:
    value = request.GET.get("after", request.headers.get("Last-Event-ID", "0"))
    try:
        sequence = int(value)
    except (TypeError, ValueError):
        return -1
    return sequence


async def canvas_event_stream(
    canvas_id: uuid.UUID,
    *,
    after: int,
    poll_seconds: float | None = None,
    heartbeat_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    poll_seconds = poll_seconds or settings.GENERATION_SSE_POLL_SECONDS
    heartbeat_seconds = heartbeat_seconds or settings.GENERATION_SSE_HEARTBEAT_SECONDS
    cursor = after
    last_delivery = time.monotonic()
    try:
        while True:
            events = await sync_to_async(_fetch_event_batch, thread_sensitive=True)(
                canvas_id,
                cursor,
            )
            if events:
                for event in events:
                    cursor = event.canvas_sequence
                    last_delivery = time.monotonic()
                    yield encode_sse_event(event)
                continue

            if time.monotonic() - last_delivery >= heartbeat_seconds:
                last_delivery = time.monotonic()
                yield b": keepalive\n\n"
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        return


async def canvas_events(request: HttpRequest, canvas_id: uuid.UUID) -> HttpResponse:
    if request.method != "GET":
        return JsonResponse(
            {"error": {"code": "method_not_allowed", "message": "Only GET is allowed."}},
            status=405,
        )
    after = _after_sequence(request)
    if after < 0:
        return JsonResponse(
            {
                "error": {
                    "code": "invalid_event_cursor",
                    "message": "after must be a non-negative integer.",
                }
            },
            status=422,
        )
    try:
        await sync_to_async(authorize_canvas, thread_sensitive=True)(request, canvas_id)
    except GraphAPIError as error:
        return JsonResponse(error.as_payload(), status=error.status)
    if not await sync_to_async(_canvas_exists, thread_sensitive=True)(canvas_id):
        return JsonResponse(
            {"error": {"code": "canvas_not_found", "message": "Canvas not found."}},
            status=404,
        )

    response = StreamingHttpResponse(
        canvas_event_stream(canvas_id, after=after),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    response["Connection"] = "keep-alive"
    response.headers.pop("Content-Length", None)
    return response
