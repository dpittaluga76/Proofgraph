from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from proofgraph.generation.execution import process_claimed_run
from proofgraph.generation.models import (
    GenerationRun,
    GenerationStage,
    GraphPatch,
    GraphPatchOperationDecision,
    PatchDecision,
    RunStatus,
    StageStatus,
)
from proofgraph.generation.patch_application import apply_graph_patch
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.queue import RunLease, claim_run, renew_lease
from proofgraph.generation.schemas import GenerationRunRequest, PatchApplyRequest
from proofgraph.generation.services import create_generation_run
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.generation.testing import (
    DeterministicPhase2Executor,
    phase2_test_composition,
)
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, GraphOperation, Node, NodeKind
from proofgraph.runtime.observability import (
    aggregate_observability,
    build_audit_snapshot,
    build_diagnostic_drill,
    telemetry_quality,
)

TEST_COMPOSITION = "proofgraph.generation.testing.phase2_test_composition"


def _request(canvas: Canvas, key: str) -> tuple[GenerationRunRequest, Node]:
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title=f"Goal {key}")
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title=f"Constraint {key}",
        metadata={"context_scope": "global", "pinned": True},
    )
    return (
        GenerationRunRequest(
            operation="generate_strategies",
            selected_node_ids=[goal.id, constraint.id],
            expected_node_versions={goal.id: goal.version, constraint.id: constraint.version},
            execution_profile_id="phase2_test_v1",
            idempotency_key=key,
        ),
        goal,
    )


class ProviderTimeoutExecutor(DeterministicPhase2Executor):
    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs["stage_name"] == "constructing_patch":
            raise ProviderExecutionError(
                "synthetic_provider_timeout",
                "Synthetic provider timed out.",
                retryable=True,
            )
        return super().execute(**kwargs)


class ConflictingPatchExecutor(DeterministicPhase2Executor):
    def __init__(self, goal_id: uuid.UUID) -> None:
        self.goal_id = goal_id

    def _patch_operations(self, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "operation_id": "conflicting-update",
                "op": "UPDATE_NODE",
                "depends_on": [],
                "node_id": str(self.goal_id),
                "expected_version": 99,
                "changes": {"title": "This must not apply"},
            }
        ]


def test_structured_telemetry_adds_identity_and_redacts_secrets(caplog) -> None:
    caplog.set_level(logging.INFO, logger="proofgraph.generation")

    emit_telemetry(
        "provider.test",
        run_id=uuid.uuid4(),
        api_key="server-secret",
        lease_token="lease-secret",
        token_usage={"input_tokens": 3, "total_tokens": 5},
        source_url="https://user:password@example.com/path?token=sensitive&view=full#fragment",
    )

    payload = json.loads(caplog.records[-1].message)
    assert payload["component"] == "generation"
    assert payload["timestamp"].endswith("+00:00")
    assert payload["api_key"] == "[REDACTED]"
    assert payload["lease_token"] == "[REDACTED]"
    assert payload["token_usage"] == {"input_tokens": 3, "total_tokens": 5}
    assert payload["source_url"] == ("https://example.com/path?token=%5BREDACTED%5D&view=full")


def test_metric_views_cover_all_component_owned_lifecycles() -> None:
    records = [
        {"event": "run.queued"},
        {"event": "run.claimed", "reclaimed": True},
        {"event": "queue.depth", "depth": 4},
        {"event": "queue.depth", "depth": 1},
        {"event": "stage.started"},
        {"event": "stage.completed", "stage": "planning", "duration_ms": 12},
        {"event": "stage.reused"},
        {"event": "run.failed", "retryable": True, "code": "provider_timeout"},
        {"event": "run.retry_requested"},
        {"event": "run.poisoned"},
        {"event": "run.cancel_requested"},
        {"event": "run.cancelled"},
        {"event": "run.completed", "duration_ms": 42},
        {"event": "run.heartbeat"},
        {"event": "run.lease_lost"},
        {"event": "run.patch_ready_recovered"},
        {
            "event": "provider.structured_output",
            "provider": "gpt-5.6",
            "latency_ms": 20,
            "model_response_id": "response-1",
            "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
        {
            "event": "provider.failure",
            "provider": "gpt-5.6",
            "latency_ms": 30,
        },
        {"event": "patch.ready"},
        {"event": "patch.applied", "accepted_operation_ratio": 0.75},
        {"event": "patch.rejected"},
        {"event": "patch.apply_conflict"},
        {"event": "patch.regeneration_linked"},
        {
            "event": "extraction.retained",
            "input_count": 8,
            "retained_count": 6,
            "rejected_count": 2,
        },
        {
            "event": "evidence.clustered",
            "cluster_count": 2,
            "independent_source_counts": [1, 2],
        },
        {
            "event": "research_cache.query",
            "outcome": "miss",
            "invalidation_reason": "not_found",
        },
        {"event": "research_cache.source", "outcome": "hit"},
        {"event": "source_ingestion.reservation", "outcome": "reclaimed"},
        {"event": "source_ingestion.fence_lost"},
        {"event": "source_ingestion.completed"},
        {"event": "source_ingestion.failed"},
        *[
            {"event": event}
            for event in (
                "demo.session_created",
                "demo.session_expired",
                "demo.session_cleaned",
                "demo.reset",
                "demo.profile_rejected",
                "demo.session_quota_rejected",
                "demo.circuit_breaker_open",
                "demo.replay_selected",
            )
        ],
    ]

    metrics = aggregate_observability(records)

    assert metrics["queue"]["depth"]["latest"] == 1
    assert metrics["queue"]["reclaims"] == 1
    assert metrics["stages"]["duration_ms_by_stage"]["planning"]["average"] == 12
    assert metrics["failure_retry"]["retryable_failures"] == 1
    assert metrics["failure_retry"]["attempts_exhausted"] == 1
    assert metrics["leases"]["lost"] == 1
    assert metrics["providers"]["by_provider"]["gpt-5.6"] == {
        "calls": 1,
        "failures": 1,
        "latency_ms": {"count": 2, "average": 25.0, "maximum": 30.0},
    }
    assert metrics["patches"]["accepted_operation_ratio"]["average"] == 0.75
    assert metrics["evidence_quality"]["independent_support"]["average"] == 1.5
    assert metrics["cache"]["invalidation_reasons"] == {"not_found": 1}
    assert metrics["source_ingestion"]["reclaims"] == 1
    assert metrics["demo"]["demo.circuit_breaker_open"] == 1


@pytest.mark.django_db(transaction=True)
@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_diagnostic_drill_correlates_real_logs_metrics_events_and_audits(caplog) -> None:
    caplog.set_level(logging.INFO)
    success_canvas = Canvas.objects.create(title="Successful diagnostic")
    success_request, success_goal = _request(success_canvas, "diagnostic-success")
    success_created = create_generation_run(success_canvas.id, success_request)
    success_lease = claim_run("diagnostic-success-worker")
    assert success_lease is not None
    successful = replace(
        phase2_test_composition(),
        executor=ConflictingPatchExecutor(success_goal.id),
    )
    process_claimed_run(success_lease, composition=successful)
    success_run = GenerationRun.objects.get(pk=success_created.payload["run_id"])

    failure_canvas = Canvas.objects.create(title="Provider failure diagnostic")
    failure_request, _failure_goal = _request(failure_canvas, "diagnostic-failure")
    failure_created = create_generation_run(failure_canvas.id, failure_request)
    failure_lease = claim_run("diagnostic-failure-worker")
    assert failure_lease is not None
    failing = replace(
        phase2_test_composition(),
        executor=ProviderTimeoutExecutor(),
    )
    process_claimed_run(failure_lease, composition=failing)
    failure_run = GenerationRun.objects.get(pk=failure_created.payload["run_id"])
    assert failure_run.status == RunStatus.FAILED
    assert failure_run.error["retryable"] is True

    lease_canvas = Canvas.objects.create(title="Lease loss diagnostic")
    lease_request, _lease_goal = _request(lease_canvas, "diagnostic-lease")
    create_generation_run(lease_canvas.id, lease_request)
    lease = claim_run("diagnostic-lease-worker")
    assert lease is not None
    stale_lease = RunLease(
        lease.run_id,
        lease.worker_id,
        uuid.uuid4(),
        lease.lease_epoch,
        lease.canvas_id,
    )
    assert renew_lease(stale_lease) is False

    with pytest.raises(GraphAPIError) as conflict:
        apply_graph_patch(success_run.patch.id, PatchApplyRequest())
    assert conflict.value.code == "patch_apply_conflict"

    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name.startswith("proofgraph.") and record.message.startswith("{")
    ]
    drill = build_diagnostic_drill(records)

    assert drill["passed"] is True
    assert telemetry_quality(records)["passed"] is True
    scenarios = drill["scenarios"]
    assert scenarios["successful_run"]["metrics"]["patches"]["ready"] == 1
    assert "run.failed" in scenarios["retryable_provider_failure"]["persistent_event_types"]
    assert "run.started" in scenarios["lease_loss"]["persistent_event_types"]
    assert scenarios["patch_conflict"]["audit"]["patches"][0]["status"] == "pending"


@pytest.mark.django_db
def test_audit_snapshot_preserves_every_required_reasoning_and_edit_record() -> None:
    canvas = Canvas.objects.create(title="Audit canvas")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Audited goal")
    configuration = phase2_test_composition().profile_resolver.resolve(
        "phase2_test_v1",
        product_request=True,
    )
    run = GenerationRun.objects.create(
        canvas=canvas,
        operation="research_evidence",
        idempotency_key="audit-run",
        request_fingerprint="audit-fingerprint",
        status=RunStatus.COMPLETED,
        base_canvas_revision=0,
        context_snapshot={"nodes": [{"id": str(goal.id), "kind": "goal"}]},
        context_manifest={"request": {"instruction": "Find an auditable opportunity."}},
        context_hash="audit-context",
        selected_node_ids=[str(goal.id)],
        expected_node_versions={str(goal.id): goal.version},
        execution_configuration=configuration.model_dump(mode="json"),
        completed_at=timezone.now(),
    )
    outputs = {
        "planning": {"strategies": [{"id": "strategy-1"}]},
        "researching": {
            "sources": [
                {
                    "id": "source-1",
                    "sanitized_excerpt": "Bounded derived evidence.",
                }
            ]
        },
        "extracting": {"claims": [{"id": "claim-1", "source_ids": ["source-1"]}]},
        "synthesizing": {"opportunities": [{"id": "candidate-1"}]},
        "critiquing": {"critiques": [{"candidate_id": "candidate-1"}]},
        "constructing_patch": {"operations": [{"operation_id": "candidate-op"}]},
    }
    for index, (name, output) in enumerate(outputs.items(), start=1):
        GenerationStage.objects.create(
            run=run,
            name=name,
            input_hash=f"audit-stage-{index}",
            status=StageStatus.COMPLETED,
            attempt=1,
            output=output,
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
    patch = GraphPatch.objects.create(
        run=run,
        canvas=canvas,
        base_canvas_revision=0,
        operations=[
            {"operation_id": "accepted-op"},
            {"operation_id": "rejected-op"},
        ],
    )
    accepted_graph_operation = GraphOperation.objects.create(
        canvas=canvas,
        actor_type="graph_patch",
        operation_key="accepted-patch-operation",
        request_fingerprint="accepted-patch-fingerprint",
        operation_type="ADD_NODE",
        payload={"title": "Accepted candidate"},
        result_payload={"node_id": str(uuid.uuid4())},
        canvas_revision=1,
    )
    GraphPatchOperationDecision.objects.create(
        patch=patch,
        canvas=canvas,
        operation_index=0,
        decision=PatchDecision.ACCEPTED,
        reason="user_accepted",
        actor_type="anonymous_user",
        graph_operation=accepted_graph_operation,
    )
    GraphPatchOperationDecision.objects.create(
        patch=patch,
        canvas=canvas,
        operation_index=1,
        decision=PatchDecision.REJECTED,
        reason="user_rejected",
        actor_type="anonymous_user",
    )
    GraphOperation.objects.create(
        canvas=canvas,
        actor_type="anonymous_user",
        operation_key="direct-user-edit",
        request_fingerprint="direct-user-edit-fingerprint",
        operation_type="UPDATE_NODE",
        payload={"node_id": str(goal.id), "changes": {"title": "Edited goal"}},
        result_payload={"node_id": str(goal.id)},
        canvas_revision=2,
    )

    audit = build_audit_snapshot(
        run_ids=[run.id],
        include_payloads=True,
    )

    assert all(audit["coverage"].values())
    assert audit["runs"][0]["context_manifest"]["request"]["instruction"].startswith("Find")
    assert audit["runs"][0]["stages"][1]["output"]["sources"][0]["id"] == "source-1"
    assert {decision["decision"] for decision in audit["patches"][0]["decisions"]} == {
        "accepted",
        "rejected",
    }
    assert any(
        operation["operation_key"] == "direct-user-edit" for operation in audit["graph_operations"]
    )


def test_observability_report_command_aggregates_jsonl(tmp_path) -> None:
    source = tmp_path / "telemetry.jsonl"
    source.write_text(
        "\n".join(
            (
                json.dumps({"event": "run.queued"}),
                json.dumps({"event": "queue.depth", "depth": 2}),
            )
        ),
        encoding="utf-8",
    )
    output = StringIO()

    call_command("observability_report", input=str(source), stdout=output)

    report = json.loads(output.getvalue())
    assert report["metrics"]["queue"]["queued"] == 1
    assert report["metrics"]["queue"]["depth"]["latest"] == 2
