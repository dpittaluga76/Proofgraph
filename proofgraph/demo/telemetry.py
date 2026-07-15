from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("proofgraph.demo")


def emit_demo_telemetry(event: str, **fields: Any) -> None:
    payload = {"event": event}
    payload.update(
        {key: str(value) if hasattr(value, "hex") else value for key, value in fields.items()}
    )
    logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
