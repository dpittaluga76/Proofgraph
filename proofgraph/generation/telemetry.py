from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from proofgraph.runtime.telemetry import emit_structured_telemetry

logger = logging.getLogger("proofgraph.generation")
_dimensions: ContextVar[dict[str, Any] | None] = ContextVar(
    "generation_telemetry_dimensions",
    default=None,
)


@contextmanager
def telemetry_context(**dimensions: Any) -> Iterator[None]:
    current = _dimensions.get() or {}
    token = _dimensions.set({**current, **dimensions})
    try:
        yield
    finally:
        _dimensions.reset(token)


def emit_telemetry(name: str, **dimensions: Any) -> None:
    correlated = {**(_dimensions.get() or {}), **dimensions}
    emit_structured_telemetry(
        logger,
        component="generation",
        event=name,
        fields=correlated,
    )


def emit_patch_regeneration_terminal(
    *,
    run_id: Any,
    canvas_id: Any,
    status: str,
) -> None:
    from proofgraph.generation.models import GraphPatch

    original_patch = (
        GraphPatch.objects.filter(regenerated_by_run_id=run_id).values("id", "run_id").first()
    )
    if original_patch is None:
        return
    emit_telemetry(
        "patch.regeneration_terminal",
        patch_id=original_patch["id"],
        original_run_id=original_patch["run_id"],
        run_id=run_id,
        canvas_id=canvas_id,
        status=status,
    )
