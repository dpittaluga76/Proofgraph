# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

FORWARD_SQL = """
ALTER TABLE research_query_cache
    ADD CONSTRAINT fk_research_query_cache_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE source_content_cache
    ADD CONSTRAINT fk_source_content_cache_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE source_ingestion_request
    ADD CONSTRAINT fk_source_ingestion_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE source_ingestion_request
    ADD CONSTRAINT fk_source_ingestion_result_canvas
    FOREIGN KEY (result_source_node_id, canvas_id) REFERENCES node(id, canvas_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;
"""

REVERSE_SQL = """
ALTER TABLE source_ingestion_request
    DROP CONSTRAINT IF EXISTS fk_source_ingestion_result_canvas;
ALTER TABLE source_ingestion_request
    DROP CONSTRAINT IF EXISTS fk_source_ingestion_canvas;
ALTER TABLE source_content_cache
    DROP CONSTRAINT IF EXISTS fk_source_content_cache_canvas;
ALTER TABLE research_query_cache
    DROP CONSTRAINT IF EXISTS fk_research_query_cache_canvas;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0002_generationrun_events_after_sequence"),
        ("graph", "0002_canvas_lifecycle_delete"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchQueryCache",
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
                ("normalized_query", models.TextField()),
                ("provider_identity", models.TextField()),
                ("strategy_version", models.TextField()),
                ("prompt_version", models.TextField()),
                ("context_hash", models.TextField()),
                ("result", models.JSONField()),
                ("retrieved_at", models.DateTimeField()),
                ("fresh_until", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="research_query_cache_entries",
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "research_query_cache",
                "indexes": [
                    models.Index(
                        fields=[
                            "canvas",
                            "normalized_query",
                            "provider_identity",
                            "strategy_version",
                            "prompt_version",
                            "context_hash",
                            "fresh_until",
                        ],
                        name="research_cache_fresh_idx",
                    ),
                    models.Index(fields=["expires_at", "id"], name="research_cache_expiry_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "canvas",
                            "normalized_query",
                            "provider_identity",
                            "strategy_version",
                            "prompt_version",
                            "context_hash",
                        ),
                        name="uq_research_query_cache_key",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("fresh_until__lte", models.F("expires_at")),
                            ("retrieved_at__lte", models.F("fresh_until")),
                        ),
                        name="ck_research_cache_times",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceContentCache",
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
                ("normalized_url", models.TextField()),
                ("content_hash", models.TextField()),
                ("retained_content", models.TextField(blank=True, null=True)),
                ("retrieval_metadata", models.JSONField()),
                ("retrieved_at", models.DateTimeField()),
                ("fresh_until", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="source_content_cache_entries",
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "source_content_cache",
                "indexes": [
                    models.Index(
                        fields=[
                            "canvas",
                            "normalized_url",
                            "fresh_until",
                            "retrieved_at",
                        ],
                        name="source_cache_fresh_idx",
                    ),
                    models.Index(fields=["expires_at", "id"], name="source_cache_expiry_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("canvas", "normalized_url", "content_hash"),
                        name="uq_source_content_cache_key",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("retained_content__isnull", True)),
                        name="ck_source_cache_no_content",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("fresh_until__lte", models.F("expires_at")),
                            ("retrieved_at__lte", models.F("fresh_until")),
                        ),
                        name="ck_source_cache_times",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceIngestionRequest",
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
                ("operation_key", models.TextField()),
                ("request_fingerprint", models.TextField()),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ]
                    ),
                ),
                ("worker_id", models.TextField(blank=True, null=True)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("lease_epoch", models.BigIntegerField(default=0)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="source_ingestion_requests",
                        to="graph.canvas",
                    ),
                ),
                (
                    "result_source_node",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="completed_source_ingestions",
                        to="graph.node",
                    ),
                ),
            ],
            options={
                "db_table": "source_ingestion_request",
                "indexes": [
                    models.Index(
                        condition=models.Q(("status", "running")),
                        fields=["lease_expires_at", "id"],
                        name="source_ingestion_reclaim_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("canvas", "operation_key"),
                        name="uq_source_ingestion_operation",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("status__in", ["running", "completed", "failed"])),
                        name="ck_source_ingestion_status",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("lease_epoch__gte", 0)),
                        name="ck_source_ingestion_epoch",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("status", "running"), _negated=True),
                            ("lease_epoch__gt", 0),
                            _connector="OR",
                        ),
                        name="ck_source_ingestion_run_epoch",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("error__isnull", True),
                                ("lease_expires_at__isnull", False),
                                ("lease_token__isnull", False),
                                ("result_source_node__isnull", True),
                                ("status", "running"),
                                ("worker_id__isnull", False),
                            ),
                            models.Q(
                                ("error__isnull", True),
                                ("lease_expires_at__isnull", True),
                                ("lease_token__isnull", True),
                                ("result_source_node__isnull", False),
                                ("status", "completed"),
                                ("worker_id__isnull", True),
                            ),
                            models.Q(
                                ("error__isnull", False),
                                ("lease_expires_at__isnull", True),
                                ("lease_token__isnull", True),
                                ("result_source_node__isnull", True),
                                ("status", "failed"),
                                ("worker_id__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="ck_source_ingestion_lifecycle",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
