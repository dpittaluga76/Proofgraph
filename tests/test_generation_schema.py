import uuid
from collections.abc import Callable

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from proofgraph.generation.events import append_event_locked
from proofgraph.generation.models import (
    CanvasEventCursor,
    GenerationEvent,
    GenerationEventType,
    GenerationRun,
    GenerationStage,
    GraphPatch,
    GraphPatchOperationDecision,
    RunOperation,
    RunStatus,
    StageStatus,
)
from proofgraph.generation.retention import RetentionPolicyError, validate_retained_payload
from proofgraph.graph.models import Canvas, GraphOperation

pytestmark = pytest.mark.django_db(transaction=True)


def make_run(canvas: Canvas, *, key: str | None = None) -> GenerationRun:
    return GenerationRun.objects.create(
        canvas=canvas,
        operation=RunOperation.GENERATE_STRATEGIES,
        idempotency_key=key or str(uuid.uuid4()),
        request_fingerprint=str(uuid.uuid4()),
        base_canvas_revision=canvas.revision,
        context_snapshot={"nodes": []},
        context_manifest={"included_node_ids": []},
        context_hash="context-hash",
        selected_node_ids=[],
        expected_node_versions={},
        execution_configuration={"profile_id": "test"},
    )


def test_generation_migration_backfills_preexisting_canvas_cursor() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([("generation", None)])
    old_apps = executor.loader.project_state([("graph", "0002_canvas_lifecycle_delete")]).apps
    OldCanvas = old_apps.get_model("graph", "Canvas")
    canvas = OldCanvas.objects.create(title="Canvas predating generation migration")

    try:
        executor = MigrationExecutor(connection)
        executor.migrate([("generation", "0001_initial")])
        new_apps = executor.loader.project_state([("generation", "0001_initial")]).apps
        Cursor = new_apps.get_model("generation", "CanvasEventCursor")

        assert Cursor.objects.filter(canvas_id=canvas.id, last_sequence=0).count() == 1
    finally:
        latest_executor = MigrationExecutor(connection)
        latest_executor.migrate(latest_executor.loader.graph.leaf_nodes())


def test_graph_patch_contract_migration_backfills_or_rejects_legacy_regeneration() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([("generation", "0005_graphpatch_regeneration_contract")])
    old_apps = executor.loader.project_state(
        [("generation", "0005_graphpatch_regeneration_contract")]
    ).apps
    OldCanvas = old_apps.get_model("graph", "Canvas")
    OldRun = old_apps.get_model("generation", "GenerationRun")
    OldStage = old_apps.get_model("generation", "GenerationStage")
    OldPatch = old_apps.get_model("generation", "GraphPatch")
    canvas = OldCanvas.objects.create(title="Legacy regeneration contracts")

    def legacy_run(key: str):  # type: ignore[no-untyped-def]
        return OldRun.objects.create(
            canvas_id=canvas.id,
            operation="regenerate_stale",
            idempotency_key=key,
            request_fingerprint=key,
            base_canvas_revision=0,
            context_snapshot={"nodes": []},
            context_manifest={"included_node_ids": []},
            context_hash=key,
            selected_node_ids=[],
            expected_node_versions={},
            execution_configuration={"profile_id": "replay_v1"},
        )

    backfilled_run = legacy_run("legacy-backfilled")
    rejected_run = legacy_run("legacy-rejected")
    target_ids = ["claim_1", "claim_2"]
    OldStage.objects.create(
        run_id=backfilled_run.id,
        name="constructing_patch",
        input_hash="legacy-patch",
        status="completed",
        attempt=1,
        output={
            "stage_name": "constructing_patch",
            "output": {
                "regeneration_target_ids": target_ids,
                "resolves_stale_node_ids": target_ids,
            },
        },
    )
    backfilled_patch = OldPatch.objects.create(
        run_id=backfilled_run.id,
        canvas_id=canvas.id,
        base_canvas_revision=0,
        operations=[],
    )
    rejected_patch = OldPatch.objects.create(
        run_id=rejected_run.id,
        canvas_id=canvas.id,
        base_canvas_revision=0,
        operations=[],
    )

    try:
        executor = MigrationExecutor(connection)
        executor.migrate([("generation", "0006_protect_graph_patch_contract")])
        new_apps = executor.loader.project_state(
            [("generation", "0006_protect_graph_patch_contract")]
        ).apps
        NewPatch = new_apps.get_model("generation", "GraphPatch")
        migrated = NewPatch.objects.get(pk=backfilled_patch.id)
        incompatible = NewPatch.objects.get(pk=rejected_patch.id)

        assert migrated.regeneration_target_ids == target_ids
        assert migrated.permitted_stale_resolution_ids == target_ids
        assert migrated.status == "pending"
        assert incompatible.status == "rejected"
        assert incompatible.decided_at is not None
    finally:
        latest_executor = MigrationExecutor(connection)
        latest_executor.migrate(latest_executor.loader.graph.leaf_nodes())


def enforce_constraints() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def assert_rejected(action: Callable[[], object]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        action()
        enforce_constraints()


def test_canvas_cursor_trigger_creates_exactly_one_cursor() -> None:
    canvas = Canvas.objects.create(title="Cursor trigger")

    cursor = CanvasEventCursor.objects.get(canvas=canvas)

    assert cursor.last_sequence == 0
    assert CanvasEventCursor.objects.filter(canvas=canvas).count() == 1


def test_generation_composite_constraints_reject_cross_canvas_records() -> None:
    canvas = Canvas.objects.create(title="Primary")
    other = Canvas.objects.create(title="Other")
    run = make_run(canvas)
    patch = GraphPatch.objects.create(
        run=run,
        canvas=canvas,
        base_canvas_revision=0,
        operations=[],
    )
    operation = GraphOperation.objects.create(
        canvas=other,
        actor_type="test",
        operation_key="foreign-operation",
        request_fingerprint="foreign-operation",
        operation_type="ADD_NODE",
        payload={},
        result_payload={},
        canvas_revision=1,
    )

    assert_rejected(
        lambda: GenerationEvent.objects.create(
            canvas=other,
            run=run,
            canvas_sequence=1,
            run_sequence=1,
            event_type=GenerationEventType.RUN_STARTED,
            payload={},
        )
    )
    assert_rejected(
        lambda: GraphPatchOperationDecision.objects.create(
            patch=patch,
            canvas=canvas,
            operation_index=0,
            decision="accepted",
            actor_type="test",
            graph_operation=operation,
        )
    )


def test_completed_stage_result_is_database_immutable() -> None:
    canvas = Canvas.objects.create(title="Immutable stage")
    run = make_run(canvas)
    stage = GenerationStage.objects.create(
        run=run,
        name="planning",
        input_hash="one",
        status=StageStatus.COMPLETED,
        attempt=1,
        output={"schema_version": 1},
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        GenerationStage.objects.filter(pk=stage.pk).update(output={"changed": True})


def test_graph_patch_candidate_contract_is_database_immutable() -> None:
    canvas = Canvas.objects.create(title="Immutable patch")
    run = make_run(canvas)
    patch = GraphPatch.objects.create(
        run=run,
        canvas=canvas,
        base_canvas_revision=0,
        operations=[],
        regeneration_target_ids=[],
        permitted_stale_resolution_ids=[],
    )

    immutable_updates = (
        {"base_canvas_revision": 1},
        {"operations": [{"op": "ADD_NODE"}]},
        {"regeneration_target_ids": ["strategy_1"]},
        {"permitted_stale_resolution_ids": ["strategy_1"]},
    )
    for updates in immutable_updates:
        with pytest.raises(DatabaseError), transaction.atomic():
            GraphPatch.objects.filter(pk=patch.pk).update(**updates)

    assert (
        GraphPatch.objects.filter(pk=patch.pk).update(
            status="rejected",
            client_id_map={"candidate_1": str(uuid.uuid4())},
            decided_at=timezone.now(),
        )
        == 1
    )
    patch.refresh_from_db()
    assert patch.status == "rejected"
    assert "candidate_1" in patch.client_id_map


def test_graph_patch_json_contract_shapes_are_database_enforced() -> None:
    canvas = Canvas.objects.create(title="Patch JSON shapes")
    run = make_run(canvas)

    assert_rejected(
        lambda: GraphPatch.objects.create(
            run=run,
            canvas=canvas,
            base_canvas_revision=0,
            operations={},
        )
    )


def test_canvas_deletion_cascades_every_phase_two_record() -> None:
    canvas = Canvas.objects.create(title="Lifecycle")
    run = make_run(canvas)
    stage = GenerationStage.objects.create(
        run=run,
        name="planning",
        input_hash="one",
        status=StageStatus.RUNNING,
        attempt=1,
    )
    event = GenerationEvent.objects.create(
        canvas=canvas,
        run=run,
        canvas_sequence=1,
        run_sequence=1,
        event_type=GenerationEventType.RUN_STARTED,
        payload={},
    )
    patch = GraphPatch.objects.create(
        run=run,
        canvas=canvas,
        base_canvas_revision=0,
        operations=[],
    )
    decision = GraphPatchOperationDecision.objects.create(
        patch=patch,
        canvas=canvas,
        operation_index=0,
        decision="rejected",
        actor_type="test",
    )

    canvas.delete()

    assert not GenerationRun.objects.filter(pk=run.pk).exists()
    assert not GenerationStage.objects.filter(pk=stage.pk).exists()
    assert not GenerationEvent.objects.filter(pk=event.pk).exists()
    assert not GraphPatch.objects.filter(pk=patch.pk).exists()
    assert not GraphPatchOperationDecision.objects.filter(pk=decision.pk).exists()
    assert not CanvasEventCursor.objects.filter(canvas_id=canvas.pk).exists()


def test_queue_indexes_are_partial_and_match_claim_paths() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'generation_run' AND indexname LIKE 'run_%_idx'"
        )
        indexes = dict(cursor.fetchall())

    assert "WHERE (status = 'queued'::text)" in indexes["run_queued_claim_idx"]
    assert "WHERE (status = 'running'::text)" in indexes["run_expired_lease_idx"]


def test_retention_validator_rejects_raw_sources_and_overlong_excerpts() -> None:
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"raw_html": "<html>not retained</html>"})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"sanitized_excerpt": "x" * 501})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"retained_content": "source body"})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"retrieved_page": {"content": "sentinel-source-document"}})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"sources": [{"content": "sentinel-source-document"}]})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload(
            {
                "node": {
                    "kind": "source",
                    "metadata": {"notes": {"text": "sentinel-source-document"}},
                }
            }
        )
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"node": {"kind": "source", "metadata": {"tags": ["x" * 501]}}})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload({"source": {"body": "sentinel-source-document"}})
    with pytest.raises(RetentionPolicyError):
        validate_retained_payload(
            {
                "operations": [
                    {
                        "node": {
                            "kind": "source",
                            "body": "x" * 501,
                        }
                    }
                ]
            }
        )

    validate_retained_payload(
        {"content_hash": "abc", "sanitized_excerpt": "x" * 500, "retained_content": None}
    )
    validate_retained_payload({"operations": [{"node": {"kind": "source", "body": "x" * 500}}]})


def test_run_status_and_lease_coherence_is_database_enforced() -> None:
    canvas = Canvas.objects.create(title="Lease state")
    run = make_run(canvas)

    def invalid_running_transition() -> None:
        GenerationRun.objects.filter(pk=run.pk).update(status=RunStatus.RUNNING)

    assert_rejected(invalid_running_transition)


def test_event_retention_is_rejected_before_cursor_or_payload_persistence() -> None:
    canvas = Canvas.objects.create(title="Event retention")
    run = make_run(canvas)

    with pytest.raises(RetentionPolicyError), transaction.atomic():
        locked = GenerationRun.objects.select_for_update().get(pk=run.pk)
        append_event_locked(
            locked,
            GenerationEventType.RESEARCH_SOURCE_FOUND,
            {"provisional": True, "raw_content": "complete retrieved page"},
        )

    assert GenerationEvent.objects.filter(run=run).count() == 0
    assert CanvasEventCursor.objects.get(canvas=canvas).last_sequence == 0

    with pytest.raises(RetentionPolicyError), transaction.atomic():
        locked = GenerationRun.objects.select_for_update().get(pk=run.pk)
        append_event_locked(
            locked,
            GenerationEventType.EVIDENCE_EXTRACTED,
            {"sanitized_excerpt": "derived but missing provisional flag"},
        )
