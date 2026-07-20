from __future__ import annotations

import logging
from typing import Any

from proofgraph.runtime.telemetry import emit_structured_telemetry

logger = logging.getLogger("proofgraph.graph")


def emit_graph_telemetry(event: str, **fields: Any) -> None:
    emit_structured_telemetry(
        logger,
        component="graph",
        event=event,
        fields=fields,
    )
