import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, transaction
from django.db.models import F
from django.test import Client, override_settings

from proofgraph.generation.context import GraphRunContextFactory
from proofgraph.generation.execution import process_claimed_run
from proofgraph.generation.models import GenerationRun
from proofgraph.generation.queue import claim_run
from proofgraph.generation.schemas import GenerationRunRequest
from proofgraph.generation.services import create_generation_run
from proofgraph.generation.testing import phase2_test_composition
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


def make_strategy_inputs() -> tuple[Canvas, Node, Node]:
    canvas = Canvas.objects.create(title="Generation API")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Goal")
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Constraint",
        metadata={"context_scope": "global", "pinned": True},
    )
    return canvas, goal, constraint


def request_body(goal: Node, constraint: Node, *, key: str = "run-key") -> dict[str, object]:
    return {
        "operation": "generate_strategies",
        "selected_node_ids": [str(goal.id), str(constraint.id)],
        "expected_node_versions": {
            str(goal.id): goal.version,
            str(constraint.id): constraint.version,
        },
        "instruction": "Generate distinct strategies.",
        "execution_profile_id": "phase2_test_v1",
        "idempotency_key": key,
    }


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_run_creation_is_idempotent_and_freezes_semantic_context() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    client = Client()
    body = request_body(goal, constraint)

    first = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )
    replay = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    run = GenerationRun.objects.get(pk=first.json()["run_id"])
    assert run.selected_node_ids == sorted([str(goal.id), str(constraint.id)])
    assert run.expected_node_versions == {str(goal.id): 1, str(constraint.id): 1}
    assert run.base_canvas_revision == canvas.revision
    assert run.context_manifest["request"] == {
        "operation": "generate_strategies",
        "instruction": "Generate distinct strategies.",
        "regeneration_scope": None,
    }
    assert all("position" not in node for node in run.context_snapshot["nodes"])
    assert first.json()["events_url"].endswith("?after=0")


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_delayed_idempotent_replay_preserves_original_sse_baseline() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    client = Client()
    body = request_body(goal, constraint, key="delayed-replay")
    first = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )
    lease = claim_run("idempotency-worker")
    assert lease is not None
    process_claimed_run(lease)

    replay = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["status"] == "completed"
    assert replay.json()["events_url"] == first.json()["events_url"]
    assert replay.json()["events_url"].endswith("?after=0")


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_conflicting_idempotency_reuse_returns_409() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    client = Client()
    body = request_body(goal, constraint)
    first = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )
    body["instruction"] = "A different request."

    conflict = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_exact_versions_and_operation_cardinality_are_enforced() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    client = Client()
    wrong_version = request_body(goal, constraint, key="wrong-version")
    wrong_version["expected_node_versions"][str(goal.id)] = 99

    mismatch = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(wrong_version),
        content_type="application/json",
    )
    invalid_cardinality = request_body(goal, constraint, key="bad-cardinality")
    invalid_cardinality["selected_node_ids"] = [str(goal.id)]
    invalid_cardinality["expected_node_versions"] = {str(goal.id): 1}
    invalid = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(invalid_cardinality),
        content_type="application/json",
    )

    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "invalid_generation_selection"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_generation_selection"


def test_phase_two_test_profile_is_unreachable_from_production_composition() -> None:
    canvas, goal, constraint = make_strategy_inputs()

    response = Client().post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(request_body(goal, constraint)),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "execution_profile_unavailable"
    assert GenerationRun.objects.count() == 0


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_run_status_and_queued_cancellation_contract() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    client = Client()
    created = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(request_body(goal, constraint)),
        content_type="application/json",
    ).json()

    status = client.get(f"/api/generation-runs/{created['run_id']}")
    cancelled = client.post(f"/api/generation-runs/{created['run_id']}/cancel")
    duplicate = client.post(f"/api/generation-runs/{created['run_id']}/cancel")
    retry = client.post(f"/api/generation-runs/{created['run_id']}/retry")

    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert status.json()["ready_patch_id"] is None
    assert cancelled.status_code == duplicate.status_code == 200
    assert duplicate.json()["cancellation_state"] == "cancelled"
    assert retry.status_code == 409


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_request_schema_rejects_unknown_fields_and_regeneration_scope_misuse() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    body = request_body(goal, constraint)
    body["regeneration_scope"] = "branch"
    body["raw_content"] = "must not enter persistence"

    response = Client().post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_generation_request"


def make_stale_generated(canvas: Canvas, kind: str, title: str) -> Node:
    with transaction.atomic():
        operation = GraphOperation.objects.create(
            canvas=canvas,
            actor_type="test",
            operation_key=f"stale-{title}",
            request_fingerprint=f"stale-{title}",
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


@pytest.mark.parametrize(
    "metadata",
    (
        {"generated_by_run_id": "fixture"},
        {"generated_by_run_id": "fixture", "review_status": "provisional"},
        {"generated_by_run_id": "fixture", "review_status": "rejected"},
        {
            "generated_by_run_id": "fixture",
            "review_status": "accepted",
            "provisional": True,
        },
    ),
)
@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_generated_provisional_or_unaccepted_nodes_cannot_start_runs(
    metadata: dict[str, object],
) -> None:
    canvas = Canvas.objects.create(title="Review-state gate")
    strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Unaccepted generated strategy",
        metadata=metadata,
    )

    response = Client().post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(
            {
                "operation": "research_evidence",
                "selected_node_ids": [str(strategy.id)],
                "expected_node_versions": {str(strategy.id): strategy.version},
                "execution_profile_id": "phase2_test_v1",
                "idempotency_key": f"review-state-{strategy.id}",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_generation_selection"
    assert GenerationRun.objects.filter(canvas=canvas).count() == 0


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_branch_regeneration_freezes_cycle_safe_deduplicated_workset() -> None:
    canvas = Canvas.objects.create(title="Branch workset")
    root = make_stale_generated(canvas, NodeKind.STRATEGY, "Root")
    first_claim = make_stale_generated(canvas, NodeKind.CLAIM, "First claim")
    second_claim = make_stale_generated(canvas, NodeKind.CLAIM, "Second claim")
    Edge.objects.bulk_create(
        [
            Edge(canvas=canvas, source=root, target=first_claim, kind=EdgeKind.DERIVED_FROM),
            Edge(canvas=canvas, source=root, target=second_claim, kind=EdgeKind.DERIVED_FROM),
            Edge(
                canvas=canvas,
                source=first_claim,
                target=second_claim,
                kind=EdgeKind.DERIVED_FROM,
            ),
            Edge(
                canvas=canvas,
                source=second_claim,
                target=first_claim,
                kind=EdgeKind.DERIVED_FROM,
            ),
        ]
    )
    response = Client().post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(
            {
                "operation": "regenerate_stale",
                "selected_node_ids": [str(root.id)],
                "expected_node_versions": {str(root.id): 1},
                "execution_profile_id": "phase2_test_v1",
                "idempotency_key": "branch-cycle",
                "regeneration_scope": "branch",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 202, response.content
    targets = GenerationRun.objects.get(pk=response.json()["run_id"]).context_manifest[
        "regeneration"
    ]["targets"]
    assert [target["node_id"] for target in targets] == [
        str(root.id),
        *sorted([str(first_claim.id), str(second_claim.id)]),
    ]
    assert [target["distance"] for target in targets] == [0, 1, 1]
    assert len({target["node_id"] for target in targets}) == 3


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_canvas_lock_prevents_mixed_context_during_concurrent_mutation() -> None:
    canvas, goal, constraint = make_strategy_inputs()
    context_started = Event()
    release_context = Event()
    mutation_started = Event()

    class BlockingContextFactory(GraphRunContextFactory):
        def build(self, **kwargs):  # type: ignore[no-untyped-def]
            context_started.set()
            assert release_context.wait(timeout=5)
            return super().build(**kwargs)

    composition = replace(
        phase2_test_composition(),
        context_factory=BlockingContextFactory(),
    )
    request = GenerationRunRequest(
        operation="generate_strategies",
        selected_node_ids=[goal.id, constraint.id],
        expected_node_versions={goal.id: 1, constraint.id: 1},
        execution_profile_id="phase2_test_v1",
        idempotency_key="concurrent-snapshot",
    )

    def create_run_in_thread():  # type: ignore[no-untyped-def]
        close_old_connections()
        try:
            return create_generation_run(canvas.id, request)
        finally:
            close_old_connections()

    def mutate_in_thread() -> None:
        close_old_connections()
        try:
            mutation_started.set()
            with transaction.atomic():
                Canvas.objects.select_for_update().get(pk=canvas.id)
                Node.objects.filter(pk=goal.id).update(
                    title="After-state goal",
                    version=F("version") + 1,
                )
        finally:
            close_old_connections()

    with (
        patch("proofgraph.generation.services.get_composition", return_value=composition),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        create_future = executor.submit(create_run_in_thread)
        assert context_started.wait(timeout=5)
        mutation_future = executor.submit(mutate_in_thread)
        assert mutation_started.wait(timeout=5)
        with pytest.raises(TimeoutError):
            mutation_future.result(timeout=0.05)
        release_context.set()
        created = create_future.result(timeout=5)
        mutation_future.result(timeout=5)

    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    snapshot = {node["id"]: node for node in run.context_snapshot["nodes"]}
    goal.refresh_from_db()

    assert snapshot[str(goal.id)]["title"] == "Goal"
    assert snapshot[str(goal.id)]["version"] == 1
    assert goal.title == "After-state goal"
    assert goal.version == 2


@override_settings(GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION)
def test_overlong_source_excerpt_is_rejected_before_run_persistence() -> None:
    canvas = Canvas.objects.create(title="Source retention")
    strategy = Node.objects.create(canvas=canvas, kind=NodeKind.STRATEGY, title="Strategy")
    claim = Node.objects.create(canvas=canvas, kind=NodeKind.CLAIM, title="Claim")
    source = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Source",
        body="x" * 501,
    )
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=source,
        kind=EdgeKind.EXTRACTED_FROM,
    )

    response = Client().post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(
            {
                "operation": "synthesize_opportunities",
                "selected_node_ids": [str(strategy.id), str(claim.id)],
                "expected_node_versions": {str(strategy.id): 1, str(claim.id): 1},
                "execution_profile_id": "phase2_test_v1",
                "idempotency_key": "retention-rejection",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "retention_policy_violation"
    assert GenerationRun.objects.filter(canvas=canvas).count() == 0
