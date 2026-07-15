# ruff: noqa: RUF012 - Django Meta uses declarative mutable class attributes.

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from proofgraph.demo.models import DemoSession
from proofgraph.graph.models import Canvas, GraphOperation, Node


class RunOperation(models.TextChoices):
    GENERATE_STRATEGIES = "generate_strategies", "Generate strategies"
    RESEARCH_EVIDENCE = "research_evidence", "Research evidence"
    SYNTHESIZE_OPPORTUNITIES = "synthesize_opportunities", "Synthesize opportunities"
    REGENERATE_STALE = "regenerate_stale", "Regenerate stale"


class RunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    PATCH_READY = "patch_ready", "Patch ready"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class StageStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class PatchStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPLIED = "applied", "Applied"
    PARTIALLY_APPLIED = "partially_applied", "Partially applied"
    REJECTED = "rejected", "Rejected"


class PatchDecision(models.TextChoices):
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    SKIPPED_CONFLICT = "skipped_conflict", "Skipped conflict"


class SourceIngestionStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class GenerationEventType(models.TextChoices):
    RUN_STARTED = "run.started", "Run started"
    RUN_RESUMED = "run.resumed", "Run resumed"
    RUN_RETRY_REQUESTED = "run.retry_requested", "Run retry requested"
    STAGE_STARTED = "stage.started", "Stage started"
    STAGE_PROGRESS = "stage.progress", "Stage progress"
    RESEARCH_QUERY_CREATED = "research.query_created", "Research query created"
    RESEARCH_SOURCE_FOUND = "research.source_found", "Research source found"
    EVIDENCE_EXTRACTED = "evidence.extracted", "Evidence extracted"
    CANDIDATE_GENERATED = "candidate.generated", "Candidate generated"
    CANDIDATE_CRITIQUED = "candidate.critiqued", "Candidate critiqued"
    PATCH_READY = "patch.ready", "Patch ready"
    RUN_COMPLETED = "run.completed", "Run completed"
    RUN_FAILED = "run.failed", "Run failed"
    RUN_CANCELLED = "run.cancelled", "Run cancelled"


class GenerationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="generation_runs",
    )
    demo_session = models.ForeignKey(
        DemoSession,
        blank=True,
        null=True,
        on_delete=models.DO_NOTHING,
        related_name="generation_runs",
    )
    operation = models.TextField(choices=RunOperation.choices)
    idempotency_key = models.TextField()
    request_fingerprint = models.TextField()
    status = models.TextField(choices=RunStatus.choices, default=RunStatus.QUEUED)
    current_stage = models.TextField(blank=True, null=True)  # noqa: DJ001 - null is state
    base_canvas_revision = models.BigIntegerField()
    context_snapshot = models.JSONField()
    context_manifest = models.JSONField()
    context_hash = models.TextField()
    events_after_sequence = models.BigIntegerField(default=0)
    selected_node_ids = models.JSONField(default=list)
    expected_node_versions = models.JSONField(default=dict)
    execution_configuration = models.JSONField()
    worker_id = models.TextField(blank=True, null=True)  # noqa: DJ001 - lease is absent or present
    lease_token = models.UUIDField(blank=True, null=True)
    lease_epoch = models.BigIntegerField(default=0)
    attempt = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    cancel_requested_at = models.DateTimeField(blank=True, null=True)
    error = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "generation_run"
        constraints = [
            models.UniqueConstraint(fields=["id", "canvas"], name="uq_run_id_canvas"),
            models.UniqueConstraint(
                fields=["canvas", "idempotency_key"],
                name="uq_run_canvas_idem_key",
            ),
            models.CheckConstraint(
                condition=Q(operation__in=RunOperation.values),
                name="ck_run_operation",
            ),
            models.CheckConstraint(
                condition=Q(status__in=RunStatus.values),
                name="ck_run_status",
            ),
            models.CheckConstraint(condition=Q(attempt__gte=0), name="ck_run_attempt"),
            models.CheckConstraint(condition=Q(max_attempts__gt=0), name="ck_run_max_attempts"),
            models.CheckConstraint(condition=Q(lease_epoch__gte=0), name="ck_run_lease_epoch"),
            models.CheckConstraint(
                condition=Q(events_after_sequence__gte=0),
                name="ck_run_events_after_sequence",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=[RunStatus.RUNNING, RunStatus.PATCH_READY],
                        worker_id__isnull=False,
                        lease_token__isnull=False,
                        heartbeat_at__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | (
                        ~Q(status__in=[RunStatus.RUNNING, RunStatus.PATCH_READY])
                        & Q(
                            worker_id__isnull=True,
                            lease_token__isnull=True,
                            heartbeat_at__isnull=True,
                            lease_expires_at__isnull=True,
                        )
                    )
                ),
                name="ck_run_lease_state",
            ),
        ]
        indexes = [
            models.Index(
                fields=["created_at", "id"],
                condition=Q(status=RunStatus.QUEUED),
                name="run_queued_claim_idx",
            ),
            models.Index(
                fields=["lease_expires_at", "created_at", "id"],
                condition=Q(status=RunStatus.RUNNING),
                name="run_expired_lease_idx",
            ),
            models.Index(
                fields=["demo_session", "status"],
                condition=Q(status__in=[RunStatus.QUEUED, RunStatus.RUNNING]),
                name="run_demo_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.operation}:{self.id}:{self.status}"


class GenerationStage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        GenerationRun,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="stages",
    )
    name = models.TextField()
    input_hash = models.TextField()
    status = models.TextField(choices=StageStatus.choices)
    attempt = models.IntegerField(default=0)
    openai_response_id = models.TextField(blank=True, null=True)  # noqa: DJ001 - optional identity
    output = models.JSONField(blank=True, null=True)
    error = models.JSONField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "generation_stage"
        constraints = [
            models.UniqueConstraint(
                fields=["run", "name", "input_hash"],
                name="uq_stage_run_name_input",
            ),
            models.CheckConstraint(
                condition=Q(status__in=StageStatus.values),
                name="ck_stage_status",
            ),
            models.CheckConstraint(condition=Q(attempt__gt=0), name="ck_stage_attempt"),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.name}:{self.status}"


class CanvasEventCursor(models.Model):
    canvas = models.OneToOneField(
        Canvas,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        primary_key=True,
        related_name="event_cursor",
    )
    last_sequence = models.BigIntegerField(default=0)

    class Meta:
        db_table = "canvas_event_cursor"
        constraints = [
            models.CheckConstraint(
                condition=Q(last_sequence__gte=0),
                name="ck_canvas_event_sequence",
            )
        ]

    def __str__(self) -> str:
        return f"{self.canvas_id}:{self.last_sequence}"


class GenerationEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="generation_events",
    )
    run = models.ForeignKey(
        GenerationRun,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="events",
    )
    canvas_sequence = models.BigIntegerField()
    run_sequence = models.BigIntegerField()
    event_type = models.TextField(choices=GenerationEventType.choices)
    payload = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "generation_event"
        constraints = [
            models.UniqueConstraint(
                fields=["canvas", "canvas_sequence"],
                name="uq_event_canvas_sequence",
            ),
            models.UniqueConstraint(
                fields=["run", "run_sequence"],
                name="uq_event_run_sequence",
            ),
            models.CheckConstraint(
                condition=Q(event_type__in=GenerationEventType.values),
                name="ck_event_type",
            ),
            models.CheckConstraint(
                condition=Q(canvas_sequence__gt=0),
                name="ck_event_canvas_sequence",
            ),
            models.CheckConstraint(
                condition=Q(run_sequence__gt=0),
                name="ck_event_run_sequence",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.canvas_id}:{self.canvas_sequence}:{self.event_type}"


class ResearchQueryCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        db_index=False,
        on_delete=models.DO_NOTHING,
        related_name="research_query_cache_entries",
    )
    normalized_query = models.TextField()
    provider_identity = models.TextField()
    strategy_version = models.TextField()
    prompt_version = models.TextField()
    context_hash = models.TextField()
    result = models.JSONField()
    retrieved_at = models.DateTimeField()
    fresh_until = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "research_query_cache"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "canvas",
                    "normalized_query",
                    "provider_identity",
                    "strategy_version",
                    "prompt_version",
                    "context_hash",
                ],
                name="uq_research_query_cache_key",
            ),
            models.CheckConstraint(
                condition=Q(
                    retrieved_at__lte=models.F("fresh_until"),
                    fresh_until__lte=models.F("expires_at"),
                ),
                name="ck_research_cache_times",
            ),
        ]
        indexes = [
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
        ]

    def __str__(self) -> str:
        return f"{self.canvas_id}:{self.provider_identity}:{self.normalized_query}"


class SourceContentCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        db_index=False,
        on_delete=models.DO_NOTHING,
        related_name="source_content_cache_entries",
    )
    normalized_url = models.TextField()
    content_hash = models.TextField()
    retained_content = models.TextField(blank=True, null=True)  # noqa: DJ001
    retrieval_metadata = models.JSONField()
    retrieved_at = models.DateTimeField()
    fresh_until = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "source_content_cache"
        constraints = [
            models.UniqueConstraint(
                fields=["canvas", "normalized_url", "content_hash"],
                name="uq_source_content_cache_key",
            ),
            models.CheckConstraint(
                condition=Q(retained_content__isnull=True),
                name="ck_source_cache_no_content",
            ),
            models.CheckConstraint(
                condition=Q(
                    retrieved_at__lte=models.F("fresh_until"),
                    fresh_until__lte=models.F("expires_at"),
                ),
                name="ck_source_cache_times",
            ),
        ]
        indexes = [
            models.Index(
                fields=["canvas", "normalized_url", "fresh_until", "retrieved_at"],
                name="source_cache_fresh_idx",
            ),
            models.Index(fields=["expires_at", "id"], name="source_cache_expiry_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.canvas_id}:{self.normalized_url}:{self.content_hash}"


class SourceIngestionRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        db_index=False,
        on_delete=models.DO_NOTHING,
        related_name="source_ingestion_requests",
    )
    operation_key = models.TextField()
    request_fingerprint = models.TextField()
    status = models.TextField(choices=SourceIngestionStatus.choices)
    worker_id = models.TextField(blank=True, null=True)  # noqa: DJ001
    lease_token = models.UUIDField(blank=True, null=True)
    lease_epoch = models.BigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    result_source_node = models.ForeignKey(
        Node,
        blank=True,
        null=True,
        db_constraint=False,
        db_index=False,
        on_delete=models.DO_NOTHING,
        related_name="completed_source_ingestions",
    )
    error = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "source_ingestion_request"
        constraints = [
            models.UniqueConstraint(
                fields=["canvas", "operation_key"],
                name="uq_source_ingestion_operation",
            ),
            models.CheckConstraint(
                condition=Q(status__in=SourceIngestionStatus.values),
                name="ck_source_ingestion_status",
            ),
            models.CheckConstraint(
                condition=Q(lease_epoch__gte=0),
                name="ck_source_ingestion_epoch",
            ),
            models.CheckConstraint(
                condition=(~Q(status=SourceIngestionStatus.RUNNING) | Q(lease_epoch__gt=0)),
                name="ck_source_ingestion_run_epoch",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=SourceIngestionStatus.RUNNING,
                        worker_id__isnull=False,
                        lease_token__isnull=False,
                        lease_expires_at__isnull=False,
                        result_source_node__isnull=True,
                        error__isnull=True,
                    )
                    | Q(
                        status=SourceIngestionStatus.COMPLETED,
                        worker_id__isnull=True,
                        lease_token__isnull=True,
                        lease_expires_at__isnull=True,
                        result_source_node__isnull=False,
                        error__isnull=True,
                    )
                    | Q(
                        status=SourceIngestionStatus.FAILED,
                        worker_id__isnull=True,
                        lease_token__isnull=True,
                        lease_expires_at__isnull=True,
                        result_source_node__isnull=True,
                        error__isnull=False,
                    )
                ),
                name="ck_source_ingestion_lifecycle",
            ),
        ]
        indexes = [
            models.Index(
                fields=["lease_expires_at", "id"],
                condition=Q(status=SourceIngestionStatus.RUNNING),
                name="source_ingestion_reclaim_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.canvas_id}:{self.operation_key}:{self.status}"


class GraphPatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(
        GenerationRun,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="patch",
    )
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="graph_patches",
    )
    base_canvas_revision = models.BigIntegerField()
    operations = models.JSONField()
    regeneration_target_ids = models.JSONField(default=list)
    permitted_stale_resolution_ids = models.JSONField(default=list)
    client_id_map = models.JSONField(default=dict)
    status = models.TextField(choices=PatchStatus.choices, default=PatchStatus.PENDING)
    regenerated_by_run = models.ForeignKey(
        GenerationRun,
        blank=True,
        null=True,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="regenerated_patches",
    )
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(blank=True, null=True)
    applied_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "graph_patch"
        constraints = [
            models.UniqueConstraint(fields=["id", "canvas"], name="uq_patch_id_canvas"),
            models.CheckConstraint(
                condition=Q(status__in=PatchStatus.values),
                name="ck_patch_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.status}"


class GraphPatchOperationDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patch = models.ForeignKey(
        GraphPatch,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="decisions",
    )
    canvas = models.ForeignKey(
        Canvas,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="graph_patch_decisions",
    )
    operation_index = models.IntegerField()
    decision = models.TextField(choices=PatchDecision.choices)
    reason = models.TextField(blank=True, null=True)  # noqa: DJ001 - optional audit reason
    actor_type = models.TextField()
    actor_id = models.TextField(blank=True, null=True)  # noqa: DJ001 - actor may be anonymous
    graph_operation = models.ForeignKey(
        GraphOperation,
        blank=True,
        null=True,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="patch_decisions",
    )
    decided_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "graph_patch_operation_decision"
        constraints = [
            models.UniqueConstraint(
                fields=["patch", "operation_index"],
                name="uq_patch_operation_decision",
            ),
            models.CheckConstraint(
                condition=Q(operation_index__gte=0),
                name="ck_patch_decision_index",
            ),
            models.CheckConstraint(
                condition=Q(decision__in=PatchDecision.values),
                name="ck_patch_decision",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.patch_id}:{self.operation_index}:{self.decision}"
