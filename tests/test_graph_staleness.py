import json
import uuid
from unittest.mock import patch

import pytest
from django.test import Client

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


def _node(canvas: Canvas, kind: str, title: str) -> Node:
    return Node.objects.create(
        canvas=canvas,
        kind=kind,
        title=title,
        metadata={"generated_by_run_id": "staleness-test", "review_status": "accepted"},
        context_token_count=42,
        context_content_hash=f"hash-{title}",
    )


def _post(canvas: Canvas, payload: dict[str, object]):
    return Client().post(
        f"/api/canvases/{canvas.id}/operations",
        data=json.dumps(payload),
        content_type="application/json",
    )


def _update(canvas: Canvas, node: Node, *, key: str | None = None):
    return _post(
        canvas,
        {
            "op": "UPDATE_NODE",
            "operation_key": key or str(uuid.uuid4()),
            "node_id": str(node.id),
            "expected_version": node.version,
            "changes": {"title": f"{node.title} updated"},
        },
    )


@pytest.mark.parametrize(
    ("kind", "reverse"),
    [
        (EdgeKind.SUPPORTS, False),
        (EdgeKind.CONTRADICTS, False),
        (EdgeKind.DERIVED_FROM, False),
        (EdgeKind.EVOLVES_INTO, False),
        (EdgeKind.REQUIRES_VALIDATION, False),
        (EdgeKind.CONSTRAINED_BY, True),
        (EdgeKind.EXTRACTED_FROM, True),
    ],
)
def test_semantic_edits_follow_every_invalidation_direction(kind: str, reverse: bool) -> None:
    canvas = Canvas.objects.create(title=f"Direction {kind}")
    origin = _node(canvas, NodeKind.CLAIM, "Origin")
    dependent = _node(canvas, NodeKind.OPPORTUNITY, "Dependent")
    Edge.objects.create(
        canvas=canvas,
        source=dependent if reverse else origin,
        target=origin if reverse else dependent,
        kind=kind,
    )

    response = _update(canvas, origin)

    assert response.status_code == 200, response.json()
    dependent.refresh_from_db()
    origin.refresh_from_db()
    assert origin.stale is False
    assert dependent.stale is True
    assert dependent.stale_since_revision == 1
    assert dependent.version == 2
    assert dependent.context_token_count is None
    assert dependent.context_content_hash is None
    cause = NodeStalenessCause.objects.get(node=dependent)
    assert cause.origin_entity_type == "node"
    assert cause.origin_entity_id == origin.id
    assert cause.cause_graph_operation.operation_type == "UPDATE_NODE"


def test_cycle_and_converging_paths_mark_each_descendant_once_per_originating_operation() -> None:
    canvas = Canvas.objects.create(title="Cycle and convergence")
    origin = _node(canvas, NodeKind.STRATEGY, "Origin")
    left = _node(canvas, NodeKind.CLAIM, "Left")
    right = _node(canvas, NodeKind.CLAIM, "Right")
    leaf = _node(canvas, NodeKind.OPPORTUNITY, "Leaf")
    for source, target in (
        (origin, left),
        (origin, right),
        (left, leaf),
        (right, leaf),
        (leaf, origin),
    ):
        Edge.objects.create(
            canvas=canvas,
            source=source,
            target=target,
            kind=EdgeKind.DERIVED_FROM,
        )

    first = _update(canvas, origin)
    assert first.status_code == 200
    assert first.json()["newly_stale_node_ids"] == sorted(
        [str(left.id), str(right.id), str(leaf.id)]
    )
    origin.refresh_from_db()
    for node in (left, right, leaf):
        node.refresh_from_db()
        assert node.stale is True
        assert node.version == 2
        assert NodeStalenessCause.objects.filter(node=node).count() == 1
    assert origin.stale is False

    second = _update(canvas, origin)
    assert second.status_code == 200
    assert second.json()["newly_stale_node_ids"] == []
    for node in (left, right, leaf):
        node.refresh_from_db()
        assert node.version == 2
        assert NodeStalenessCause.objects.filter(node=node).count() == 2


def test_edge_delete_uses_predelete_relationship_and_retry_is_idempotent() -> None:
    canvas = Canvas.objects.create(title="Edge delete")
    source = _node(canvas, NodeKind.CLAIM, "Source")
    middle = _node(canvas, NodeKind.OPPORTUNITY, "Middle")
    leaf = _node(canvas, NodeKind.RISK, "Leaf")
    removed = Edge.objects.create(
        canvas=canvas,
        source=source,
        target=middle,
        kind=EdgeKind.SUPPORTS,
    )
    Edge.objects.create(
        canvas=canvas,
        source=middle,
        target=leaf,
        kind=EdgeKind.DERIVED_FROM,
    )
    key = str(uuid.uuid4())
    payload = {
        "op": "DELETE_EDGE",
        "operation_key": key,
        "edge_id": str(removed.id),
        "expected_version": 1,
    }

    first = _post(canvas, payload)
    replay = _post(canvas, payload)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["stale_node_ids"] == sorted([str(middle.id), str(leaf.id)])
    operation = GraphOperation.objects.get(operation_key=key)
    for node in (middle, leaf):
        node.refresh_from_db()
        assert node.stale is True
        cause = NodeStalenessCause.objects.get(node=node)
        assert cause.cause_graph_operation == operation
        assert cause.origin_entity_type == "edge"
        assert cause.origin_entity_id == removed.id
    assert NodeStalenessCause.objects.count() == 2


def test_edge_update_invalidates_both_old_and_new_dependency_relationships() -> None:
    canvas = Canvas.objects.create(title="Edge update")
    source = _node(canvas, NodeKind.CLAIM, "Source")
    target = _node(canvas, NodeKind.OPPORTUNITY, "Target")
    edge = Edge.objects.create(
        canvas=canvas,
        source=source,
        target=target,
        kind=EdgeKind.SUPPORTS,
    )

    response = _post(
        canvas,
        {
            "op": "UPDATE_EDGE",
            "operation_key": str(uuid.uuid4()),
            "edge_id": str(edge.id),
            "expected_version": 1,
            "changes": {"kind": EdgeKind.CONSTRAINED_BY},
        },
    )

    assert response.status_code == 200, response.json()
    source.refresh_from_db()
    target.refresh_from_db()
    assert source.stale is True
    assert target.stale is True
    assert set(response.json()["stale_node_ids"]) == {str(source.id), str(target.id)}


def test_assumption_replacement_audits_previous_value_and_stales_its_family_owner() -> None:
    canvas = Canvas.objects.create(title="Assumption replacement")
    opportunity = _node(canvas, NodeKind.OPPORTUNITY, "Opportunity")
    assumption = _node(canvas, NodeKind.ASSUMPTION, "Old assumption")
    risk = _node(canvas, NodeKind.RISK, "Risk")
    Edge.objects.create(
        canvas=canvas,
        source=opportunity,
        target=assumption,
        kind=EdgeKind.DERIVED_FROM,
    )
    Edge.objects.create(
        canvas=canvas,
        source=opportunity,
        target=risk,
        kind=EdgeKind.DERIVED_FROM,
    )

    response = _post(
        canvas,
        {
            "op": "REPLACE_ASSUMPTION",
            "operation_key": str(uuid.uuid4()),
            "node_id": str(assumption.id),
            "expected_version": 1,
            "replacement": {
                "title": "Buyers repeat the workflow weekly",
                "body": "Validate recurrence before investing further.",
            },
        },
    )

    assert response.status_code == 200, response.json()
    assert response.json()["previous_assumption"] == {
        "title": "Old assumption",
        "body": None,
        "version": 1,
        "opportunity_id": str(opportunity.id),
    }
    assumption.refresh_from_db()
    opportunity.refresh_from_db()
    risk.refresh_from_db()
    assert assumption.title == "Buyers repeat the workflow weekly"
    assert assumption.version == 2
    assert assumption.stale is False
    assert opportunity.stale is True
    assert risk.stale is True
    assert set(response.json()["newly_stale_node_ids"]) == {
        str(opportunity.id),
        str(risk.id),
    }


def test_layout_and_add_operations_do_not_create_staleness_causes() -> None:
    canvas = Canvas.objects.create(title="No false invalidation")
    node = _node(canvas, NodeKind.OPPORTUNITY, "Movable")

    move = _post(
        canvas,
        {
            "op": "MOVE_NODE",
            "operation_key": str(uuid.uuid4()),
            "node_id": str(node.id),
            "expected_position_version": 1,
            "position": {"x": 10, "y": 20},
        },
    )
    add = _post(
        canvas,
        {
            "op": "ADD_NODE",
            "operation_key": str(uuid.uuid4()),
            "node": {"kind": NodeKind.GOAL, "title": "New goal"},
        },
    )

    assert move.status_code == add.status_code == 200
    assert move.json()["stale_node_ids"] == []
    assert add.json()["stale_node_ids"] == []
    assert not NodeStalenessCause.objects.exists()


def test_semantic_edit_invalidates_api_created_descendants_without_review_status() -> None:
    canvas = Canvas.objects.create(title="Manual graph invalidation")
    origin_response = _post(
        canvas,
        {
            "op": "ADD_NODE",
            "operation_key": str(uuid.uuid4()),
            "node": {"kind": NodeKind.GOAL, "title": "Manual goal"},
        },
    )
    dependent_response = _post(
        canvas,
        {
            "op": "ADD_NODE",
            "operation_key": str(uuid.uuid4()),
            "node": {"kind": NodeKind.STRATEGY, "title": "Manual strategy"},
        },
    )
    assert origin_response.status_code == dependent_response.status_code == 200
    origin = origin_response.json()["node"]
    dependent = dependent_response.json()["node"]
    edge_response = _post(
        canvas,
        {
            "op": "ADD_EDGE",
            "operation_key": str(uuid.uuid4()),
            "edge": {
                "source_node_id": origin["id"],
                "target_node_id": dependent["id"],
                "kind": EdgeKind.DERIVED_FROM,
            },
        },
    )
    assert edge_response.status_code == 200

    update_response = _post(
        canvas,
        {
            "op": "UPDATE_NODE",
            "operation_key": str(uuid.uuid4()),
            "node_id": origin["id"],
            "expected_version": origin["version"],
            "changes": {"title": "Updated manual goal"},
        },
    )

    assert update_response.status_code == 200, update_response.json()
    assert update_response.json()["newly_stale_node_ids"] == [dependent["id"]]
    dependent_node = Node.objects.get(pk=dependent["id"])
    assert dependent_node.metadata == {}
    assert dependent_node.stale is True


def test_rejecting_a_source_rejects_its_solely_supported_claim_and_stales_descendants() -> None:
    canvas = Canvas.objects.create(title="Reject sole support")
    source = _node(canvas, NodeKind.SOURCE, "Only source")
    source.metadata["independence_key"] = "publisher:one"
    source.save(update_fields=["metadata"])
    claim = _node(canvas, NodeKind.CLAIM, "Solely supported claim")
    opportunity = _node(canvas, NodeKind.OPPORTUNITY, "Dependent opportunity")
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=source,
        kind=EdgeKind.EXTRACTED_FROM,
    )
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=opportunity,
        kind=EdgeKind.SUPPORTS,
    )
    key = str(uuid.uuid4())
    payload = {
        "op": "REJECT_EVIDENCE",
        "operation_key": key,
        "node_id": str(source.id),
        "expected_version": 1,
    }

    response = _post(canvas, payload)
    replay = _post(canvas, payload)

    assert response.status_code == replay.status_code == 200, response.json()
    assert replay.json() == response.json()
    assert response.json()["rejected_claim_ids"] == [str(claim.id)]
    assert response.json()["newly_stale_node_ids"] == [str(opportunity.id)]
    operation = GraphOperation.objects.get(operation_key=key)
    source.refresh_from_db()
    claim.refresh_from_db()
    opportunity.refresh_from_db()
    assert source.metadata["review_status"] == "rejected"
    assert source.metadata["rejected_by_operation_id"] == operation.id
    assert source.version == 2
    assert claim.metadata["review_status"] == "rejected"
    assert claim.metadata["eligible_source_ids"] == []
    assert claim.metadata["independent_support_count"] == 0
    assert claim.metadata["rejected_by_operation_id"] == operation.id
    assert claim.version == 2
    assert opportunity.stale is True
    assert opportunity.version == 2
    assert NodeStalenessCause.objects.get(node=opportunity).cause_graph_operation == operation
    assert GraphOperation.objects.filter(operation_key=key).count() == 1


def test_rejecting_one_source_preserves_independently_supported_claim() -> None:
    canvas = Canvas.objects.create(title="Preserve independent support")
    rejected_source = _node(canvas, NodeKind.SOURCE, "Rejected source")
    rejected_source.metadata["independence_key"] = "publisher:one"
    rejected_source.save(update_fields=["metadata"])
    retained_source = _node(canvas, NodeKind.SOURCE, "Retained source")
    retained_source.metadata["independence_key"] = "publisher:two"
    retained_source.save(update_fields=["metadata"])
    claim = _node(canvas, NodeKind.CLAIM, "Multiply supported claim")
    opportunity = _node(canvas, NodeKind.OPPORTUNITY, "Dependent opportunity")
    for source in (rejected_source, retained_source):
        Edge.objects.create(
            canvas=canvas,
            source=claim,
            target=source,
            kind=EdgeKind.EXTRACTED_FROM,
        )
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=opportunity,
        kind=EdgeKind.SUPPORTS,
    )

    response = _post(
        canvas,
        {
            "op": "REJECT_EVIDENCE",
            "operation_key": str(uuid.uuid4()),
            "node_id": str(rejected_source.id),
            "expected_version": 1,
        },
    )

    assert response.status_code == 200, response.json()
    assert response.json()["rejected_claim_ids"] == []
    assert response.json()["retained_claim_ids"] == [str(claim.id)]
    claim.refresh_from_db()
    opportunity.refresh_from_db()
    assert claim.metadata["review_status"] == "accepted"
    assert claim.metadata["eligible_source_ids"] == [str(retained_source.id)]
    assert claim.metadata["eligible_independence_keys"] == ["publisher:two"]
    assert claim.metadata["independent_support_count"] == 1
    assert claim.version == 2
    assert claim.stale is False
    assert opportunity.stale is True


def test_direct_claim_rejection_is_visible_audited_and_invalidates_descendants() -> None:
    canvas = Canvas.objects.create(title="Reject claim")
    claim = _node(canvas, NodeKind.CLAIM, "Rejected claim")
    opportunity = _node(canvas, NodeKind.OPPORTUNITY, "Dependent opportunity")
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=opportunity,
        kind=EdgeKind.CONTRADICTS,
    )

    response = _post(
        canvas,
        {
            "op": "REJECT_EVIDENCE",
            "operation_key": str(uuid.uuid4()),
            "node_id": str(claim.id),
            "expected_version": 1,
        },
    )

    assert response.status_code == 200, response.json()
    claim.refresh_from_db()
    opportunity.refresh_from_db()
    assert claim.metadata["review_status"] == "rejected"
    assert claim.stale is False
    assert opportunity.stale is True
    assert response.json()["stale_node_ids"] == [str(opportunity.id)]


@pytest.mark.parametrize("kind", [EdgeKind.SUPPORTS, EdgeKind.CONTRADICTS])
def test_rejecting_source_invalidates_direct_opportunity_dependencies(kind: str) -> None:
    canvas = Canvas.objects.create(title=f"Reject direct source {kind}")
    source = _node(canvas, NodeKind.SOURCE, "Direct source")
    opportunity = _node(canvas, NodeKind.OPPORTUNITY, "Directly dependent opportunity")
    Edge.objects.create(
        canvas=canvas,
        source=source,
        target=opportunity,
        kind=kind,
    )

    response = _post(
        canvas,
        {
            "op": "REJECT_EVIDENCE",
            "operation_key": str(uuid.uuid4()),
            "node_id": str(source.id),
            "expected_version": source.version,
        },
    )

    assert response.status_code == 200, response.json()
    source.refresh_from_db()
    opportunity.refresh_from_db()
    assert source.metadata["review_status"] == "rejected"
    assert opportunity.stale is True
    assert response.json()["newly_stale_node_ids"] == [str(opportunity.id)]
    cause = NodeStalenessCause.objects.get(node=opportunity)
    assert cause.origin_entity_type == "node"
    assert cause.origin_entity_id == source.id


def test_evidence_rejection_rolls_back_review_support_and_staleness_together() -> None:
    canvas = Canvas.objects.create(title="Atomic rejection")
    source = _node(canvas, NodeKind.SOURCE, "Source")
    claim = _node(canvas, NodeKind.CLAIM, "Claim")
    opportunity = _node(canvas, NodeKind.OPPORTUNITY, "Opportunity")
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=source,
        kind=EdgeKind.EXTRACTED_FROM,
    )
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=opportunity,
        kind=EdgeKind.SUPPORTS,
    )

    with (
        patch("proofgraph.graph.operations.apply_staleness", side_effect=RuntimeError("stop")),
        pytest.raises(RuntimeError, match="stop"),
    ):
        _post(
            canvas,
            {
                "op": "REJECT_EVIDENCE",
                "operation_key": str(uuid.uuid4()),
                "node_id": str(source.id),
                "expected_version": 1,
            },
        )

    canvas.refresh_from_db()
    for node in (source, claim, opportunity):
        node.refresh_from_db()
        assert node.metadata["review_status"] == "accepted"
        assert node.version == 1
        assert node.stale is False
    assert canvas.revision == 0
    assert not GraphOperation.objects.exists()
    assert not NodeStalenessCause.objects.exists()
