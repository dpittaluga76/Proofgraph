import logging

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@ensure_csrf_cookie
@require_GET
def health(_request: HttpRequest) -> JsonResponse:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        logger.exception("PostgreSQL health check failed")
        return JsonResponse(
            {"status": "unavailable", "database": "unavailable"},
            status=503,
        )

    return JsonResponse(
        {
            "status": "ok",
            "database": "ok",
            "demo_mode": settings.DEMO_PUBLIC_MODE,
        }
    )
