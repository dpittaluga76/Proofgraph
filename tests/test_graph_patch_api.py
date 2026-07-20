import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, connections, transaction
from django.test import Client, override_settings

from proofgraph.generation.models import (
    GenerationRun,
    GraphPatch,
    GraphPatchOperationDecision,
    PatchDecision,
    PatchStatus,
    RunStatus,
)
from proofgraph.generation.schemas import GenerationRunRequest
from proofgraph.generation.services import create_generation_run
from proofgraph.graph.models import Canvas, Edge, GraphOperation, Node, NodeKind
from proofgraph.graph.operations import apply_graph_operation

pytestmark = pytest.mark.django_db(transaction=True)

TEST_COMPOSITION = "proofgraph.generation.testing.phase2_test_composition"


def _opportunity_metadata() -> dict[str, object]:
    return {
        "provenance_node_ids": [],
        "assumptions": [
            {
                "id": "assumption-volume",
                "statement": "Questionnaire volume recurs.",
                "importance": "high",
            }
        ],
        "risks": [
            {
                "id": "risk-trust",
                "statement": "Reviewers may distrust stale answers.",
                "impact": "high",
                "mitigation": "Require approval and provenance.",
            }
        ],
        "contradiction": "Some buyers report low recurrence.",
        "distribution_rationale": "Security operators gather in focused communities.",
        "defensibility": "Approved-answer history compounds with use.",
        "dimensions": {
            "evidence_strength": {"rating": "medium", "rationale": "Two sources."},
            "novelty": {"rating": "medium", "rationale": "Workflow wedge."},
            "builder_fit": {"rating": "high", "rationale": "Bounded MVP."},
            "technical_feasibility": {"rating": "high", "rationale": "Known stack."},
            "distribution_clarity": {"rating": "medium", "rationale": "Known buyer."},
            "operational_burden": {"rating": "medium", "rationale": "Review remains."},
        },
    }


def _candidate_operations() -> list[dict[str, object]]:
    return [
        {
            "operation_id": "add-opportunity",
            "op": "ADD_NODE",
            "depends_on": [],
            "client_generated_id": "candidate-opportunity",
            "node": {
                "kind": "opportunity",
                "title": "Questionnaire response workspace",
                "body": "Reuse approved answers with provenance.",
                "metadata": _opportunity_metadata(),
            },
        },
        {
            "operation_id": "add-risk",
            "op": "ADD_NODE",
            "depends_on": ["add-opportunity"],
            "client_generated_id": "candidate-risk",
            "node": {
                "kind": "risk",
                "title": "Trust and freshness burden",
                "body": "Answers require review.",
                "metadata": {"provenance_node_ids": ["candidate-opportunity"]},
            },
        },
        {
            "operation_id": "add-contradiction",
            "op": "ADD_EDGE",
            "depends_on": ["add-opportunity", "add-risk"],
            "client_generated_id": "candidate-contradiction",
            "edge": {
                "source_node_id": "candidate-risk",
                "target_node_id": "candidate-opportunity",
                "kind": "contradicts",
                "metadata": {},
            },
        },
    ]


def _make_completed_run() -> tuple[GenerationRun, Node, Node]:
    canvas = Canvas.objects.create(title="Patch review")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Find an opportunity")
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Six week MVP",
        metadata={"context_scope": "global", "pinned": True},
    )
    request = GenerationRunRequest(
        operation="generate_strategies",
        selected_node_ids=[goal.id, constraint.id],
        expected_node_versions={goal.id: goal.version, constraint.id: constraint.version},
        instruction="Generate strategies.",
        execution_profile_id="phase2_test_v1",
        idempotency_key="original-run",
    )
    result = create_generation_run(canvas.id, request)
    run = GenerationRun.objects.get(pk=result.payload["run_id"])
    run.status = RunStatus.COMPLETED
    run.save(update_fields=["status"])
    return run, goal, constraint


def _make_pending_patch(
    operations: list[dict[str, object]] | None = None,
) -> tuple[GraphPatch, Node, Node]:
    run, goal, constraint = _make_completed_run()
    patch = _create_patch(run, operations or _candidate_operations())
    return patch, goal, constraint


def _create_patch(
    run: GenerationRun,
    operations: list[dict[str, object]],
) -> GraphPatch:
    patch = GraphPatch.objects.create(
        run=run,
        canvas=run.canvas,
        base_canvas_revision=run.canvas.revision,
        operations=operations,
    )
    return patch


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_patch_detail_exposes_immutable_candidates_review_facets_and_dependencies() -> None:
    patch, _goal, _constraint = _make_pending_patch()

    response = Client().get(f"/api/graph-patches/{patch.id}")

    assert response.status_code == 200
    payload = response.json()["patch"]
    assert payload["status"] == "pending"
    assert payload["operations"][0]["candidate"] == _candidate_operations()[0]
    assert payload["operations"][0]["review"] == {
        "change_type": "addition",
        "entity_type": "node",
        "semantic_role": "opportunity",
        "title": "Questionnaire response workspace",
        "provenance_node_ids": [],
        "assumptions": _opportunity_metadata()["assumptions"],
        "risks": _opportunity_metadata()["risks"],
        "contradiction": "Some buyers report low recurrence.",
        "quality_dimensions": _opportunity_metadata()["dimensions"],
        "distribution_rationale": "Security operators gather in focused communities.",
        "defensibility_rationale": "Approved-answer history compounds with use.",
    }
    assert payload["operations"][1]["dependency_operation_ids"] == ["add-opportunity"]
    assert payload["operations"][1]["dependency_operation_indices"] == [0]
    assert payload["operations"][2]["review"]["semantic_role"] == "contradicts"
    assert payload["decisions"] == []


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_reject_records_every_operation_without_mutating_graph_and_is_idempotent() -> None:
    patch, _goal, _constraint = _make_pending_patch()
    canvas = patch.canvas
    node_count = canvas.nodes.count()
    revision = canvas.revision
    client = Client()

    first = client.post(
        f"/api/graph-patches/{patch.id}/reject",
        data=json.dumps({}),
        content_type="application/json",
    )
    replay = client.post(
        f"/api/graph-patches/{patch.id}/reject",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert first.status_code == replay.status_code == 200, (first.json(), replay.json())
    assert replay.json() == first.json()
    patch.refresh_from_db()
    canvas.refresh_from_db()
    assert patch.status == PatchStatus.REJECTED
    assert patch.decided_at is not None
    assert canvas.revision == revision
    assert canvas.nodes.count() == node_count
    assert GraphPatchOperationDecision.objects.filter(patch=patch).count() == 3
    assert set(patch.decisions.values_list("decision", flat=True)) == {"rejected"}
    assert set(patch.decisions.values_list("reason", flat=True)) == {"user_rejected"}


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_reject_rejects_fields_and_does_not_override_applied_patch() -> None:
    patch, _goal, _constraint = _make_pending_patch()
    client = Client()
    invalid = client.post(
        f"/api/graph-patches/{patch.id}/reject",
        data=json.dumps({"reason": "not accepted"}),
        content_type="application/json",
    )
    patch.status = PatchStatus.APPLIED
    patch.save(update_fields=["status"])
    conflict = client.post(
        f"/api/graph-patches/{patch.id}/reject",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_patch_rejection_request"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "patch_not_pending"
    assert not patch.decisions.exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_regenerate_revalidates_current_state_rejects_original_and_links_one_run(
    caplog,
) -> None:
    patch, goal, _constraint = _make_pending_patch()
    Node.objects.filter(pk=goal.id).update(title="Updated goal", version=2)
    client = Client()
    body = {
        "instruction": "Make the strategies more operationally conservative.",
        "idempotency_key": "revise-patch-once",
    }

    with caplog.at_level(logging.INFO, logger="proofgraph.generation"):
        first = client.post(
            f"/api/graph-patches/{patch.id}/regenerate",
            data=json.dumps(body),
            content_type="application/json",
        )
    replay = client.post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    patch.refresh_from_db()
    linked = GenerationRun.objects.get(pk=first.json()["regeneration_run"]["run_id"])
    assert patch.status == PatchStatus.REJECTED
    assert patch.regenerated_by_run == linked
    assert linked.status == RunStatus.QUEUED
    assert linked.operation == patch.run.operation
    assert linked.expected_node_versions[str(goal.id)] == 2
    assert linked.context_manifest["request"]["instruction"] == body["instruction"]
    assert linked.execution_configuration["profile_id"] == "phase2_test_v1"
    assert set(patch.decisions.values_list("reason", flat=True)) == {"regeneration_requested"}
    detail = client.get(f"/api/graph-patches/{patch.id}")
    assert detail.json()["patch"]["regenerated_by_run_id"] == str(linked.id)
    events = [json.loads(record.message) for record in caplog.records]
    requested = next(event for event in events if event["event"] == "patch.regeneration_requested")
    assert (
        requested.items()
        >= {
            "component": "generation",
            "event": "patch.regeneration_requested",
            "patch_id": str(patch.id),
            "original_run_id": str(patch.run_id),
            "canvas_id": str(patch.canvas_id),
        }.items()
    )
    assert requested["timestamp"].endswith("+00:00")


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_regeneration_conflicts_do_not_change_pending_patch() -> None:
    patch, _goal, constraint = _make_pending_patch()
    constraint.delete()
    response = Client().post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps(
            {
                "instruction": "Revise after deletion.",
                "idempotency_key": "invalid-current-selection",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "selected_entity_missing"
    patch.refresh_from_db()
    assert patch.status == PatchStatus.PENDING
    assert patch.regenerated_by_run_id is None
    assert not patch.decisions.exists()
    assert not GenerationRun.objects.filter(idempotency_key="invalid-current-selection").exists()


def test_unavailable_regeneration_profile_leaves_patch_pending() -> None:
    with override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION):
        patch, _goal, _constraint = _make_pending_patch()

    response = Client().post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps(
            {
                "instruction": "Revise with the original profile.",
                "idempotency_key": "profile-no-longer-product-selectable",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "execution_profile_unavailable"
    patch.refresh_from_db()
    assert patch.status == PatchStatus.PENDING
    assert patch.regenerated_by_run_id is None
    assert not patch.decisions.exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_regeneration_rejects_conflicting_retries_and_key_reuse() -> None:
    patch, goal, constraint = _make_pending_patch()
    client = Client()
    body = {"instruction": "First revision.", "idempotency_key": "revision-key"}
    first = client.post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps(body),
        content_type="application/json",
    )
    changed = client.post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps({**body, "instruction": "Different revision."}),
        content_type="application/json",
    )
    another_key = client.post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps({**body, "idempotency_key": "another-key"}),
        content_type="application/json",
    )

    assert first.status_code == 202
    assert changed.status_code == another_key.status_code == 409
    assert GenerationRun.objects.filter(canvas=patch.canvas).count() == 2

    second_patch = GraphPatch.objects.create(
        run=GenerationRun.objects.create(
            canvas=patch.canvas,
            operation="generate_strategies",
            idempotency_key="second-original",
            request_fingerprint="second-original",
            status=RunStatus.COMPLETED,
            base_canvas_revision=patch.canvas.revision,
            context_snapshot={},
            context_manifest={
                "request": {
                    "operation": "generate_strategies",
                    "instruction": None,
                    "regeneration_scope": None,
                }
            },
            context_hash="second-original",
            selected_node_ids=sorted([str(goal.id), str(constraint.id)]),
            expected_node_versions={
                str(goal.id): goal.version,
                str(constraint.id): constraint.version,
            },
            execution_configuration=patch.run.execution_configuration,
        ),
        canvas=patch.canvas,
        base_canvas_revision=patch.canvas.revision,
        operations=_candidate_operations(),
    )
    key_reuse = client.post(
        f"/api/graph-patches/{second_patch.id}/regenerate",
        data=json.dumps(body),
        content_type="application/json",
    )
    assert key_reuse.status_code == 409
    second_patch.refresh_from_db()
    assert second_patch.status == PatchStatus.PENDING
    assert not second_patch.decisions.exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_linked_run_terminal_state_emits_patch_regeneration_telemetry(caplog) -> None:
    patch, _goal, _constraint = _make_pending_patch()
    created = Client().post(
        f"/api/graph-patches/{patch.id}/regenerate",
        data=json.dumps(
            {
                "instruction": "Revise and then cancel.",
                "idempotency_key": "terminal-telemetry",
            }
        ),
        content_type="application/json",
    )
    linked_run_id = created.json()["regeneration_run"]["run_id"]
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="proofgraph.generation"):
        cancelled = Client().post(f"/api/generation-runs/{linked_run_id}/cancel")

    events = [json.loads(record.message) for record in caplog.records]
    terminal = next(event for event in events if event["event"] == "patch.regeneration_terminal")
    assert cancelled.status_code == 200
    assert (
        terminal.items()
        >= {
            "component": "generation",
            "event": "patch.regeneration_terminal",
            "patch_id": str(patch.id),
            "original_run_id": str(patch.run_id),
            "run_id": linked_run_id,
            "canvas_id": str(patch.canvas_id),
            "status": "cancelled",
        }.items()
    )
    assert terminal["timestamp"].endswith("+00:00")


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_apply_all_materializes_dependencies_with_deterministic_ids_and_is_idempotent() -> None:
    patch, goal, _constraint = _make_pending_patch()
    canvas = patch.canvas
    apply_graph_operation(
        canvas.id,
        {
            "op": "MOVE_NODE",
            "operation_key": str(uuid.uuid4()),
            "node_id": str(goal.id),
            "expected_position_version": goal.position_version,
            "position": {"x": 40, "y": 60},
        },
    )
    client = Client()

    first = client.post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps({}),
        content_type="application/json",
    )
    replay = client.post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert first.status_code == replay.status_code == 200, (first.json(), replay.json())
    assert replay.json() == first.json()
    patch.refresh_from_db()
    canvas.refresh_from_db()
    assert patch.status == PatchStatus.APPLIED
    assert canvas.revision == 4
    assert canvas.nodes.count() == 4
    assert canvas.edges.count() == 1
    assert GraphOperation.objects.filter(canvas=canvas, actor_type="graph_patch").count() == 3
    assert set(patch.decisions.values_list("decision", flat=True)) == {"accepted"}
    expected_map = {
        local_id: str(uuid.uuid5(patch.id, local_id))
        for local_id in (
            "candidate-contradiction",
            "candidate-opportunity",
            "candidate-risk",
        )
    }
    assert patch.client_id_map == expected_map
    opportunity = Node.objects.get(pk=expected_map["candidate-opportunity"])
    risk = Node.objects.get(pk=expected_map["candidate-risk"])
    edge = Edge.objects.get(pk=expected_map["candidate-contradiction"])
    assert opportunity.metadata["review_status"] == "accepted"
    assert opportunity.metadata["source_patch_id"] == str(patch.id)
    assert risk.metadata["provenance_node_ids"] == [str(opportunity.id)]
    assert edge.source_id == risk.id
    assert edge.target_id == opportunity.id
    assert patch.base_canvas_revision == 0
    for operation in _candidate_operations():
        expected_key = str(uuid.uuid5(patch.id, str(operation["operation_id"])))
        assert GraphOperation.objects.filter(
            canvas=canvas,
            actor_type="graph_patch",
            operation_key=expected_key,
        ).exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_apply_selected_requires_dependency_closure_and_rejects_unselected_operations() -> None:
    incomplete_patch, _goal, _constraint = _make_pending_patch()
    client = Client()
    incomplete = client.post(
        f"/api/graph-patches/{incomplete_patch.id}/apply",
        data=json.dumps({"selected_operation_ids": ["add-risk"]}),
        content_type="application/json",
    )

    assert incomplete.status_code == 409
    assert incomplete.json()["error"]["code"] == "patch_dependency_incomplete"
    incomplete_patch.refresh_from_db()
    assert incomplete_patch.status == PatchStatus.PENDING
    assert not incomplete_patch.decisions.exists()

    patch, _goal, _constraint = _make_pending_patch()
    selected = client.post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps({"selected_operation_ids": ["add-opportunity"]}),
        content_type="application/json",
    )
    assert selected.status_code == 200
    patch.refresh_from_db()
    assert patch.status == PatchStatus.PARTIALLY_APPLIED
    assert patch.decisions.get(operation_index=0).decision == PatchDecision.ACCEPTED
    assert set(
        patch.decisions.filter(operation_index__in=[1, 2]).values_list("reason", flat=True)
    ) == {"user_not_selected"}
    assert set(patch.client_id_map) == {"candidate-opportunity"}
    conflicting_retry = client.post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert conflicting_retry.status_code == 409
    assert conflicting_retry.json()["error"]["code"] == "patch_apply_request_conflict"


def _conflicting_update_patch() -> tuple[GraphPatch, Node, Node]:
    run, goal, constraint = _make_completed_run()
    operations: list[dict[str, object]] = [
        {
            "operation_id": "update-goal",
            "op": "UPDATE_NODE",
            "depends_on": [],
            "node_id": str(goal.id),
            "expected_version": goal.version,
            "changes": {"title": "Applied goal title"},
        },
        {
            "operation_id": "update-constraint",
            "op": "UPDATE_NODE",
            "depends_on": [],
            "node_id": str(constraint.id),
            "expected_version": 99,
            "changes": {"title": "Conflicting constraint title"},
        },
        {
            "operation_id": "dependent-risk",
            "op": "ADD_NODE",
            "depends_on": ["update-constraint"],
            "client_generated_id": "dependent-risk-node",
            "node": {
                "kind": "risk",
                "title": "Dependent risk",
                "body": "Depends on the conflicting update.",
                "metadata": {},
            },
        },
    ]
    return _create_patch(run, operations), goal, constraint


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_strict_conflict_rolls_back_every_write() -> None:
    patch, goal, constraint = _conflicting_update_patch()
    canvas = patch.canvas
    response = Client().post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "patch_apply_conflict"
    conflict = response.json()["error"]["details"]["conflicts"][0]
    assert conflict["operation_id"] == "update-constraint"
    assert conflict["code"] == "version_conflict"
    patch.refresh_from_db()
    goal.refresh_from_db()
    constraint.refresh_from_db()
    canvas.refresh_from_db()
    assert patch.status == PatchStatus.PENDING
    assert goal.title == "Find an opportunity"
    assert constraint.title == "Six week MVP"
    assert canvas.revision == 0
    assert not patch.decisions.exists()
    assert not GraphOperation.objects.filter(canvas=canvas, actor_type="graph_patch").exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_nonconflicting_mode_applies_independent_work_and_skips_dependents() -> None:
    patch, goal, constraint = _conflicting_update_patch()
    client = Client()
    body = {"apply_nonconflicting_only": True}

    response = client.post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps(body),
        content_type="application/json",
    )
    replay = client.post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert response.status_code == replay.status_code == 200
    assert replay.json() == response.json()
    assert [item["operation_id"] for item in response.json()["conflicts"]] == [
        "update-constraint",
        "dependent-risk",
    ]
    patch.refresh_from_db()
    goal.refresh_from_db()
    constraint.refresh_from_db()
    assert patch.status == PatchStatus.PARTIALLY_APPLIED
    assert goal.title == "Applied goal title"
    assert constraint.title == "Six week MVP"
    assert patch.decisions.get(operation_index=0).decision == PatchDecision.ACCEPTED
    assert set(
        patch.decisions.filter(operation_index__in=[1, 2]).values_list("decision", flat=True)
    ) == {PatchDecision.SKIPPED_CONFLICT}
    assert not Node.objects.filter(title="Dependent risk").exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_delete_node_requires_and_applies_edge_and_branch_constraint_prerequisites() -> None:
    run, goal, original_constraint = _make_completed_run()
    canvas = run.canvas
    strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Old strategy",
        metadata={"generated_by_run_id": str(run.id), "review_status": "accepted"},
    )
    edge = Edge.objects.create(
        canvas=canvas,
        source=goal,
        target=strategy,
        kind="derived_from",
    )
    branch_constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Branch constraint",
        metadata={"context_scope": "branch", "pinned": True},
        branch_root=strategy,
    )
    operations: list[dict[str, object]] = [
        {
            "operation_id": "delete-edge",
            "op": "DELETE_EDGE",
            "depends_on": [],
            "edge_id": str(edge.id),
            "expected_version": edge.version,
        },
        {
            "operation_id": "rescope-constraint",
            "op": "UPDATE_NODE",
            "depends_on": [],
            "node_id": str(branch_constraint.id),
            "expected_version": branch_constraint.version,
            "changes": {
                "metadata": {"context_scope": "global"},
                "branch_root_node_id": None,
            },
        },
        {
            "operation_id": "delete-strategy",
            "op": "DELETE_NODE",
            "depends_on": ["delete-edge", "rescope-constraint"],
            "node_id": str(strategy.id),
            "expected_version": strategy.version,
            "required_incident_edge_ids": [str(edge.id)],
            "required_branch_constraint_ids": [str(branch_constraint.id)],
        },
    ]
    patch = _create_patch(run, operations)

    response = Client().post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert not Node.objects.filter(pk=strategy.id).exists()
    assert not Edge.objects.filter(pk=edge.id).exists()
    branch_constraint.refresh_from_db()
    assert branch_constraint.branch_root_id is None
    assert branch_constraint.metadata["context_scope"] == "global"
    assert Node.objects.filter(pk=original_constraint.id).exists()


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_concurrent_new_dependency_conflicts_and_prevents_node_deletion() -> None:
    run, goal, constraint = _make_completed_run()
    patch = _create_patch(
        run,
        [
            {
                "operation_id": "delete-goal",
                "op": "DELETE_NODE",
                "depends_on": [],
                "node_id": str(goal.id),
                "expected_version": goal.version,
            }
        ],
    )
    dependency_committed = Event()

    def add_dependency() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                canvas = Canvas.objects.select_for_update().get(pk=run.canvas_id)
                locked_goal = Node.objects.select_for_update().get(pk=goal.id)
                locked_constraint = Node.objects.select_for_update().get(pk=constraint.id)
                Edge.objects.create(
                    canvas=canvas,
                    source=locked_goal,
                    target=locked_constraint,
                    kind="constrained_by",
                )
            dependency_committed.set()
        finally:
            connections.close_all()

    def apply_patch() -> tuple[int, dict[str, object]]:
        close_old_connections()
        try:
            dependency_committed.wait(timeout=5)
            response = Client().post(
                f"/api/graph-patches/{patch.id}/apply",
                data=json.dumps({}),
                content_type="application/json",
            )
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        dependency_future = executor.submit(add_dependency)
        apply_future = executor.submit(apply_patch)
        dependency_future.result(timeout=5)
        status, payload = apply_future.result(timeout=5)

    assert status == 409
    conflict = payload["error"]["details"]["conflicts"][0]
    assert conflict["code"] == "node_has_dependencies"
    assert conflict["details"]["incident_edges"]
    patch.refresh_from_db()
    assert patch.status == PatchStatus.PENDING
    assert Node.objects.filter(pk=goal.id).exists()
    assert not patch.decisions.exists()
