from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

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
    logger.info(
        json.dumps(
            {"event": name, **correlated},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
