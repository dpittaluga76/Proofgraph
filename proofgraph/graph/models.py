# ruff: noqa: RUF012 - Django Meta uses declarative mutable class attributes.

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class NodeKind(models.TextChoices):
    GOAL = "goal", "Goal"
    CONSTRAINT = "constraint", "Constraint"
    STRATEGY = "strategy", "Strategy"
    SOURCE = "source", "Source"
    CLAIM = "claim", "Claim"
    OPPORTUNITY = "opportunity", "Opportunity"
    ASSUMPTION = "assumption", "Assumption"
    RISK = "risk", "Risk"
    VALIDATION_EXPERIMENT = "validation_experiment", "Validation experiment"
    GENERATION_PLACEHOLDER = "generation_placeholder", "Generation placeholder"


class EdgeKind(models.TextChoices):
    SUPPORTS = "supports", "Supports"
    CONTRADICTS = "contradicts", "Contradicts"
    DERIVED_FROM = "derived_from", "Derived from"
    CONSTRAINED_BY = "constrained_by", "Constrained by"
    EVOLVES_INTO = "evolves_into", "Evolves into"
    REQUIRES_VALIDATION = "requires_validation", "Requires validation"
    EXTRACTED_FROM = "extracted_from", "Extracted from"


class Canvas(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    revision = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "canvas"

    def __str__(self) -> str:
        return self.title


class Node(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canvas = models.ForeignKey(Canvas, on_delete=models.DO_NOTHING, related_name="nodes")
    kind = models.TextField(choices=NodeKind.choices)
    title = models.TextField()
    body = models.TextField(blank=True, null=True)  # noqa: DJ001 - nullable by design
    metadata = models.JSONField(default=dict)
    branch_root = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        db_column="branch_root_node_id",
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="anchored_constraints",
    )
    position = models.JSONField(default=dict)
    stale = models.BooleanField(default=False)
    stale_since_revision = models.BigIntegerField(blank=True, null=True)
    version = models.BigIntegerField(default=1)
    position_version = models.BigIntegerField(default=1)
    context_token_count = models.IntegerField(blank=True, null=True)
    context_representation_version = models.IntegerField(default=1)
    context_content_hash = models.TextField(  # noqa: DJ001 - nullable cache by design
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    semantic_updated_at = models.DateTimeField(default=timezone.now)
    position_updated_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "node"
        constraints = [
            models.UniqueConstraint(fields=["id", "canvas"], name="uq_node_id_canvas"),
            models.CheckConstraint(condition=Q(kind__in=NodeKind.values), name="ck_node_kind"),
            models.CheckConstraint(
                condition=(
                    Q(stale=False, stale_since_revision__isnull=True)
                    | Q(stale=True, stale_since_revision__isnull=False)
                ),
                name="ck_node_stale_revision",
            ),
        ]
        indexes = [
            models.Index(fields=["canvas", "kind"], name="node_canvas_kind_idx"),
            models.Index(
                fields=["canvas", "id"],
                condition=Q(stale=True),
                name="node_canvas_stale_idx",
            ),
            models.Index(
                fields=["canvas", "branch_root"],
                condition=Q(branch_root__isnull=False),
                name="node_branch_root_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.title}"


class Edge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canvas = models.ForeignKey(Canvas, on_delete=models.DO_NOTHING, related_name="edges")
    source = models.ForeignKey(
        Node,
        db_column="source_node_id",
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="outgoing_edges",
    )
    target = models.ForeignKey(
        Node,
        db_column="target_node_id",
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="incoming_edges",
    )
    kind = models.TextField(choices=EdgeKind.choices)
    metadata = models.JSONField(default=dict)
    version = models.BigIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "edge"
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=EdgeKind.values), name="ck_edge_kind"),
        ]
        indexes = [
            models.Index(fields=["canvas", "source", "kind"], name="edge_canvas_source_kind_idx"),
            models.Index(fields=["canvas", "target", "kind"], name="edge_canvas_target_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.source_id}->{self.target_id}"


class GraphOperation(models.Model):
    id = models.BigAutoField(primary_key=True)
    canvas = models.ForeignKey(Canvas, on_delete=models.DO_NOTHING, related_name="operations")
    actor_type = models.TextField()
    actor_id = models.TextField(blank=True, null=True)  # noqa: DJ001 - actor may be anonymous
    operation_key = models.TextField()
    request_fingerprint = models.TextField()
    operation_type = models.TextField()
    payload = models.JSONField()
    result_payload = models.JSONField()
    canvas_revision = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "graph_operation"
        constraints = [
            models.UniqueConstraint(fields=["id", "canvas"], name="uq_graph_op_id_canvas"),
            models.UniqueConstraint(
                fields=["canvas", "actor_type", "operation_key"],
                name="uq_graph_op_actor_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["canvas", "canvas_revision", "id"],
                name="graph_op_canvas_revision_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.canvas_id}:{self.canvas_revision}:{self.operation_type}"


class NodeStalenessCause(models.Model):
    id = models.BigAutoField(primary_key=True)
    canvas = models.ForeignKey(
        Canvas,
        on_delete=models.DO_NOTHING,
        related_name="node_staleness_causes",
    )
    node = models.ForeignKey(
        Node,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="staleness_causes",
    )
    cause_graph_operation = models.ForeignKey(
        GraphOperation,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="caused_staleness",
    )
    origin_entity_type = models.TextField()
    origin_entity_id = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now)
    cleared_by_graph_operation = models.ForeignKey(
        GraphOperation,
        blank=True,
        null=True,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="cleared_staleness",
    )
    cleared_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "node_staleness_cause"
        constraints = [
            models.UniqueConstraint(fields=["id", "canvas"], name="uq_stale_cause_id_canvas"),
            models.UniqueConstraint(
                fields=["node", "cause_graph_operation"],
                name="uq_node_stale_cause_operation",
            ),
            models.CheckConstraint(
                condition=(
                    Q(cleared_by_graph_operation__isnull=True, cleared_at__isnull=True)
                    | Q(cleared_by_graph_operation__isnull=False, cleared_at__isnull=False)
                ),
                name="ck_stale_clearing_pair",
            ),
        ]
        indexes = [
            models.Index(
                fields=["canvas", "node"],
                condition=Q(cleared_at__isnull=True),
                name="node_staleness_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.node_id}:{self.cause_graph_operation_id}"
