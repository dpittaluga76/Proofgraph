from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from django.db.models import Max

from proofgraph.generation.models import (
    CanvasEventCursor,
    GenerationEvent,
    GenerationEventType,
    GenerationRun,
)
from proofgraph.generation.retention import validate_progress_payload

TERMINAL_EVENT_TYPES = {
    GenerationEventType.RUN_COMPLETED,
    GenerationEventType.RUN_FAILED,
    GenerationEventType.RUN_CANCELLED,
}


def append_event_locked(
    run: GenerationRun,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    terminal_once: bool = False,
) -> GenerationEvent | None:
    """Append inside an atomic block after locking the generation run row."""
    validate_progress_payload(event_type, payload)
    if terminal_once:
        attempt = payload.get("attempt", run.attempt)
        if GenerationEvent.objects.filter(
            run=run,
            event_type__in=TERMINAL_EVENT_TYPES,
            payload__attempt=attempt,
        ).exists():
            return None

    cursor = CanvasEventCursor.objects.select_for_update().get(canvas_id=run.canvas_id)
    run_sequence = (
        GenerationEvent.objects.filter(run=run).aggregate(value=Max("run_sequence"))["value"] or 0
    ) + 1
    cursor.last_sequence += 1
    cursor.save(update_fields=["last_sequence"])
    return GenerationEvent.objects.create(
        canvas_id=run.canvas_id,
        run=run,
        canvas_sequence=cursor.last_sequence,
        run_sequence=run_sequence,
        event_type=event_type,
        payload=dict(payload),
    )


def serialize_event(event: GenerationEvent) -> dict[str, Any]:
    return {
        "run_id": str(event.run_id),
        "canvas_sequence": event.canvas_sequence,
        "run_sequence": event.run_sequence,
        "event_type": event.event_type,
        "payload": event.payload,
        "timestamp": event.created_at.isoformat(),
    }


def encode_sse_event(event: GenerationEvent) -> bytes:
    data = json.dumps(serialize_event(event), separators=(",", ":"), ensure_ascii=False)
    return (f"id: {event.canvas_sequence}\nevent: {event.event_type}\ndata: {data}\n\n").encode()
