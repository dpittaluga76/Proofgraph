import uuid
from collections.abc import Callable

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

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


def enforce_deferred_constraints() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def assert_rejected(
    action: Callable[[], object],
    error: type[DatabaseError] = IntegrityError,
) -> None:
    with pytest.raises(error), transaction.atomic():
        action()
        enforce_deferred_constraints()


def make_canvas(title: str = "Opportunity map") -> Canvas:
    return Canvas.objects.create(title=title)


def make_node(
    canvas: Canvas,
    *,
    kind: str = NodeKind.GOAL,
    title: str = "Node",
    metadata: dict[str, object] | None = None,
    branch_root: Node | None = None,
    stale: bool = False,
    stale_since_revision: int | None = None,
) -> Node:
    return Node.objects.create(
        canvas=canvas,
        kind=kind,
        title=title,
        metadata={} if metadata is None else metadata,
        branch_root=branch_root,
        stale=stale,
        stale_since_revision=stale_since_revision,
    )


def make_operation(
    canvas: Canvas,
    *,
    key: str | None = None,
    actor_type: str = "user",
    revision: int = 1,
) -> GraphOperation:
    operation_key = key or str(uuid.uuid4())
    return GraphOperation.objects.create(
        canvas=canvas,
        actor_type=actor_type,
        actor_id="actor-1",
        operation_key=operation_key,
        request_fingerprint=f"fingerprint:{operation_key}",
        operation_type="UPDATE_NODE",
        payload={"operation_key": operation_key},
        result_payload={"canvas_revision": revision},
        canvas_revision=revision,
    )


def make_stale_node(
    canvas: Canvas,
) -> tuple[Node, GraphOperation, NodeStalenessCause]:
    with transaction.atomic():
        operation = make_operation(canvas)
        node = make_node(canvas, stale=True, stale_since_revision=operation.canvas_revision)
        cause = NodeStalenessCause.objects.create(
            canvas=canvas,
            node=node,
            cause_graph_operation=operation,
            origin_entity_type="node",
            origin_entity_id=uuid.uuid4(),
        )
        enforce_deferred_constraints()
    return node, operation, cause


def test_frozen_node_and_edge_taxonomies_are_persisted() -> None:
    assert list(NodeKind.values) == [
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
    ]
    assert list(EdgeKind.values) == [
        "supports",
        "contradicts",
        "derived_from",
        "constrained_by",
        "evolves_into",
        "requires_validation",
        "extracted_from",
    ]

    canvas = make_canvas()
    source = make_node(canvas)
    target = make_node(canvas, kind=NodeKind.CLAIM)

    assert_rejected(lambda: make_node(canvas, kind="custom_kind"))
    assert_rejected(
        lambda: Edge.objects.create(
            canvas=canvas,
            source=source,
            target=target,
            kind="custom_edge",
        )
    )


def test_node_semantic_and_position_state_have_independent_versions_and_timestamps() -> None:
    canvas = make_canvas()
    node = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.OPPORTUNITY,
        title="Managed Django Fleet",
        body="A fixed-price operations platform",
        metadata={"buyer": "Agencies"},
        position={"x": 812, "y": 405},
    )

    semantic_updated_at = node.semantic_updated_at
    position_updated_at = timezone.now()
    node.position = {"x": 900, "y": 450}
    node.position_version = 2
    node.position_updated_at = position_updated_at
    node.updated_at = position_updated_at
    node.save(update_fields=["position", "position_version", "position_updated_at", "updated_at"])
    node.refresh_from_db()

    assert node.metadata == {"buyer": "Agencies"}
    assert node.position == {"x": 900, "y": 450}
    assert node.version == 1
    assert node.position_version == 2
    assert node.semantic_updated_at == semantic_updated_at
    assert node.position_updated_at == position_updated_at
    assert node.context_representation_version == 1


def test_constraint_scope_and_branch_anchor_rules_are_database_enforced() -> None:
    canvas = make_canvas()
    other_canvas = make_canvas("Other")
    root = make_node(canvas, kind=NodeKind.STRATEGY)
    claim_root = make_node(canvas, kind=NodeKind.CLAIM)
    opportunity_root = make_node(canvas, kind=NodeKind.OPPORTUNITY)
    wrong_kind_root = make_node(canvas, kind=NodeKind.GOAL)
    other_root = make_node(other_canvas, kind=NodeKind.STRATEGY)

    global_constraint = make_node(
        canvas,
        kind=NodeKind.CONSTRAINT,
        metadata={"context_scope": "global", "pinned": True},
    )
    for branch_root in (root, claim_root, opportunity_root):
        make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "branch", "pinned": False},
            branch_root=branch_root,
        )

    assert global_constraint.branch_root_id is None

    invalid_constraints: list[Callable[[], Node]] = [
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "global", "pinned": True},
            branch_root=root,
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "branch", "pinned": True},
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "branch", "pinned": "yes"},
            branch_root=root,
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "workspace", "pinned": True},
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "global"},
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "branch", "pinned": True},
            branch_root=wrong_kind_root,
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.CONSTRAINT,
            metadata={"context_scope": "branch", "pinned": True},
            branch_root=other_root,
        ),
        lambda: make_node(
            canvas,
            kind=NodeKind.GOAL,
            metadata={"context_scope": "global", "pinned": True},
        ),
        lambda: make_node(canvas, kind=NodeKind.GOAL, branch_root=root),
    ]
    for invalid_constraint in invalid_constraints:
        assert_rejected(invalid_constraint)


def test_referenced_branch_root_cannot_change_to_an_invalid_kind_or_be_deleted() -> None:
    canvas = make_canvas()
    root = make_node(canvas, kind=NodeKind.STRATEGY)
    make_node(
        canvas,
        kind=NodeKind.CONSTRAINT,
        metadata={"context_scope": "branch", "pinned": True},
        branch_root=root,
    )

    def change_root_kind() -> None:
        Node.objects.filter(pk=root.pk).update(kind=NodeKind.GOAL)

    assert_rejected(change_root_kind)
    assert_rejected(lambda: Node.objects.filter(pk=root.pk).delete())


def test_edges_require_existing_same_canvas_endpoints() -> None:
    canvas = make_canvas()
    other_canvas = make_canvas("Other")
    source = make_node(canvas, kind=NodeKind.SOURCE)
    target = make_node(canvas, kind=NodeKind.CLAIM)
    other_target = make_node(other_canvas, kind=NodeKind.CLAIM)

    edge = Edge.objects.create(
        canvas=canvas,
        source=source,
        target=target,
        kind=EdgeKind.SUPPORTS,
        metadata={"confidence": 0.9},
    )
    assert edge.version == 1

    assert_rejected(
        lambda: Edge.objects.create(
            canvas=canvas,
            source=source,
            target=other_target,
            kind=EdgeKind.SUPPORTS,
        )
    )
    assert_rejected(
        lambda: Edge.objects.create(
            canvas=canvas,
            source_id=uuid.uuid4(),
            target=target,
            kind=EdgeKind.SUPPORTS,
        )
    )


def test_graph_operations_are_actor_scoped_idempotency_records_and_append_only() -> None:
    canvas = make_canvas()
    operation = make_operation(canvas, key="retry-key", actor_type="user")
    make_operation(canvas, key="retry-key", actor_type="system", revision=2)

    assert operation.request_fingerprint == "fingerprint:retry-key"
    assert operation.payload == {"operation_key": "retry-key"}
    assert operation.result_payload == {"canvas_revision": 1}
    assert_rejected(lambda: make_operation(canvas, key="retry-key", actor_type="user"))

    def update_operation() -> None:
        GraphOperation.objects.filter(pk=operation.pk).update(canvas_revision=99)

    def delete_operation() -> None:
        GraphOperation.objects.filter(pk=operation.pk).delete()

    assert_rejected(update_operation, DatabaseError)
    assert_rejected(delete_operation, DatabaseError)
    operation.refresh_from_db()
    assert operation.canvas_revision == 1


def test_stale_flag_requires_an_active_same_canvas_operation_linked_cause() -> None:
    canvas = make_canvas()
    other_canvas = make_canvas("Other")
    node, operation, cause = make_stale_node(canvas)

    assert node.stale is True
    assert cause.cleared_at is None
    assert cause.cause_graph_operation == operation

    assert_rejected(
        lambda: make_node(canvas, stale=True, stale_since_revision=2),
    )
    assert_rejected(
        lambda: make_node(canvas, stale=False, stale_since_revision=2),
    )

    def duplicate_cause() -> None:
        NodeStalenessCause.objects.create(
            canvas=canvas,
            node=node,
            cause_graph_operation=operation,
            origin_entity_type="node",
            origin_entity_id=uuid.uuid4(),
        )

    assert_rejected(duplicate_cause)

    fresh_node = make_node(canvas)
    another_operation = make_operation(canvas, revision=2)

    def add_cause_to_fresh_node() -> None:
        NodeStalenessCause.objects.create(
            canvas=canvas,
            node=fresh_node,
            cause_graph_operation=another_operation,
            origin_entity_type="edge",
            origin_entity_id=uuid.uuid4(),
        )

    assert_rejected(add_cause_to_fresh_node)

    foreign_operation = make_operation(other_canvas)

    def add_cross_canvas_cause() -> None:
        cross_canvas_node = make_node(canvas, stale=True, stale_since_revision=3)
        NodeStalenessCause.objects.create(
            canvas=canvas,
            node=cross_canvas_node,
            cause_graph_operation=foreign_operation,
            origin_entity_type="node",
            origin_entity_id=uuid.uuid4(),
        )

    assert_rejected(add_cross_canvas_cause)

    def attach_node_under_wrong_canvas() -> None:
        NodeStalenessCause.objects.create(
            canvas=other_canvas,
            node=node,
            cause_graph_operation=foreign_operation,
            origin_entity_type="node",
            origin_entity_id=uuid.uuid4(),
        )

    assert_rejected(attach_node_under_wrong_canvas)


def test_staleness_causes_allow_only_one_audited_clearing_transition() -> None:
    canvas = make_canvas()
    node, _, first_cause = make_stale_node(canvas)

    with transaction.atomic():
        second_operation = make_operation(canvas, revision=2)
        second_cause = NodeStalenessCause.objects.create(
            canvas=canvas,
            node=node,
            cause_graph_operation=second_operation,
            origin_entity_type="edge",
            origin_entity_id=uuid.uuid4(),
        )
        enforce_deferred_constraints()

    with transaction.atomic():
        first_clear_operation = make_operation(canvas, revision=3)
        first_cause.cleared_by_graph_operation = first_clear_operation
        first_cause.cleared_at = timezone.now()
        first_cause.save(update_fields=["cleared_by_graph_operation", "cleared_at"])
        enforce_deferred_constraints()

    node.refresh_from_db()
    assert node.stale is True

    with transaction.atomic():
        final_clear_operation = make_operation(canvas, revision=4)
        node.stale = False
        node.stale_since_revision = None
        node.save(update_fields=["stale", "stale_since_revision"])
        second_cause.cleared_by_graph_operation = final_clear_operation
        second_cause.cleared_at = timezone.now()
        second_cause.save(update_fields=["cleared_by_graph_operation", "cleared_at"])
        enforce_deferred_constraints()

    node.refresh_from_db()
    assert node.stale is False
    assert NodeStalenessCause.objects.filter(node=node, cleared_at__isnull=True).count() == 0

    def clear_cause_again() -> None:
        first_cause.cleared_at = timezone.now()
        first_cause.save(update_fields=["cleared_at"])

    assert_rejected(clear_cause_again, DatabaseError)


def test_staleness_cause_immutable_fields_and_clearing_pair_are_protected() -> None:
    canvas = make_canvas()
    other_canvas = make_canvas("Other")
    node, _, cause = make_stale_node(canvas)

    def mutate_origin() -> None:
        cause.origin_entity_id = uuid.uuid4()
        cause.save(update_fields=["origin_entity_id"])

    assert_rejected(mutate_origin, DatabaseError)

    def set_incomplete_clear_pair() -> None:
        cause.cleared_at = timezone.now()
        cause.cleared_by_graph_operation = None
        cause.save(update_fields=["cleared_at"])

    assert_rejected(set_incomplete_clear_pair, DatabaseError)

    foreign_clear_operation = make_operation(other_canvas)

    def clear_with_cross_canvas_operation() -> None:
        cause.cleared_at = timezone.now()
        cause.cleared_by_graph_operation = foreign_clear_operation
        cause.save(update_fields=["cleared_at", "cleared_by_graph_operation"])

    assert_rejected(clear_with_cross_canvas_operation)

    def delete_active_cause() -> None:
        NodeStalenessCause.objects.filter(pk=cause.pk).delete()

    assert_rejected(delete_active_cause, DatabaseError)
    assert Node.objects.filter(pk=node.pk).exists()


def test_deleting_a_stale_node_cascades_its_causes_but_preserves_audit_operations() -> None:
    canvas = make_canvas()
    node, operation, cause = make_stale_node(canvas)

    Node.objects.filter(pk=node.pk).delete()

    assert not NodeStalenessCause.objects.filter(pk=cause.pk).exists()
    assert GraphOperation.objects.filter(pk=operation.pk).exists()
