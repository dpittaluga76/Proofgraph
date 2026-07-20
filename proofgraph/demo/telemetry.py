from __future__ import annotations

import logging
from typing import Any

from proofgraph.runtime.telemetry import emit_structured_telemetry

logger = logging.getLogger("proofgraph.demo")


def emit_demo_telemetry(event: str, **fields: Any) -> None:
    emit_structured_telemetry(
        logger,
        component="demo",
        event=event,
        fields=fields,
    )
