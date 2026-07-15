# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

FORWARD_SQL = """
ALTER TABLE generation_run
    ADD CONSTRAINT fk_generation_run_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE generation_stage
    ADD CONSTRAINT fk_generation_stage_run
    FOREIGN KEY (run_id) REFERENCES generation_run(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE canvas_event_cursor
    ADD CONSTRAINT fk_canvas_event_cursor_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE generation_event
    ADD CONSTRAINT fk_generation_event_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE generation_event
    ADD CONSTRAINT fk_generation_event_run_canvas
    FOREIGN KEY (run_id, canvas_id) REFERENCES generation_run(id, canvas_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE graph_patch
    ADD CONSTRAINT fk_graph_patch_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE graph_patch
    ADD CONSTRAINT fk_graph_patch_run_canvas
    FOREIGN KEY (run_id, canvas_id) REFERENCES generation_run(id, canvas_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE graph_patch
    ADD CONSTRAINT fk_graph_patch_regenerated_run_canvas
    FOREIGN KEY (regenerated_by_run_id, canvas_id) REFERENCES generation_run(id, canvas_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE graph_patch_operation_decision
    ADD CONSTRAINT fk_graph_patch_decision_canvas
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE graph_patch_operation_decision
    ADD CONSTRAINT fk_graph_patch_decision_patch_canvas
    FOREIGN KEY (patch_id, canvas_id) REFERENCES graph_patch(id, canvas_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE graph_patch_operation_decision
    ADD CONSTRAINT fk_graph_patch_decision_operation_canvas
    FOREIGN KEY (graph_operation_id, canvas_id) REFERENCES graph_operation(id, canvas_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

INSERT INTO canvas_event_cursor (canvas_id, last_sequence)
SELECT id, 0 FROM canvas
ON CONFLICT (canvas_id) DO NOTHING;

CREATE FUNCTION proofgraph_create_canvas_event_cursor()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO canvas_event_cursor (canvas_id, last_sequence)
    VALUES (NEW.id, 0)
    ON CONFLICT (canvas_id) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER canvas_event_cursor_insert_trigger
AFTER INSERT ON canvas
FOR EACH ROW
EXECUTE FUNCTION proofgraph_create_canvas_event_cursor();

CREATE FUNCTION proofgraph_protect_completed_generation_stage()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'completed generation stages are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER generation_stage_completed_immutable_trigger
BEFORE UPDATE ON generation_stage
FOR EACH ROW
EXECUTE FUNCTION proofgraph_protect_completed_generation_stage();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS generation_stage_completed_immutable_trigger ON generation_stage;
DROP FUNCTION IF EXISTS proofgraph_protect_completed_generation_stage();
DROP TRIGGER IF EXISTS canvas_event_cursor_insert_trigger ON canvas;
DROP FUNCTION IF EXISTS proofgraph_create_canvas_event_cursor();
ALTER TABLE graph_patch_operation_decision
    DROP CONSTRAINT IF EXISTS fk_graph_patch_decision_operation_canvas;
ALTER TABLE graph_patch_operation_decision
    DROP CONSTRAINT IF EXISTS fk_graph_patch_decision_patch_canvas;
ALTER TABLE graph_patch_operation_decision
    DROP CONSTRAINT IF EXISTS fk_graph_patch_decision_canvas;
ALTER TABLE graph_patch
    DROP CONSTRAINT IF EXISTS fk_graph_patch_regenerated_run_canvas;
ALTER TABLE graph_patch
    DROP CONSTRAINT IF EXISTS fk_graph_patch_run_canvas;
ALTER TABLE graph_patch
    DROP CONSTRAINT IF EXISTS fk_graph_patch_canvas;
ALTER TABLE generation_event
    DROP CONSTRAINT IF EXISTS fk_generation_event_run_canvas;
ALTER TABLE generation_event
    DROP CONSTRAINT IF EXISTS fk_generation_event_canvas;
ALTER TABLE canvas_event_cursor
    DROP CONSTRAINT IF EXISTS fk_canvas_event_cursor_canvas;
ALTER TABLE generation_stage
    DROP CONSTRAINT IF EXISTS fk_generation_stage_run;
ALTER TABLE generation_run
    DROP CONSTRAINT IF EXISTS fk_generation_run_canvas;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [("graph", "0002_canvas_lifecycle_delete")]

    operations = [
        migrations.CreateModel(
            name="GenerationRun",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "operation",
                    models.TextField(
                        choices=[
                            ("generate_strategies", "Generate strategies"),
                            ("research_evidence", "Research evidence"),
                            ("synthesize_opportunities", "Synthesize opportunities"),
                            ("regenerate_stale", "Regenerate stale"),
                        ]
                    ),
                ),
                ("idempotency_key", models.TextField()),
                ("request_fingerprint", models.TextField()),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("patch_ready", "Patch ready"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="queued",
                    ),
                ),
                ("current_stage", models.TextField(blank=True, null=True)),
                ("base_canvas_revision", models.BigIntegerField()),
                ("context_snapshot", models.JSONField()),
                ("context_manifest", models.JSONField()),
                ("context_hash", models.TextField()),
                ("selected_node_ids", models.JSONField(default=list)),
                ("expected_node_versions", models.JSONField(default=dict)),
                ("execution_configuration", models.JSONField()),
                ("worker_id", models.TextField(blank=True, null=True)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("lease_epoch", models.BigIntegerField(default=0)),
                ("attempt", models.IntegerField(default=0)),
                ("max_attempts", models.IntegerField(default=3)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("cancel_requested_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="generation_runs",
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "generation_run",
                "indexes": [
                    models.Index(
                        condition=models.Q(("status", "queued")),
                        fields=["created_at", "id"],
                        name="run_queued_claim_idx",
                    ),
                    models.Index(
                        condition=models.Q(("status", "running")),
                        fields=["lease_expires_at", "created_at", "id"],
                        name="run_expired_lease_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("id", "canvas"), name="uq_run_id_canvas"),
                    models.UniqueConstraint(
                        fields=("canvas", "idempotency_key"), name="uq_run_canvas_idem_key"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "operation__in",
                                [
                                    "generate_strategies",
                                    "research_evidence",
                                    "synthesize_opportunities",
                                    "regenerate_stale",
                                ],
                            )
                        ),
                        name="ck_run_operation",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                [
                                    "queued",
                                    "running",
                                    "patch_ready",
                                    "completed",
                                    "failed",
                                    "cancelled",
                                ],
                            )
                        ),
                        name="ck_run_status",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("attempt__gte", 0)), name="ck_run_attempt"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("max_attempts__gt", 0)), name="ck_run_max_attempts"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("lease_epoch__gte", 0)), name="ck_run_lease_epoch"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("heartbeat_at__isnull", False),
                                ("lease_expires_at__isnull", False),
                                ("lease_token__isnull", False),
                                ("status__in", ["running", "patch_ready"]),
                                ("worker_id__isnull", False),
                            ),
                            models.Q(
                                models.Q(("status__in", ["running", "patch_ready"]), _negated=True),
                                ("heartbeat_at__isnull", True),
                                ("lease_expires_at__isnull", True),
                                ("lease_token__isnull", True),
                                ("worker_id__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="ck_run_lease_state",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CanvasEventCursor",
            fields=[
                ("last_sequence", models.BigIntegerField(default=0)),
                (
                    "canvas",
                    models.OneToOneField(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        primary_key=True,
                        related_name="event_cursor",
                        serialize=False,
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "canvas_event_cursor",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("last_sequence__gte", 0)),
                        name="ck_canvas_event_sequence",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="GenerationStage",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("name", models.TextField()),
                ("input_hash", models.TextField()),
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
                ("attempt", models.IntegerField(default=0)),
                ("openai_response_id", models.TextField(blank=True, null=True)),
                ("output", models.JSONField(blank=True, null=True)),
                ("error", models.JSONField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "run",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="stages",
                        to="generation.generationrun",
                    ),
                ),
            ],
            options={
                "db_table": "generation_stage",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("run", "name", "input_hash"), name="uq_stage_run_name_input"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("status__in", ["running", "completed", "failed"])),
                        name="ck_stage_status",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("attempt__gt", 0)), name="ck_stage_attempt"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="GenerationEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("canvas_sequence", models.BigIntegerField()),
                ("run_sequence", models.BigIntegerField()),
                (
                    "event_type",
                    models.TextField(
                        choices=[
                            ("run.started", "Run started"),
                            ("run.resumed", "Run resumed"),
                            ("run.retry_requested", "Run retry requested"),
                            ("stage.started", "Stage started"),
                            ("stage.progress", "Stage progress"),
                            ("research.query_created", "Research query created"),
                            ("research.source_found", "Research source found"),
                            ("evidence.extracted", "Evidence extracted"),
                            ("candidate.generated", "Candidate generated"),
                            ("candidate.critiqued", "Candidate critiqued"),
                            ("patch.ready", "Patch ready"),
                            ("run.completed", "Run completed"),
                            ("run.failed", "Run failed"),
                            ("run.cancelled", "Run cancelled"),
                        ]
                    ),
                ),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="generation_events",
                        to="graph.canvas",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="events",
                        to="generation.generationrun",
                    ),
                ),
            ],
            options={
                "db_table": "generation_event",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("canvas", "canvas_sequence"), name="uq_event_canvas_sequence"
                    ),
                    models.UniqueConstraint(
                        fields=("run", "run_sequence"), name="uq_event_run_sequence"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "event_type__in",
                                [
                                    "run.started",
                                    "run.resumed",
                                    "run.retry_requested",
                                    "stage.started",
                                    "stage.progress",
                                    "research.query_created",
                                    "research.source_found",
                                    "evidence.extracted",
                                    "candidate.generated",
                                    "candidate.critiqued",
                                    "patch.ready",
                                    "run.completed",
                                    "run.failed",
                                    "run.cancelled",
                                ],
                            )
                        ),
                        name="ck_event_type",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("canvas_sequence__gt", 0)),
                        name="ck_event_canvas_sequence",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("run_sequence__gt", 0)),
                        name="ck_event_run_sequence",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="GraphPatch",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("base_canvas_revision", models.BigIntegerField()),
                ("operations", models.JSONField()),
                ("client_id_map", models.JSONField(default=dict)),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("pending", "Pending"),
                            ("applied", "Applied"),
                            ("partially_applied", "Partially applied"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="graph_patches",
                        to="graph.canvas",
                    ),
                ),
                (
                    "regenerated_by_run",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="regenerated_patches",
                        to="generation.generationrun",
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="patch",
                        to="generation.generationrun",
                    ),
                ),
            ],
            options={
                "db_table": "graph_patch",
                "constraints": [
                    models.UniqueConstraint(fields=("id", "canvas"), name="uq_patch_id_canvas"),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("status__in", ["pending", "applied", "partially_applied", "rejected"])
                        ),
                        name="ck_patch_status",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="GraphPatchOperationDecision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("operation_index", models.IntegerField()),
                (
                    "decision",
                    models.TextField(
                        choices=[
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("skipped_conflict", "Skipped conflict"),
                        ]
                    ),
                ),
                ("reason", models.TextField(blank=True, null=True)),
                ("actor_type", models.TextField()),
                ("actor_id", models.TextField(blank=True, null=True)),
                ("decided_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "canvas",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="graph_patch_decisions",
                        to="graph.canvas",
                    ),
                ),
                (
                    "graph_operation",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="patch_decisions",
                        to="graph.graphoperation",
                    ),
                ),
                (
                    "patch",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="decisions",
                        to="generation.graphpatch",
                    ),
                ),
            ],
            options={
                "db_table": "graph_patch_operation_decision",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("patch", "operation_index"), name="uq_patch_operation_decision"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("operation_index__gte", 0)),
                        name="ck_patch_decision_index",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("decision__in", ["accepted", "rejected", "skipped_conflict"])
                        ),
                        name="ck_patch_decision",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
