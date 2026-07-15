import json
import logging
import uuid

import pytest
from django.db import transaction
from django.test import Client

from proofgraph.generation.models import (
    GenerationRun,
    GraphPatch,
    GraphPatchOperationDecision,
    RunStatus,
)
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


def _run(
    canvas: Canvas,
    *,
    operation: str = "generate_strategies",
    context_manifest: dict[str, object] | None = None,
) -> GenerationRun:
    return GenerationRun.objects.create(
        canvas=canvas,
        operation=operation,
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint=str(uuid.uuid4()),
        status=RunStatus.COMPLETED,
        base_canvas_revision=canvas.revision,
        context_snapshot={"nodes": [], "edges": []},
        context_manifest=context_manifest or {"request": {"operation": operation}},
        context_hash=str(uuid.uuid4()),
        execution_configuration={
            "profile_id": "replay_v1",
            "fixture_bundle_id": "security_questionnaires_v1",
            "fixture_bundle_version": 1,
            "pipeline_version": "pipeline_v1",
            "prompt_version": "prompt_v1",
            "strategy_version": "strategy_v1",
        },
    )


def _patch(
    run: GenerationRun,
    operations: list[dict[str, object]],
    *,
    target_ids: list[str] | None = None,
    permitted_ids: list[str] | None = None,
) -> GraphPatch:
    return GraphPatch.objects.create(
        run=run,
        canvas=run.canvas,
        base_canvas_revision=run.base_canvas_revision,
        operations=operations,
        regeneration_target_ids=target_ids or [],
        permitted_stale_resolution_ids=permitted_ids or [],
    )


def _apply(patch: GraphPatch, operation_ids: list[str] | None = None):
    return Client().post(
        f"/api/graph-patches/{patch.id}/apply",
        data=json.dumps(
            {
                "selected_operation_ids": operation_ids,
                "apply_nonconflicting_only": False,
            }
        ),
        content_type="application/json",
    )


def _generated_node(canvas: Canvas, kind: str, title: str) -> Node:
    return Node.objects.create(
        canvas=canvas,
        kind=kind,
        title=title,
        metadata={"generated_by_run_id": "fixture", "review_status": "accepted"},
    )


def _mark_stale(canvas: Canvas, node: Node) -> NodeStalenessCause:
    with transaction.atomic():
        canvas.revision += 1
        canvas.save(update_fields=["revision"])
        operation = GraphOperation.objects.create(
            canvas=canvas,
            actor_type="direct",
            operation_key=str(uuid.uuid4()),
            request_fingerprint=str(uuid.uuid4()),
            operation_type="UPDATE_NODE",
            payload={"node_id": str(node.id)},
            result_payload={"canvas_revision": canvas.revision},
            canvas_revision=canvas.revision,
        )
        node.stale = True
        node.stale_since_revision = canvas.revision
        node.version += 1
        node.save(update_fields=["stale", "stale_since_revision", "version"])
        return NodeStalenessCause.objects.create(
            canvas=canvas,
            node=node,
            cause_graph_operation=operation,
            origin_entity_type="node",
            origin_entity_id=node.id,
        )


@pytest.mark.parametrize("operation_type", ["UPDATE_NODE", "PATCH_NODE_METADATA"])
def test_patch_node_mutations_propagate_transitive_staleness(operation_type: str) -> None:
    canvas = Canvas.objects.create(title=f"Patch {operation_type}")
    claim = _generated_node(canvas, NodeKind.CLAIM, "Upstream claim")
    opportunity = _generated_node(canvas, NodeKind.OPPORTUNITY, "Dependent opportunity")
    risk = _generated_node(canvas, NodeKind.RISK, "Dependent risk")
    Edge.objects.create(canvas=canvas, source=claim, target=opportunity, kind=EdgeKind.SUPPORTS)
    Edge.objects.create(
        canvas=canvas,
        source=opportunity,
        target=risk,
        kind=EdgeKind.DERIVED_FROM,
    )
    operation: dict[str, object] = {
        "operation_id": "mutate-claim",
        "op": operation_type,
        "depends_on": [],
        "node_id": str(claim.id),
        "expected_version": claim.version,
    }
    if operation_type == "UPDATE_NODE":
        operation["changes"] = {"title": "Updated upstream claim"}
    else:
        operation["metadata"] = {"strength": "high"}
    patch = _patch(_run(canvas), [operation])

    response = _apply(patch)

    assert response.status_code == 200, response.json()
    claim.refresh_from_db()
    opportunity.refresh_from_db()
    risk.refresh_from_db()
    assert claim.version == 2
    assert opportunity.stale is risk.stale is True
    assert opportunity.version == risk.version == 2
    audit = GraphOperation.objects.get(actor_type="graph_patch", operation_type=operation_type)
    assert audit.result_payload["newly_stale_node_ids"] == sorted(
        [str(opportunity.id), str(risk.id)]
    )
    assert set(
        NodeStalenessCause.objects.filter(cause_graph_operation=audit).values_list(
            "node_id", flat=True
        )
    ) == {opportunity.id, risk.id}


@pytest.mark.parametrize("operation_type", ["UPDATE_EDGE", "DELETE_EDGE"])
def test_patch_edge_mutations_capture_pre_and_post_dependency_paths(operation_type: str) -> None:
    canvas = Canvas.objects.create(title=f"Patch {operation_type}")
    source = _generated_node(canvas, NodeKind.CLAIM, "Source")
    target = _generated_node(canvas, NodeKind.OPPORTUNITY, "Target")
    leaf = _generated_node(canvas, NodeKind.RISK, "Leaf")
    edge = Edge.objects.create(
        canvas=canvas,
        source=source,
        target=target,
        kind=EdgeKind.SUPPORTS,
    )
    Edge.objects.create(canvas=canvas, source=target, target=leaf, kind=EdgeKind.DERIVED_FROM)
    operation: dict[str, object] = {
        "operation_id": "mutate-edge",
        "op": operation_type,
        "depends_on": [],
        "edge_id": str(edge.id),
        "expected_version": edge.version,
    }
    if operation_type == "UPDATE_EDGE":
        operation["changes"] = {"kind": EdgeKind.CONSTRAINED_BY}
    patch = _patch(_run(canvas), [operation])

    response = _apply(patch)

    assert response.status_code == 200, response.json()
    source.refresh_from_db()
    target.refresh_from_db()
    leaf.refresh_from_db()
    if operation_type == "UPDATE_EDGE":
        assert source.stale is True
    else:
        assert source.stale is False
    assert target.stale is leaf.stale is True
    audit = GraphOperation.objects.get(actor_type="graph_patch", operation_type=operation_type)
    expected = {target.id, leaf.id}
    if operation_type == "UPDATE_EDGE":
        expected.add(source.id)
    assert (
        set(
            NodeStalenessCause.objects.filter(cause_graph_operation=audit).values_list(
                "node_id", flat=True
            )
        )
        == expected
    )


def test_parallel_regeneration_requires_complete_lineage_group_and_preserves_old_branch(
    caplog,
) -> None:
    canvas = Canvas.objects.create(title="Parallel regeneration")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Goal")
    old_strategy = _generated_node(canvas, NodeKind.STRATEGY, "Old strategy")
    Edge.objects.create(
        canvas=canvas,
        source=goal,
        target=old_strategy,
        kind=EdgeKind.EVOLVES_INTO,
    )
    old_constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Low capital",
        body="Keep spend low.",
        metadata={"context_scope": "branch", "pinned": True, "category": "capital"},
        branch_root=old_strategy,
    )
    cause = _mark_stale(canvas, old_strategy)
    run = _run(
        canvas,
        operation="regenerate_stale",
        context_manifest={
            "request": {"operation": "regenerate_stale", "regeneration_scope": "branch"},
            "regeneration": {
                "scope": "branch",
                "targets": [
                    {
                        "node_id": str(old_strategy.id),
                        "kind": NodeKind.STRATEGY,
                        "stale_node_ids": [str(old_strategy.id)],
                    }
                ],
            },
        },
    )
    successor_id = "successor-strategy"
    operations = [
        {
            "operation_id": "add-successor",
            "op": "ADD_NODE",
            "depends_on": [],
            "client_generated_id": successor_id,
            "node": {
                "kind": NodeKind.STRATEGY,
                "title": "Fresh strategy",
                "body": "A parallel successor.",
                "metadata": {
                    "generated_by_run_id": str(run.id),
                    "provenance_node_ids": [str(goal.id)],
                    "review_status": "provisional",
                    "regenerated_from_node_id": str(old_strategy.id),
                    "regeneration_scope": "branch",
                    "lineage_mode": "parallel",
                },
            },
        },
        {
            "operation_id": "add-lineage",
            "op": "ADD_EDGE",
            "depends_on": ["add-successor"],
            "client_generated_id": "successor-lineage",
            "edge": {
                "source_node_id": str(old_strategy.id),
                "target_node_id": successor_id,
                "kind": EdgeKind.EVOLVES_INTO,
                "metadata": {"generated_by_run_id": str(run.id)},
            },
        },
        {
            "operation_id": "clone-constraint",
            "op": "ADD_NODE",
            "depends_on": ["add-lineage", "add-successor"],
            "client_generated_id": "cloned-constraint",
            "node": {
                "kind": NodeKind.CONSTRAINT,
                "title": old_constraint.title,
                "body": old_constraint.body,
                "metadata": {
                    **old_constraint.metadata,
                    "generated_by_run_id": str(run.id),
                    "provenance_node_ids": [str(old_constraint.id)],
                    "review_status": "provisional",
                },
                "branch_root_node_id": successor_id,
            },
        },
    ]
    patch = _patch(
        run,
        operations,
        target_ids=[str(old_strategy.id)],
        permitted_ids=[str(old_strategy.id)],
    )

    incomplete = _apply(patch, ["add-successor"])

    assert incomplete.status_code == 409, incomplete.json()
    assert incomplete.json()["error"]["code"] == "patch_regeneration_dependency_incomplete"
    assert patch.canvas.nodes.count() == 3
    assert not GraphPatchOperationDecision.objects.filter(patch=patch).exists()

    with caplog.at_level(logging.INFO, logger="proofgraph.generation"):
        applied = _apply(patch)

    assert applied.status_code == 200, applied.json()
    patch.refresh_from_db()
    old_strategy.refresh_from_db()
    old_constraint.refresh_from_db()
    cause.refresh_from_db()
    successor = Node.objects.get(pk=patch.client_id_map[successor_id])
    clone = Node.objects.get(pk=patch.client_id_map["cloned-constraint"])
    assert old_strategy.stale is True
    assert old_strategy.stale_since_revision == cause.cause_graph_operation.canvas_revision
    assert cause.cleared_at is cause.cleared_by_graph_operation_id is None
    assert old_constraint.branch_root == old_strategy
    assert successor.stale is False
    assert successor.metadata["review_status"] == "accepted"
    assert successor.metadata["regenerated_from_node_id"] == str(old_strategy.id)
    assert successor.metadata["regeneration_scope"] == "branch"
    assert successor.metadata["lineage_mode"] == "parallel"
    assert Edge.objects.filter(
        canvas=canvas,
        source=old_strategy,
        target=successor,
        kind=EdgeKind.EVOLVES_INTO,
    ).exists()
    assert clone.branch_root == successor
    assert clone.metadata["review_status"] == "accepted"
    telemetry = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "proofgraph.generation"
        and json.loads(record.getMessage()).get("event") == "patch.applied"
    ][-1]
    assert telemetry["regeneration_scope"] == "branch"
    assert telemetry["regeneration_workset_size"] == 1
    assert telemetry["accepted_resolution_count"] == 0
    assert telemetry["lineage_mode"] == "parallel"

    replay = _apply(patch)
    assert replay.status_code == 200
    assert replay.json() == applied.json()
    assert (
        NodeStalenessCause.objects.filter(node=old_strategy, cleared_at__isnull=True).count() == 1
    )


def test_opportunity_family_regeneration_preserves_a_fresh_production_root() -> None:
    canvas = Canvas.objects.create(title="Opportunity-family regeneration")
    old_opportunity = _generated_node(
        canvas,
        NodeKind.OPPORTUNITY,
        "Current opportunity root",
    )
    stale_assumption = _generated_node(
        canvas,
        NodeKind.ASSUMPTION,
        "Stale family assumption",
    )
    Edge.objects.create(
        canvas=canvas,
        source=old_opportunity,
        target=stale_assumption,
        kind=EdgeKind.DERIVED_FROM,
    )
    cause = _mark_stale(canvas, stale_assumption)
    run = _run(
        canvas,
        operation="regenerate_stale",
        context_manifest={
            "request": {"operation": "regenerate_stale", "regeneration_scope": "node"},
            "regeneration": {
                "scope": "node",
                "targets": [
                    {
                        "node_id": str(old_opportunity.id),
                        "kind": NodeKind.OPPORTUNITY,
                        "member_node_ids": [
                            str(old_opportunity.id),
                            str(stale_assumption.id),
                        ],
                        "stale_node_ids": [str(stale_assumption.id)],
                    }
                ],
            },
        },
    )
    successor_id = "successor-opportunity"
    operations = [
        {
            "operation_id": "add-successor",
            "op": "ADD_NODE",
            "depends_on": [],
            "client_generated_id": successor_id,
            "node": {
                "kind": NodeKind.OPPORTUNITY,
                "title": "Fresh opportunity successor",
                "body": "A parallel opportunity-family successor.",
                "metadata": {
                    "generated_by_run_id": str(run.id),
                    "provenance_node_ids": [],
                    "review_status": "provisional",
                    "regenerated_from_node_id": str(old_opportunity.id),
                    "regeneration_scope": "node",
                    "lineage_mode": "parallel",
                },
            },
        },
        {
            "operation_id": "add-lineage",
            "op": "ADD_EDGE",
            "depends_on": ["add-successor"],
            "client_generated_id": "opportunity-lineage",
            "edge": {
                "source_node_id": str(old_opportunity.id),
                "target_node_id": successor_id,
                "kind": EdgeKind.EVOLVES_INTO,
                "metadata": {"generated_by_run_id": str(run.id)},
            },
        },
    ]
    patch = _patch(
        run,
        operations,
        target_ids=[str(old_opportunity.id)],
        permitted_ids=[str(stale_assumption.id)],
    )

    response = _apply(patch)

    assert response.status_code == 200, response.json()
    old_opportunity.refresh_from_db()
    stale_assumption.refresh_from_db()
    cause.refresh_from_db()
    successor = Node.objects.get(pk=response.json()["client_id_map"][successor_id])
    assert old_opportunity.stale is False
    assert old_opportunity.version == 1
    assert stale_assumption.stale is True
    assert cause.cleared_at is cause.cleared_by_graph_operation_id is None
    assert successor.metadata["regenerated_from_node_id"] == str(old_opportunity.id)
