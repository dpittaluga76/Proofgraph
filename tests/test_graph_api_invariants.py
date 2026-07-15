import json
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from proofgraph.graph.models import (
    Canvas,
    Edge,
    GraphOperation,
    Node,
    NodeKind,
    NodeStalenessCause,
)

pytestmark = pytest.mark.django_db(transaction=True)


def payload(op: str, **fields: Any) -> dict[str, Any]:
    return {"op": op, "operation_key": str(uuid.uuid4()), **fields}


def post_operation(client: Client, canvas: Canvas, body: dict[str, Any]):
    return client.post(
        f"/api/canvases/{canvas.id}/operations",
        data=json.dumps(body),
        content_type="application/json",
    )


def api_add_node(
    client: Client,
    canvas: Canvas,
    *,
    kind: str,
    title: str,
    metadata: dict[str, Any] | None = None,
    branch_root_node_id: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"kind": kind, "title": title}
    if metadata is not None:
        node["metadata"] = metadata
    if branch_root_node_id is not None:
        node["branch_root_node_id"] = branch_root_node_id
    response = post_operation(client, canvas, payload("ADD_NODE", node=node))
    assert response.status_code == 200
    return response.json()["node"]


@pytest.mark.parametrize("operation", ["UPDATE_NODE", "PATCH_NODE_METADATA"])
def test_update_paths_share_metadata_ownership_rules(operation: str) -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Ownership")
    goal = api_add_node(client, canvas, kind=NodeKind.GOAL, title="Goal")
    revision_before = Canvas.objects.get(pk=canvas.pk).revision
    count_before = GraphOperation.objects.filter(canvas=canvas).count()

    def request_for(metadata: dict[str, Any]) -> dict[str, Any]:
        if operation == "UPDATE_NODE":
            return payload(
                operation,
                node_id=goal["id"],
                expected_version=1,
                changes={"metadata": metadata},
            )
        return payload(
            operation,
            node_id=goal["id"],
            expected_version=1,
            metadata=metadata,
        )

    server_owned = post_operation(client, canvas, request_for({"review_status": "accepted"}))
    wrong_kind = post_operation(client, canvas, request_for({"buyer": "Agencies"}))

    assert server_owned.status_code == 403
    assert server_owned.json()["error"]["code"] == "server_owned_field"
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["error"]["code"] == "wrong_kind_field"
    canvas.refresh_from_db()
    assert canvas.revision == revision_before
    assert GraphOperation.objects.filter(canvas=canvas).count() == count_before


def test_update_node_cannot_smuggle_server_state_or_position_fields() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="No smuggling")
    node = api_add_node(client, canvas, kind=NodeKind.GOAL, title="Goal")

    server_owned = post_operation(
        client,
        canvas,
        payload(
            "UPDATE_NODE",
            node_id=node["id"],
            expected_version=1,
            changes={"stale": True},
        ),
    )
    wrong_operation = post_operation(
        client,
        canvas,
        payload(
            "UPDATE_NODE",
            node_id=node["id"],
            expected_version=1,
            changes={"position": {"x": 1, "y": 2}},
        ),
    )
    wrong_kind = post_operation(
        client,
        canvas,
        payload(
            "UPDATE_NODE",
            node_id=node["id"],
            expected_version=1,
            changes={"branch_root_node_id": None},
        ),
    )

    assert server_owned.status_code == 403
    assert wrong_operation.status_code == 422
    assert wrong_kind.status_code == 422


def test_constraint_pin_scope_and_reanchor_workflow_is_audited() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Constraints")
    other_canvas = Canvas.objects.create(title="Other")
    first_root = api_add_node(client, canvas, kind=NodeKind.STRATEGY, title="Strategy")
    second_root = api_add_node(client, canvas, kind=NodeKind.CLAIM, title="Claim")
    wrong_kind_root = api_add_node(client, canvas, kind=NodeKind.GOAL, title="Goal")
    other_root = api_add_node(
        client,
        other_canvas,
        kind=NodeKind.STRATEGY,
        title="Other strategy",
    )
    constraint = api_add_node(
        client,
        canvas,
        kind=NodeKind.CONSTRAINT,
        title="Builder time",
        metadata={"context_scope": "global", "pinned": True},
    )

    pin_response = post_operation(
        client,
        canvas,
        payload(
            "PATCH_NODE_METADATA",
            node_id=constraint["id"],
            expected_version=1,
            metadata={"pinned": False},
        ),
    )
    assert pin_response.status_code == 200
    assert pin_response.json()["node"]["version"] == 2

    branch_response = post_operation(
        client,
        canvas,
        payload(
            "UPDATE_NODE",
            node_id=constraint["id"],
            expected_version=2,
            changes={
                "metadata": {"context_scope": "branch"},
                "branch_root_node_id": first_root["id"],
            },
        ),
    )
    assert branch_response.status_code == 200
    assert branch_response.json()["node"]["branch_root_node_id"] == first_root["id"]

    reanchor_response = post_operation(
        client,
        canvas,
        payload(
            "PATCH_NODE_METADATA",
            node_id=constraint["id"],
            expected_version=3,
            metadata={},
            branch_root_node_id=second_root["id"],
        ),
    )
    assert reanchor_response.status_code == 200
    assert reanchor_response.json()["node"]["branch_root_node_id"] == second_root["id"]

    global_response = post_operation(
        client,
        canvas,
        payload(
            "PATCH_NODE_METADATA",
            node_id=constraint["id"],
            expected_version=4,
            metadata={"context_scope": "global"},
        ),
    )
    assert global_response.status_code == 200
    assert global_response.json()["node"]["branch_root_node_id"] is None

    for root_id in (wrong_kind_root["id"], other_root["id"]):
        invalid_response = post_operation(
            client,
            canvas,
            payload(
                "UPDATE_NODE",
                node_id=constraint["id"],
                expected_version=5,
                changes={
                    "metadata": {"context_scope": "branch"},
                    "branch_root_node_id": root_id,
                },
            ),
        )
        assert invalid_response.status_code == 422
        assert invalid_response.json()["error"]["code"] == "invalid_branch_root"

    constraint_row = Node.objects.get(pk=constraint["id"])
    assert constraint_row.version == 5
    assert constraint_row.metadata == {"context_scope": "global", "pinned": False}
    assert constraint_row.branch_root_id is None
    assert (
        GraphOperation.objects.filter(canvas=canvas, operation_type="PATCH_NODE_METADATA").count()
        == 3
    )


def test_delete_node_reports_edges_and_branch_constraints_until_resolved() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Explicit dependencies")
    root = Node.objects.create(canvas=canvas, kind=NodeKind.STRATEGY, title="Root")
    other = Node.objects.create(canvas=canvas, kind=NodeKind.CLAIM, title="Other")
    edge = Edge.objects.create(
        canvas=canvas,
        source=root,
        target=other,
        kind="derived_from",
    )
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Branch constraint",
        metadata={"context_scope": "branch", "pinned": True},
        branch_root=root,
    )

    conflict = post_operation(
        client,
        canvas,
        payload(
            "DELETE_NODE",
            node_id=str(root.id),
            expected_version=1,
        ),
    )
    assert conflict.status_code == 409
    details = conflict.json()["error"]["details"]
    assert details["incident_edges"] == [{"id": str(edge.id), "version": 1}]
    assert details["referencing_constraints"] == [{"id": str(constraint.id), "version": 1}]
    assert GraphOperation.objects.filter(canvas=canvas).count() == 0

    delete_edge_response = post_operation(
        client,
        canvas,
        payload(
            "DELETE_EDGE",
            edge_id=str(edge.id),
            expected_version=1,
        ),
    )
    assert delete_edge_response.status_code == 200
    rescope_response = post_operation(
        client,
        canvas,
        payload(
            "PATCH_NODE_METADATA",
            node_id=str(constraint.id),
            expected_version=1,
            metadata={"context_scope": "global"},
        ),
    )
    assert rescope_response.status_code == 200
    delete_node_response = post_operation(
        client,
        canvas,
        payload(
            "DELETE_NODE",
            node_id=str(root.id),
            expected_version=1,
        ),
    )
    assert delete_node_response.status_code == 200
    assert not Node.objects.filter(pk=root.id).exists()
    constraint.refresh_from_db()
    assert constraint.branch_root_id is None
    assert [
        operation.operation_type
        for operation in GraphOperation.objects.filter(canvas=canvas).order_by("canvas_revision")
    ] == ["DELETE_EDGE", "PATCH_NODE_METADATA", "DELETE_NODE"]


def test_failed_audit_append_rolls_back_entity_and_canvas_changes() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Rollback")

    with (
        patch.object(GraphOperation.objects, "create", side_effect=RuntimeError("audit failed")),
        pytest.raises(RuntimeError, match="audit failed"),
    ):
        post_operation(
            client,
            canvas,
            payload(
                "ADD_NODE",
                node={"kind": NodeKind.GOAL, "title": "Must roll back"},
            ),
        )

    canvas.refresh_from_db()
    assert canvas.revision == 0
    assert not Node.objects.filter(canvas=canvas).exists()
    assert not GraphOperation.objects.filter(canvas=canvas).exists()


def test_mutation_query_order_locks_canvas_before_entity_rows() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Lock order")
    node = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Goal")

    with CaptureQueriesContext(connection) as queries:
        response = post_operation(
            client,
            canvas,
            payload(
                "UPDATE_NODE",
                node_id=str(node.id),
                expected_version=1,
                changes={"title": "Updated"},
            ),
        )

    assert response.status_code == 200
    lock_queries = [item["sql"] for item in queries.captured_queries if "FOR UPDATE" in item["sql"]]
    assert len(lock_queries) >= 2
    assert 'FROM "canvas"' in lock_queries[0]
    assert 'FROM "node"' in lock_queries[1]


@pytest.mark.parametrize(
    ("path_suffix", "graph_table"),
    [("", 'FROM "node"'), ("/operations?after=0", 'FROM "graph_operation"')],
)
def test_graph_reads_lock_canvas_through_snapshot_queries(
    path_suffix: str,
    graph_table: str,
) -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Read snapshot")
    api_add_node(client, canvas, kind=NodeKind.GOAL, title="Goal")

    with CaptureQueriesContext(connection) as queries:
        response = client.get(f"/api/canvases/{canvas.id}{path_suffix}")

    assert response.status_code == 200
    statements = [item["sql"] for item in queries.captured_queries]
    canvas_lock_index = next(
        index
        for index, statement in enumerate(statements)
        if 'FROM "canvas"' in statement and "FOR UPDATE" in statement
    )
    graph_read_index = next(
        index for index, statement in enumerate(statements) if graph_table in statement
    )
    commit_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.strip().upper() in {"COMMIT", "RELEASE SAVEPOINT"}
    )
    assert canvas_lock_index < graph_read_index < commit_index


def test_canvas_delete_removes_every_phase_one_record_and_only_that_canvas() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Delete me", revision=1)
    other_canvas = Canvas.objects.create(title="Keep me")
    root = Node.objects.create(canvas=canvas, kind=NodeKind.STRATEGY, title="Root")
    other = Node.objects.create(canvas=canvas, kind=NodeKind.CLAIM, title="Stale")
    Edge.objects.create(canvas=canvas, source=root, target=other, kind="derived_from")
    operation = GraphOperation.objects.create(
        canvas=canvas,
        actor_type="system",
        operation_key=str(uuid.uuid4()),
        request_fingerprint="fingerprint",
        operation_type="MARK_STALE",
        payload={},
        result_payload={},
        canvas_revision=1,
    )
    with transaction.atomic():
        other.stale = True
        other.stale_since_revision = 1
        other.save(update_fields=["stale", "stale_since_revision"])
        cause = NodeStalenessCause.objects.create(
            canvas=canvas,
            node=other,
            cause_graph_operation=operation,
            origin_entity_type="node",
            origin_entity_id=root.id,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    Node.objects.create(canvas=other_canvas, kind=NodeKind.GOAL, title="Keep")

    response = client.delete(f"/api/canvases/{canvas.id}")

    assert response.status_code == 204
    assert not Canvas.objects.filter(pk=canvas.id).exists()
    assert not Node.objects.filter(canvas_id=canvas.id).exists()
    assert not Edge.objects.filter(canvas_id=canvas.id).exists()
    assert not GraphOperation.objects.filter(canvas_id=canvas.id).exists()
    assert not NodeStalenessCause.objects.filter(pk=cause.pk).exists()
    assert Canvas.objects.filter(pk=other_canvas.id).exists()
    assert Node.objects.filter(canvas=other_canvas).count() == 1
