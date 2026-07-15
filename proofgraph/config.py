from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


def env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"Invalid boolean environment value: {value!r}")


def database_config(environ: Mapping[str, str]) -> dict[str, Any]:
    raw_url = environ.get("DATABASE_URL")
    if not raw_url:
        raise ImproperlyConfigured("DATABASE_URL is required and must point to PostgreSQL.")

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must use the postgres or postgresql scheme.")
    if not parsed.hostname or not parsed.path.lstrip("/"):
        raise ImproperlyConfigured("DATABASE_URL must include a host and database name.")

    try:
        connection_max_age = int(environ.get("DATABASE_CONN_MAX_AGE", "60"))
    except ValueError as error:
        raise ImproperlyConfigured("DATABASE_CONN_MAX_AGE must be an integer.") from error

    options = dict(parse_qsl(parsed.query, keep_blank_values=False))

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": connection_max_age,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }
