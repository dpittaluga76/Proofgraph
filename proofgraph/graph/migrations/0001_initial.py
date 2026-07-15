# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

FORWARD_SQL = """
ALTER TABLE graph_operation ALTER COLUMN id SET GENERATED ALWAYS;
ALTER TABLE node_staleness_cause ALTER COLUMN id SET GENERATED ALWAYS;

ALTER TABLE node
    ADD CONSTRAINT ck_node_constraint_metadata
    CHECK (
        (
            kind = 'constraint'
            AND metadata ? 'context_scope'
            AND metadata ? 'pinned'
            AND metadata->>'context_scope' IN ('global', 'branch')
            AND jsonb_typeof(metadata->'pinned') = 'boolean'
            AND (
                (metadata->>'context_scope' = 'global' AND branch_root_node_id IS NULL)
                OR
                (metadata->>'context_scope' = 'branch' AND branch_root_node_id IS NOT NULL)
            )
        )
        OR
        (
            kind <> 'constraint'
            AND NOT (metadata ? 'context_scope')
            AND NOT (metadata ? 'pinned')
            AND branch_root_node_id IS NULL
        )
    );

ALTER TABLE node
    ADD CONSTRAINT fk_node_branch_root_canvas
    FOREIGN KEY (branch_root_node_id, canvas_id)
    REFERENCES node (id, canvas_id)
    ON DELETE NO ACTION
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE edge
    ADD CONSTRAINT fk_edge_source_canvas
    FOREIGN KEY (source_node_id, canvas_id)
    REFERENCES node (id, canvas_id)
    ON DELETE NO ACTION
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE edge
    ADD CONSTRAINT fk_edge_target_canvas
    FOREIGN KEY (target_node_id, canvas_id)
    REFERENCES node (id, canvas_id)
    ON DELETE NO ACTION
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE node_staleness_cause
    ADD CONSTRAINT fk_stale_node_canvas
    FOREIGN KEY (node_id, canvas_id)
    REFERENCES node (id, canvas_id)
    ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE node_staleness_cause
    ADD CONSTRAINT fk_stale_cause_op_canvas
    FOREIGN KEY (cause_graph_operation_id, canvas_id)
    REFERENCES graph_operation (id, canvas_id)
    ON DELETE NO ACTION
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE node_staleness_cause
    ADD CONSTRAINT fk_stale_cleared_op_canvas
    FOREIGN KEY (cleared_by_graph_operation_id, canvas_id)
    REFERENCES graph_operation (id, canvas_id)
    ON DELETE NO ACTION
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION proofgraph_validate_branch_root()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    root_canvas_id uuid;
    root_kind text;
BEGIN
    IF NEW.kind = 'constraint' AND NEW.metadata->>'context_scope' = 'branch' THEN
        SELECT canvas_id, kind
        INTO root_canvas_id, root_kind
        FROM node
        WHERE id = NEW.branch_root_node_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'branch root does not exist'
                USING ERRCODE = '23503';
        END IF;

        IF root_canvas_id IS DISTINCT FROM NEW.canvas_id THEN
            RAISE EXCEPTION 'branch root must belong to the same canvas'
                USING ERRCODE = '23514';
        END IF;

        IF root_kind NOT IN ('strategy', 'claim', 'opportunity') THEN
            RAISE EXCEPTION 'branch root kind must be strategy, claim, or opportunity'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.kind NOT IN ('strategy', 'claim', 'opportunity')
       AND EXISTS (SELECT 1 FROM node WHERE branch_root_node_id = NEW.id) THEN
        RAISE EXCEPTION 'referenced branch root kind must remain strategy, claim, or opportunity'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER node_branch_root_kind_trigger
AFTER INSERT OR UPDATE ON node
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION proofgraph_validate_branch_root();

CREATE FUNCTION proofgraph_validate_stale_consistency()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_node_id uuid;
    node_is_stale boolean;
    active_cause_exists boolean;
BEGIN
    IF TG_TABLE_NAME = 'node' THEN
        target_node_id := NEW.id;
    ELSIF TG_OP = 'DELETE' THEN
        target_node_id := OLD.node_id;
    ELSE
        target_node_id := NEW.node_id;
    END IF;

    SELECT stale
    INTO node_is_stale
    FROM node
    WHERE id = target_node_id;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM node_staleness_cause
        WHERE node_id = target_node_id AND cleared_at IS NULL
    )
    INTO active_cause_exists;

    IF node_is_stale IS DISTINCT FROM active_cause_exists THEN
        RAISE EXCEPTION 'node stale flag must match active staleness causes'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER node_stale_consistency_trigger
AFTER INSERT OR UPDATE OF stale ON node
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION proofgraph_validate_stale_consistency();

CREATE CONSTRAINT TRIGGER cause_stale_consistency_trigger
AFTER INSERT OR UPDATE OR DELETE ON node_staleness_cause
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION proofgraph_validate_stale_consistency();

CREATE FUNCTION proofgraph_protect_graph_operation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'graph operations are append-only'
            USING ERRCODE = '55000';
    END IF;

    IF EXISTS (SELECT 1 FROM canvas WHERE id = OLD.canvas_id) THEN
        RAISE EXCEPTION 'graph operations may be deleted only with their canvas'
            USING ERRCODE = '55000';
    END IF;

    RETURN OLD;
END;
$$;

CREATE TRIGGER graph_operation_append_only_trigger
BEFORE UPDATE OR DELETE ON graph_operation
FOR EACH ROW
EXECUTE FUNCTION proofgraph_protect_graph_operation();

CREATE FUNCTION proofgraph_protect_staleness_cause()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.canvas_id IS DISTINCT FROM OLD.canvas_id
           OR NEW.node_id IS DISTINCT FROM OLD.node_id
           OR NEW.cause_graph_operation_id IS DISTINCT FROM OLD.cause_graph_operation_id
           OR NEW.origin_entity_type IS DISTINCT FROM OLD.origin_entity_type
           OR NEW.origin_entity_id IS DISTINCT FROM OLD.origin_entity_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR OLD.cleared_at IS NOT NULL
           OR NEW.cleared_at IS NULL
           OR NEW.cleared_by_graph_operation_id IS NULL THEN
            RAISE EXCEPTION 'staleness causes are append-only except for one clearing transition'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;

    IF EXISTS (SELECT 1 FROM canvas WHERE id = OLD.canvas_id)
       AND EXISTS (SELECT 1 FROM node WHERE id = OLD.node_id) THEN
        RAISE EXCEPTION 'staleness causes may be deleted only with their node or canvas'
            USING ERRCODE = '55000';
    END IF;

    RETURN OLD;
END;
$$;

CREATE TRIGGER staleness_cause_append_only_trigger
BEFORE UPDATE OR DELETE ON node_staleness_cause
FOR EACH ROW
EXECUTE FUNCTION proofgraph_protect_staleness_cause();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS staleness_cause_append_only_trigger ON node_staleness_cause;
DROP FUNCTION IF EXISTS proofgraph_protect_staleness_cause();
DROP TRIGGER IF EXISTS graph_operation_append_only_trigger ON graph_operation;
DROP FUNCTION IF EXISTS proofgraph_protect_graph_operation();
DROP TRIGGER IF EXISTS cause_stale_consistency_trigger ON node_staleness_cause;
DROP TRIGGER IF EXISTS node_stale_consistency_trigger ON node;
DROP FUNCTION IF EXISTS proofgraph_validate_stale_consistency();
DROP TRIGGER IF EXISTS node_branch_root_kind_trigger ON node;
DROP FUNCTION IF EXISTS proofgraph_validate_branch_root();
ALTER TABLE node_staleness_cause DROP CONSTRAINT IF EXISTS fk_stale_cleared_op_canvas;
ALTER TABLE node_staleness_cause DROP CONSTRAINT IF EXISTS fk_stale_cause_op_canvas;
ALTER TABLE node_staleness_cause DROP CONSTRAINT IF EXISTS fk_stale_node_canvas;
ALTER TABLE edge DROP CONSTRAINT IF EXISTS fk_edge_target_canvas;
ALTER TABLE edge DROP CONSTRAINT IF EXISTS fk_edge_source_canvas;
ALTER TABLE node DROP CONSTRAINT IF EXISTS fk_node_branch_root_canvas;
ALTER TABLE node DROP CONSTRAINT IF EXISTS ck_node_constraint_metadata;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Canvas",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("title", models.TextField()),
                ("revision", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={"db_table": "canvas"},
        ),
        migrations.CreateModel(
            name="Node",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "kind",
                    models.TextField(
                        choices=[
                            ("goal", "Goal"),
                            ("constraint", "Constraint"),
                            ("strategy", "Strategy"),
                            ("source", "Source"),
                            ("claim", "Claim"),
                            ("opportunity", "Opportunity"),
                            ("assumption", "Assumption"),
                            ("risk", "Risk"),
                            ("validation_experiment", "Validation experiment"),
                            ("generation_placeholder", "Generation placeholder"),
                        ]
                    ),
                ),
                ("title", models.TextField()),
                ("body", models.TextField(blank=True, null=True)),
                ("metadata", models.JSONField(default=dict)),
                ("position", models.JSONField(default=dict)),
                ("stale", models.BooleanField(default=False)),
                ("stale_since_revision", models.BigIntegerField(blank=True, null=True)),
                ("version", models.BigIntegerField(default=1)),
                ("position_version", models.BigIntegerField(default=1)),
                ("context_token_count", models.IntegerField(blank=True, null=True)),
                ("context_representation_version", models.IntegerField(default=1)),
                ("context_content_hash", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "semantic_updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "position_updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "branch_root",
                    models.ForeignKey(
                        blank=True,
                        db_column="branch_root_node_id",
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="anchored_constraints",
                        to="graph.node",
                    ),
                ),
                (
                    "canvas",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nodes",
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "node",
                "indexes": [
                    models.Index(fields=["canvas", "kind"], name="node_canvas_kind_idx"),
                    models.Index(
                        condition=models.Q(("stale", True)),
                        fields=["canvas", "id"],
                        name="node_canvas_stale_idx",
                    ),
                    models.Index(
                        condition=models.Q(("branch_root__isnull", False)),
                        fields=["canvas", "branch_root"],
                        name="node_branch_root_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("id", "canvas"), name="uq_node_id_canvas"),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "kind__in",
                                [
                                    "goal",
                                    "constraint",
                                    "strategy",
                                    "source",
                                    "claim",
                                    "opportunity",
                                    "assumption",
                                    "risk",
                                    "validation_experiment",
                                    "generation_placeholder",
                                ],
                            )
                        ),
                        name="ck_node_kind",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("stale", False), ("stale_since_revision__isnull", True)),
                            models.Q(("stale", True), ("stale_since_revision__isnull", False)),
                            _connector="OR",
                        ),
                        name="ck_node_stale_revision",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Edge",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "kind",
                    models.TextField(
                        choices=[
                            ("supports", "Supports"),
                            ("contradicts", "Contradicts"),
                            ("derived_from", "Derived from"),
                            ("constrained_by", "Constrained by"),
                            ("evolves_into", "Evolves into"),
                            ("requires_validation", "Requires validation"),
                            ("extracted_from", "Extracted from"),
                        ]
                    ),
                ),
                ("metadata", models.JSONField(default=dict)),
                ("version", models.BigIntegerField(default=1)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "canvas",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="edges",
                        to="graph.canvas",
                    ),
                ),
                (
                    "source",
                    models.ForeignKey(
                        db_column="source_node_id",
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="outgoing_edges",
                        to="graph.node",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        db_column="target_node_id",
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="incoming_edges",
                        to="graph.node",
                    ),
                ),
            ],
            options={
                "db_table": "edge",
                "indexes": [
                    models.Index(
                        fields=["canvas", "source", "kind"],
                        name="edge_canvas_source_kind_idx",
                    ),
                    models.Index(
                        fields=["canvas", "target", "kind"],
                        name="edge_canvas_target_kind_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "kind__in",
                                [
                                    "supports",
                                    "contradicts",
                                    "derived_from",
                                    "constrained_by",
                                    "evolves_into",
                                    "requires_validation",
                                    "extracted_from",
                                ],
                            )
                        ),
                        name="ck_edge_kind",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="GraphOperation",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("actor_type", models.TextField()),
                ("actor_id", models.TextField(blank=True, null=True)),
                ("operation_key", models.TextField()),
                ("request_fingerprint", models.TextField()),
                ("operation_type", models.TextField()),
                ("payload", models.JSONField()),
                ("result_payload", models.JSONField()),
                ("canvas_revision", models.BigIntegerField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "canvas",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operations",
                        to="graph.canvas",
                    ),
                ),
            ],
            options={
                "db_table": "graph_operation",
                "indexes": [
                    models.Index(
                        fields=["canvas", "canvas_revision", "id"],
                        name="graph_op_canvas_revision_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("id", "canvas"), name="uq_graph_op_id_canvas"),
                    models.UniqueConstraint(
                        fields=("canvas", "actor_type", "operation_key"),
                        name="uq_graph_op_actor_key",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="NodeStalenessCause",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("origin_entity_type", models.TextField()),
                ("origin_entity_id", models.UUIDField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("cleared_at", models.DateTimeField(blank=True, null=True)),
                (
                    "canvas",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="node_staleness_causes",
                        to="graph.canvas",
                    ),
                ),
                (
                    "cause_graph_operation",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="caused_staleness",
                        to="graph.graphoperation",
                    ),
                ),
                (
                    "cleared_by_graph_operation",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="cleared_staleness",
                        to="graph.graphoperation",
                    ),
                ),
                (
                    "node",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="staleness_causes",
                        to="graph.node",
                    ),
                ),
            ],
            options={
                "db_table": "node_staleness_cause",
                "indexes": [
                    models.Index(
                        condition=models.Q(("cleared_at__isnull", True)),
                        fields=["canvas", "node"],
                        name="node_staleness_active_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("id", "canvas"), name="uq_stale_cause_id_canvas"
                    ),
                    models.UniqueConstraint(
                        fields=("node", "cause_graph_operation"),
                        name="uq_node_stale_cause_operation",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("cleared_at__isnull", True),
                                ("cleared_by_graph_operation__isnull", True),
                            ),
                            models.Q(
                                ("cleared_at__isnull", False),
                                ("cleared_by_graph_operation__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="ck_stale_clearing_pair",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
