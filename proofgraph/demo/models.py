# ruff: noqa: RUF012 - Django Meta uses declarative mutable class attributes.

import uuid

from django.db import models
from django.utils import timezone

from proofgraph.graph.models import Canvas


class DemoSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    active_canvas = models.OneToOneField(
        Canvas,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="demo_session",
    )
    quota_window_started_at = models.DateTimeField(default=timezone.now)
    hybrid_run_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "demo_session"
        indexes = [models.Index(fields=["expires_at", "id"], name="demo_session_expiry_idx")]

    def __str__(self) -> str:
        return f"{self.id}:{self.active_canvas_id}"


class DemoGlobalQuotaWindow(models.Model):
    window_started_at = models.DateTimeField(primary_key=True)
    hybrid_run_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "demo_global_quota_window"

    def __str__(self) -> str:
        return f"{self.window_started_at.isoformat()}:{self.hybrid_run_count}"
