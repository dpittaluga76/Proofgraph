from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "lease_token",
    "password",
    "secret",
}
_SECRET_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_cookie",
    "_credential",
    "_password",
    "_secret",
)
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "code",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}
_REDACTED = "[REDACTED]"


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = parsed.hostname
    if hostname is None:
        return value
    try:
        parsed_port = parsed.port
    except ValueError:
        return value
    port = f":{parsed_port}" if parsed_port is not None else ""
    netloc = f"{hostname}{port}"
    query = urlencode(
        [
            (key, _REDACTED if key.casefold() in _SENSITIVE_QUERY_KEYS else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


def sanitize_telemetry_value(value: Any, *, key: str = "") -> Any:
    if _is_secret_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): sanitize_telemetry_value(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_telemetry_value(child) for child in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and key.casefold().endswith(("url", "_uri")):
        return _sanitize_url(value)
    return value


def emit_structured_telemetry(
    logger: logging.Logger,
    *,
    component: str,
    event: str,
    fields: Mapping[str, Any],
) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "component": component,
        "event": event,
        **sanitize_telemetry_value(fields),
    }
    logger.info(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


__all__ = ["emit_structured_telemetry", "sanitize_telemetry_value"]
