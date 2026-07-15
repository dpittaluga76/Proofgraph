import json
import logging
from dataclasses import replace

import pytest
from django.db import connection, transaction
from django.test import override_settings

import proofgraph.generation.execution as generation_execution
from proofgraph.generation.execution import _stage_input_hash, process_claimed_run
from proofgraph.generation.models import (
    GenerationEvent,
    GenerationEventType,
    GenerationRun,
    RunStatus,
)
from proofgraph.generation.queue import claim_run
from proofgraph.generation.schemas import GenerationRunRequest, RunExecutionConfiguration
from proofgraph.generation.services import (
    cancel_generation_run,
    create_generation_run,
    retry_generation_run,
)
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.generation.testing import (
    DeterministicPhase2Executor,
    phase2_test_composition,
)
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import (
    Canvas,
    Edge,
    EdgeKind,
    GraphOperation,
    Node,
    NodeKind,
    NodeStalenessCause,
)

pytestmark = pytest.mark.django_db(transaction=True)

TEST_COMPOSITION = "proofgraph.generation.testing.phase2_test_composition"


def make_request(key: str = "execution") -> tuple[Canvas, GenerationRunRequest]:
    canvas = Canvas.objects.create(title="Execution")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Goal")
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Constraint",
        metadata={"context_scope": "global", "pinned": True},
    )
    return canvas, GenerationRunRequest(
        operation="generate_strategies",
        selected_node_ids=[goal.id, constraint.id],
        expected_node_versions={goal.id: 1, constraint.id: 1},
        execution_profile_id="phase2_test_v1",
        idempotency_key=key,
    )


def make_stale_generated(canvas: Canvas, kind: str, title: str) -> Node:
    with transaction.atomic():
        operation = GraphOperation.objects.create(
            canvas=canvas,
            actor_type="test",
            operation_key=f"stale-execution-{title}",
            request_fingerprint=f"stale-execution-{title}",
            operation_type="MARK_STALE",
            payload={},
            result_payload={},
            canvas_revision=1,
        )
        node = Node.objects.create(
            canvas=canvas,
            kind=kind,
            title=title,
            metadata={"generated_by_run_id": "fixture", "review_status": "accepted"},
            stale=True,
            stale_since_revision=1,
        )
        NodeStalenessCause.objects.create(
            canvas=canvas,
            node=node,
            cause_graph_operation=operation,
            origin_entity_type="node",
            origin_entity_id=node.id,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return node


def make_branch_request(
    key: str,
) -> tuple[Canvas, Node, list[Node], GenerationRunRequest]:
    canvas = Canvas.objects.create(title="Branch execution")
    root = make_stale_generated(canvas, NodeKind.STRATEGY, f"Root strategy {key}")
    claims = [
        make_stale_generated(canvas, NodeKind.CLAIM, f"Claim one {key}"),
        make_stale_generated(canvas, NodeKind.CLAIM, f"Claim two {key}"),
    ]
    Edge.objects.bulk_create(
        [
            Edge(canvas=canvas, source=root, target=claim, kind=EdgeKind.DERIVED_FROM)
            for claim in claims
        ]
    )
    request = GenerationRunRequest(
        operation="regenerate_stale",
        selected_node_ids=[root.id],
        expected_node_versions={root.id: root.version},
        execution_profile_id="phase2_test_v1",
        idempotency_key=key,
        regeneration_scope="branch",
    )
    return canvas, root, claims, request


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_deterministic_job_completes_via_immutable_patch() -> None:
    canvas, request = make_request()
    created = create_generation_run(canvas.id, request)
    lease = claim_run("worker")
    assert lease is not None

    process_claimed_run(lease)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])

    assert run.status == RunStatus.COMPLETED
    assert run.worker_id is None and run.lease_token is None
    assert run.stages.filter(status="completed").count() == 2
    assert run.patch.operations[0]["op"] == "ADD_NODE"
    assert run.patch.regeneration_target_ids == []
    assert run.patch.permitted_stale_resolution_ids == []
    assert list(run.events.values_list("canvas_sequence", flat=True)) == sorted(
        run.events.values_list("canvas_sequence", flat=True)
    )
    assert run.events.filter(event_type=GenerationEventType.PATCH_READY).count() == 1
    assert run.events.filter(event_type=GenerationEventType.RUN_COMPLETED).count() == 1


class FailPatchOnceExecutor(DeterministicPhase2Executor):
    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs["stage_name"] == "constructing_patch":
            raise RuntimeError("synthetic interruption")
        return super().execute(**kwargs)


class ObserveProgressBeforeCompletionExecutor(DeterministicPhase2Executor):
    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        result = super().execute(**kwargs)
        if kwargs["stage_name"] == "planning":
            run = GenerationRun.objects.get(pk=kwargs["stage_input"]["run_id"])
            assert run.stages.get(name="planning").status == "running"
            assert run.events.filter(event_type=GenerationEventType.CANDIDATE_GENERATED).exists()
        return result


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_progress_is_committed_while_provider_stage_is_still_running() -> None:
    canvas, request = make_request("progress-before-completion")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("progress-worker")
    assert lease is not None
    composition = replace(
        phase2_test_composition(),
        executor=ObserveProgressBeforeCompletionExecutor(),
    )

    process_claimed_run(lease, composition=composition)

    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert run.status == RunStatus.COMPLETED
    assert run.events.filter(event_type=GenerationEventType.CANDIDATE_GENERATED).count() == 1


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_safe_retry_reuses_completed_checkpoint_and_resumes_failed_stage() -> None:
    canvas, request = make_request("resume")
    created = create_generation_run(canvas.id, request)
    first_lease = claim_run("worker-a")
    assert first_lease is not None
    failing = replace(phase2_test_composition(), executor=FailPatchOnceExecutor())

    process_claimed_run(first_lease, composition=failing)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert run.status == RunStatus.FAILED
    assert run.error["retryable"] is True
    planning = run.stages.get(name="planning")
    assert planning.attempt == 1

    assert retry_generation_run(run.id).status == 202
    second_lease = claim_run("worker-b")
    assert second_lease is not None
    process_claimed_run(second_lease, composition=phase2_test_composition())
    run.refresh_from_db()
    planning.refresh_from_db()

    assert run.status == RunStatus.COMPLETED
    assert planning.attempt == 1
    assert run.stages.get(name="constructing_patch").attempt == 2
    assert run.events.filter(event_type=GenerationEventType.RUN_RETRY_REQUESTED).count() == 1
    assert run.events.filter(event_type=GenerationEventType.RUN_RESUMED).count() == 1
    assert run.events.filter(event_type=GenerationEventType.RUN_FAILED).count() == 1
    assert run.events.filter(event_type=GenerationEventType.RUN_COMPLETED).count() == 1


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_running_cancellation_is_finalized_only_by_fenced_worker() -> None:
    canvas, request = make_request("cancel")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("worker")
    assert lease is not None

    requested = cancel_generation_run(lease.run_id)
    before = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert requested.status == 202
    assert before.status == RunStatus.RUNNING
    assert before.cancel_requested_at is not None

    process_claimed_run(lease)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    duplicate = cancel_generation_run(run.id)

    assert run.status == RunStatus.CANCELLED
    assert duplicate.status == 200
    assert (
        GenerationEvent.objects.filter(
            run=run,
            event_type=GenerationEventType.RUN_CANCELLED,
        ).count()
        == 1
    )


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_cancellation_after_final_checkpoint_wins_before_patch_finalization(monkeypatch) -> None:
    canvas, request = make_request("cancel-finalization")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("worker")
    assert lease is not None
    complete_stage = generation_execution._complete_stage

    def complete_then_cancel(active_lease, stage_id, result):  # type: ignore[no-untyped-def]
        complete_stage(active_lease, stage_id, result)
        if result.stage_name == "constructing_patch":
            assert cancel_generation_run(active_lease.run_id).status == 202

    monkeypatch.setattr(generation_execution, "_complete_stage", complete_then_cancel)

    process_claimed_run(lease)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])

    assert run.status == RunStatus.CANCELLED
    assert not hasattr(run, "patch")
    assert run.events.filter(event_type=GenerationEventType.RUN_CANCELLED).count() == 1
    assert run.events.filter(event_type=GenerationEventType.PATCH_READY).count() == 0
    assert run.events.filter(event_type=GenerationEventType.RUN_COMPLETED).count() == 0


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_branch_regeneration_executes_phase_local_batches_and_one_patch_unit_per_target(
    caplog,
) -> None:
    canvas, root, claims, request = make_branch_request("branch-execution")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("branch-worker")
    assert lease is not None
    calls: list[tuple[str, str | None, list[str]]] = []

    class RecordingExecutor(DeterministicPhase2Executor):
        def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            stage_input = kwargs["stage_input"]
            calls.append(
                (
                    kwargs["stage_name"],
                    stage_input["regeneration_phase"],
                    [target["node_id"] for target in stage_input["target_workset"]],
                )
            )
            return super().execute(**kwargs)

    composition = replace(phase2_test_composition(), executor=RecordingExecutor())
    with caplog.at_level(logging.INFO, logger="proofgraph.generation"):
        process_claimed_run(lease, composition=composition)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    claim_ids = [str(claim.id) for claim in sorted(claims, key=lambda node: str(node.id))]

    assert calls == [
        ("planning", "strategy", [str(root.id)]),
        ("planning", "claim_evidence", claim_ids),
        ("researching", "claim_evidence", claim_ids),
        ("extracting", "claim_evidence", claim_ids),
        ("clustering", "claim_evidence", claim_ids),
        ("constructing_patch", "patch", [str(root.id), *claim_ids]),
    ]
    assert run.status == RunStatus.COMPLETED
    assert run.stages.filter(name="planning", status="completed").count() == 2
    assert len(run.patch.operations) == 3
    assert [operation["node"]["kind"] for operation in run.patch.operations] == [
        NodeKind.STRATEGY,
        NodeKind.CLAIM,
        NodeKind.CLAIM,
    ]
    events = [json.loads(record.message) for record in caplog.records]
    started = next(event for event in events if event["event"] == "regeneration.started")
    assert started["regeneration_scope"] == "branch"
    assert started["regeneration_workset_size"] == 3
    assert started["regeneration_batch_sizes"] == {"claim_evidence": 2, "strategy": 1}
    assert started["lineage_mode"] == "parallel"


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_branch_regeneration_retry_reuses_phase_checkpoints(caplog) -> None:
    canvas, _root, _claims, request = make_branch_request("branch-resume")
    created = create_generation_run(canvas.id, request)
    first_lease = claim_run("branch-worker-a")
    assert first_lease is not None

    class FailExtractionExecutor(DeterministicPhase2Executor):
        def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs["stage_name"] == "extracting":
                raise RuntimeError("synthetic branch interruption")
            return super().execute(**kwargs)

    failing = replace(phase2_test_composition(), executor=FailExtractionExecutor())
    process_claimed_run(first_lease, composition=failing)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])

    assert run.status == RunStatus.FAILED
    assert run.stages.filter(name="planning", status="completed").count() == 2
    assert run.stages.get(name="researching").attempt == 1
    assert run.stages.get(name="extracting").attempt == 1

    assert retry_generation_run(run.id).status == 202
    second_lease = claim_run("branch-worker-b")
    assert second_lease is not None
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="proofgraph.generation"):
        process_claimed_run(second_lease)
    run.refresh_from_db()

    assert run.status == RunStatus.COMPLETED
    assert all(stage.attempt == 1 for stage in run.stages.filter(name="planning"))
    assert run.stages.get(name="researching").attempt == 1
    assert run.stages.get(name="extracting").attempt == 2
    assert run.stages.get(name="clustering").attempt == 1
    assert len(run.patch.operations) == 3
    assert run.events.filter(event_type=GenerationEventType.RUN_RESUMED).count() == 1
    reuse_events = [
        json.loads(record.message)
        for record in caplog.records
        if json.loads(record.message).get("event") == "stage.reused"
    ]
    assert {(event["regeneration_phase"], event["stage"]) for event in reuse_events} == {
        ("strategy", "planning"),
        ("claim_evidence", "planning"),
        ("claim_evidence", "researching"),
    }
    assert all(event["checkpoint_reused"] is True for event in reuse_events)
    assert all(event["lineage_mode"] == "parallel" for event in reuse_events)


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_branch_cancellation_stops_the_active_batch_without_a_partial_patch() -> None:
    canvas, _root, _claims, request = make_branch_request("branch-cancel")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("branch-cancel-worker")
    assert lease is not None

    class CancelDuringClaimPlanningExecutor(DeterministicPhase2Executor):
        def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            result = super().execute(**kwargs)
            if (
                kwargs["stage_name"] == "planning"
                and kwargs["stage_input"]["regeneration_phase"] == "claim_evidence"
            ):
                assert cancel_generation_run(lease.run_id).status == 202
            return result

    composition = replace(
        phase2_test_composition(),
        executor=CancelDuringClaimPlanningExecutor(),
    )
    process_claimed_run(lease, composition=composition)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])

    assert run.status == RunStatus.CANCELLED
    assert not hasattr(run, "patch")
    assert run.stages.filter(name="planning", status="completed").count() == 1
    cancelled_stage = run.stages.get(name="planning", status="failed")
    assert cancelled_stage.error["code"] == "run_cancelled"
    assert run.stages.exclude(name="planning").count() == 0
    assert run.events.filter(event_type=GenerationEventType.RUN_CANCELLED).count() == 1


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_stage_executor_runs_outside_database_transactions() -> None:
    class TransactionAssertingExecutor(DeterministicPhase2Executor):
        def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            assert connection.in_atomic_block is False
            return super().execute(**kwargs)

    canvas, request = make_request("outside-transaction")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("worker")
    assert lease is not None
    composition = replace(
        phase2_test_composition(),
        executor=TransactionAssertingExecutor(),
    )

    process_claimed_run(lease, composition=composition)

    assert GenerationRun.objects.get(pk=created.payload["run_id"]).status == RunStatus.COMPLETED


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_provider_telemetry_inherits_run_stage_and_lease_correlation(caplog) -> None:
    class TelemetryExecutor(DeterministicPhase2Executor):
        def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            emit_telemetry("provider.inner", provider="synthetic")
            return super().execute(**kwargs)

    canvas, request = make_request("telemetry-correlation")
    created = create_generation_run(canvas.id, request)
    lease = claim_run("telemetry-worker")
    assert lease is not None
    composition = replace(
        phase2_test_composition(),
        executor=TelemetryExecutor(),
    )
    caplog.set_level(logging.INFO, logger="proofgraph.generation")

    process_claimed_run(lease, composition=composition)

    events = [json.loads(record.message) for record in caplog.records]
    provider_event = next(event for event in events if event["event"] == "provider.inner")
    assert provider_event == {
        "event": "provider.inner",
        "provider": "synthetic",
        "run_id": str(created.payload["run_id"]),
        "canvas_id": str(canvas.id),
        "stage": "planning",
        "worker_id": "telemetry-worker",
        "lease_epoch": 1,
        "attempt": 1,
        "execution_profile_id": "phase2_test_v1",
    }


def test_checkpoint_hash_covers_semantics_stage_provider_profile_and_fixture() -> None:
    base = RunExecutionConfiguration(
        profile_id="profile-a",
        provider_identity="provider-a",
        pipeline_version="pipeline-v1",
        prompt_version="prompt-v1",
        strategy_version="strategy-v1",
        fixture_bundle_id="fixtures",
        fixture_version="1",
    )
    semantic_input = {"context": {"node": "one"}}
    expected = _stage_input_hash("planning", semantic_input, base)

    assert _stage_input_hash("planning", {"context": {"node": "two"}}, base) != expected
    assert _stage_input_hash("researching", semantic_input, base) != expected
    for changed in (
        {"provider_identity": "provider-b"},
        {"profile_id": "profile-b"},
        {"fixture_version": "2"},
    ):
        assert (
            _stage_input_hash("planning", semantic_input, base.model_copy(update=changed))
            != expected
        )


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_research_plan_persists_only_provisional_sanitized_progress() -> None:
    canvas = Canvas.objects.create(title="Research execution")
    strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Applied strategy",
    )
    request = GenerationRunRequest(
        operation="research_evidence",
        selected_node_ids=[strategy.id],
        expected_node_versions={strategy.id: 1},
        execution_profile_id="phase2_test_v1",
        idempotency_key="research-progress",
    )
    created = create_generation_run(canvas.id, request)
    lease = claim_run("research-worker")
    assert lease is not None

    process_claimed_run(lease)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    source_event = run.events.get(event_type=GenerationEventType.RESEARCH_SOURCE_FOUND)
    evidence_event = run.events.get(event_type=GenerationEventType.EVIDENCE_EXTRACTED)

    assert run.status == RunStatus.COMPLETED
    assert run.stages.filter(status="completed").count() == 5
    assert source_event.payload["provisional"] is True
    assert evidence_event.payload["provisional"] is True
    assert len(source_event.payload["sanitized_excerpt"]) <= 500
    assert "raw_content" not in source_event.payload


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_last_failed_attempt_becomes_non_retryable_exhaustion() -> None:
    canvas, request = make_request("exhausted-stage")
    created = create_generation_run(canvas.id, request)
    GenerationRun.objects.filter(pk=created.payload["run_id"]).update(max_attempts=1)
    lease = claim_run("last-worker")
    assert lease is not None
    failing = replace(phase2_test_composition(), executor=FailPatchOnceExecutor())

    process_claimed_run(lease, composition=failing)
    run = GenerationRun.objects.get(pk=created.payload["run_id"])

    assert run.status == RunStatus.FAILED
    assert run.error["code"] == "attempts_exhausted"
    assert run.error["retryable"] is False
    with pytest.raises(GraphAPIError) as rejected:
        retry_generation_run(run.id)
    assert rejected.value.status == 409
