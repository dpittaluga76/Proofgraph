# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("graph", "0002_canvas_lifecycle_delete")]

    operations = [
        migrations.CreateModel(
            name="DemoGlobalQuotaWindow",
            fields=[
                ("window_started_at", models.DateTimeField(primary_key=True, serialize=False)),
                ("hybrid_run_count", models.PositiveIntegerField(default=0)),
            ],
            options={"db_table": "demo_global_quota_window"},
        ),
        migrations.CreateModel(
            name="DemoSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "quota_window_started_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("hybrid_run_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                (
                    "active_canvas",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="demo_session",
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "demo_session",
                "indexes": [
                    models.Index(
                        fields=["expires_at", "id"],
                        name="demo_session_expiry_idx",
                    )
                ],
            },
        ),
    ]
