import json
import uuid
from typing import Any

import pytest
from django.test import Client

from proofgraph.graph.models import Canvas, Edge, GraphOperation, Node, NodeKind

pytestmark = pytest.mark.django_db(transaction=True)


def operation_payload(op: str, *, key: str | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "op": op,
        "operation_key": key or str(uuid.uuid4()),
        **fields,
    }


def post_operation(client: Client, canvas_id: uuid.UUID, payload: dict[str, Any]):
    return client.post(
        f"/api/canvases/{canvas_id}/operations",
        data=json.dumps(payload),
        content_type="application/json",
    )


def add_node(
    client: Client,
    canvas: Canvas,
    *,
    kind: str = NodeKind.GOAL,
    title: str = "Node",
    metadata: dict[str, Any] | None = None,
    branch_root_node_id: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"kind": kind, "title": title}
    if metadata is not None:
        node["metadata"] = metadata
    if branch_root_node_id is not None:
        node["branch_root_node_id"] = branch_root_node_id
    response = post_operation(client, canvas.id, operation_payload("ADD_NODE", node=node))
    assert response.status_code == 200
    return response.json()["node"]


def test_canvas_crud_exposes_current_graph_state_without_changing_graph_revision() -> None:
    client = Client()
    create_response = client.post(
        "/api/canvases",
        data=json.dumps({"title": "  Opportunity map  "}),
        content_type="application/json",
    )

    assert create_response.status_code == 201
    created = create_response.json()["canvas"]
    assert created["title"] == "Opportunity map"
    assert created["revision"] == 0
    assert created["nodes"] == []
    assert created["edges"] == []

    canvas_id = created["id"]
    patch_response = client.patch(
        f"/api/canvases/{canvas_id}",
        data=json.dumps({"title": "Validated map"}),
        content_type="application/json",
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["canvas"]["title"] == "Validated map"
    assert patch_response.json()["canvas"]["revision"] == 0

    get_response = client.get(f"/api/canvases/{canvas_id}")
    assert get_response.status_code == 200
    assert get_response.json()["canvas"]["title"] == "Validated map"

    invalid_response = client.patch(
        f"/api/canvases/{canvas_id}",
        data=json.dumps({"title": "Map", "revision": 99}),
        content_type="application/json",
    )
    assert invalid_response.status_code == 422
    assert invalid_response.json()["error"]["code"] == "unknown_field"


def test_add_node_is_idempotent_and_conflicting_key_reuse_is_rejected() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Idempotency")
    operation_key = str(uuid.uuid4())
    payload = operation_payload(
        "ADD_NODE",
        key=operation_key,
        node={
            "kind": NodeKind.OPPORTUNITY,
            "title": "Managed Django Fleet",
            "metadata": {"buyer": "Web development agencies"},
            "position": {"x": 10, "y": 20},
        },
    )

    first_response = post_operation(client, canvas.id, payload)
    retry_response = post_operation(client, canvas.id, payload)

    assert first_response.status_code == 200
    assert retry_response.status_code == 200
    assert retry_response.json() == first_response.json()
    assert Node.objects.filter(canvas=canvas).count() == 1
    assert GraphOperation.objects.filter(canvas=canvas).count() == 1
    canvas.refresh_from_db()
    assert canvas.revision == 1

    operation = GraphOperation.objects.get(canvas=canvas)
    assert operation.operation_key == operation_key
    assert len(operation.request_fingerprint) == 64
    assert operation.payload == payload
    assert operation.result_payload == first_response.json()

    conflicting_payload = {**payload, "node": {**payload["node"], "title": "Different"}}
    conflict_response = post_operation(client, canvas.id, conflicting_payload)
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "operation_key_conflict"
    assert Node.objects.filter(canvas=canvas).count() == 1
    assert GraphOperation.objects.filter(canvas=canvas).count() == 1


def test_source_writes_reject_overlong_durable_content_before_audit_persistence() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Source retention")

    rejected_add = post_operation(
        client,
        canvas.id,
        operation_payload(
            "ADD_NODE",
            node={
                "kind": NodeKind.SOURCE,
                "title": "Source",
                "body": "sentinel-source-document-" + ("x" * 500),
            },
        ),
    )

    assert rejected_add.status_code == 422
    assert rejected_add.json()["error"]["code"] == "retention_policy_violation"
    assert Node.objects.filter(canvas=canvas).count() == 0
    assert GraphOperation.objects.filter(canvas=canvas).count() == 0

    rejected_title = post_operation(
        client,
        canvas.id,
        operation_payload(
            "ADD_NODE",
            node={"kind": NodeKind.SOURCE, "title": "x" * 501},
        ),
    )
    assert rejected_title.status_code == 422
    assert rejected_title.json()["error"]["code"] == "retention_policy_violation"
    assert Node.objects.filter(canvas=canvas).count() == 0
    assert GraphOperation.objects.filter(canvas=canvas).count() == 0

    source = Node.objects.create(canvas=canvas, kind=NodeKind.SOURCE, title="Source")
    rejected_update = post_operation(
        client,
        canvas.id,
        operation_payload(
            "UPDATE_NODE",
            node_id=str(source.id),
            expected_version=source.version,
            changes={"metadata": {"notes": "x" * 501}},
        ),
    )

    assert rejected_update.status_code == 422
    assert rejected_update.json()["error"]["code"] == "retention_policy_violation"
    source.refresh_from_db()
    assert source.metadata == {}
    assert source.version == 1
    assert GraphOperation.objects.filter(canvas=canvas).count() == 0

    rejected_nested_metadata = post_operation(
        client,
        canvas.id,
        operation_payload(
            "UPDATE_NODE",
            node_id=str(source.id),
            expected_version=source.version,
            changes={"metadata": {"notes": {"text": "sentinel-source-document"}}},
        ),
    )
    assert rejected_nested_metadata.status_code == 422
    assert rejected_nested_metadata.json()["error"]["code"] == "retention_policy_violation"
    assert GraphOperation.objects.filter(canvas=canvas).count() == 0


def test_semantic_updates_metadata_patches_and_moves_keep_versions_isolated() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Version isolation")
    node_data = add_node(
        client,
        canvas,
        kind=NodeKind.OPPORTUNITY,
        title="Initial opportunity",
        metadata={"buyer": "Agencies"},
    )
    node = Node.objects.get(pk=node_data["id"])
    node.context_token_count = 111
    node.context_content_hash = "cached-hash"
    node.save(update_fields=["context_token_count", "context_content_hash"])
    original_position_timestamp = node.position_updated_at

    update_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "UPDATE_NODE",
            node_id=str(node.id),
            expected_version=1,
            changes={
                "title": "Updated opportunity",
                "metadata": {"business_model": "Subscription"},
            },
        ),
    )
    assert update_response.status_code == 200
    updated = update_response.json()["node"]
    assert updated["version"] == 2
    assert updated["position_version"] == 1
    assert updated["metadata"] == {
        "buyer": "Agencies",
        "business_model": "Subscription",
    }
    assert updated["context_token_count"] is None
    assert updated["context_content_hash"] is None

    node.refresh_from_db()
    semantic_timestamp = node.semantic_updated_at
    assert node.position_updated_at == original_position_timestamp
    node.context_token_count = 222
    node.context_content_hash = "post-semantic-cache"
    node.save(update_fields=["context_token_count", "context_content_hash"])

    move_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "MOVE_NODE",
            node_id=str(node.id),
            expected_position_version=1,
            position={"x": 812, "y": 405},
        ),
    )
    assert move_response.status_code == 200
    moved = move_response.json()["node"]
    assert moved["version"] == 2
    assert moved["position_version"] == 2
    assert moved["position"] == {"x": 812, "y": 405}
    assert moved["context_token_count"] == 222
    assert moved["context_content_hash"] == "post-semantic-cache"

    node.refresh_from_db()
    assert node.semantic_updated_at == semantic_timestamp

    patch_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "PATCH_NODE_METADATA",
            node_id=str(node.id),
            expected_version=2,
            metadata={"mechanism": "Automated operations"},
        ),
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()["node"]
    assert patched["version"] == 3
    assert patched["position_version"] == 2
    assert patched["metadata"]["mechanism"] == "Automated operations"


def test_edge_add_update_and_delete_use_edge_versions() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Edges")
    source = add_node(client, canvas, kind=NodeKind.SOURCE, title="Source")
    target = add_node(client, canvas, kind=NodeKind.CLAIM, title="Claim")

    add_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "ADD_EDGE",
            edge={
                "source_node_id": source["id"],
                "target_node_id": target["id"],
                "kind": "supports",
                "metadata": {"weight": 1},
            },
        ),
    )
    assert add_response.status_code == 200
    edge = add_response.json()["edge"]
    assert edge["version"] == 1

    stale_update = post_operation(
        client,
        canvas.id,
        operation_payload(
            "UPDATE_EDGE",
            edge_id=edge["id"],
            expected_version=2,
            changes={"kind": "contradicts"},
        ),
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["error"]["code"] == "version_conflict"

    update_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "UPDATE_EDGE",
            edge_id=edge["id"],
            expected_version=1,
            changes={"kind": "contradicts", "metadata": {"weight": 2}},
        ),
    )
    assert update_response.status_code == 200
    updated_edge = update_response.json()["edge"]
    assert updated_edge["kind"] == "contradicts"
    assert updated_edge["version"] == 2

    stale_delete = post_operation(
        client,
        canvas.id,
        operation_payload(
            "DELETE_EDGE",
            edge_id=edge["id"],
            expected_version=1,
        ),
    )
    assert stale_delete.status_code == 409

    delete_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "DELETE_EDGE",
            edge_id=edge["id"],
            expected_version=2,
        ),
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_edge_id"] == edge["id"]
    assert not Edge.objects.filter(pk=edge["id"]).exists()


def test_optimistic_conflicts_do_not_create_operations_or_advance_revision() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Conflicts")
    node = add_node(client, canvas)
    canvas.refresh_from_db()
    revision_before = canvas.revision
    operation_count_before = GraphOperation.objects.filter(canvas=canvas).count()

    semantic_conflict = post_operation(
        client,
        canvas.id,
        operation_payload(
            "UPDATE_NODE",
            node_id=node["id"],
            expected_version=99,
            changes={"title": "Stale edit"},
        ),
    )
    position_conflict = post_operation(
        client,
        canvas.id,
        operation_payload(
            "MOVE_NODE",
            node_id=node["id"],
            expected_position_version=99,
            position={"x": 1, "y": 2},
        ),
    )
    delete_conflict = post_operation(
        client,
        canvas.id,
        operation_payload(
            "DELETE_NODE",
            node_id=node["id"],
            expected_version=99,
        ),
    )

    assert semantic_conflict.status_code == 409
    assert position_conflict.status_code == 409
    assert delete_conflict.status_code == 409
    canvas.refresh_from_db()
    assert canvas.revision == revision_before
    assert GraphOperation.objects.filter(canvas=canvas).count() == operation_count_before


def test_operation_replay_is_revision_ordered_and_incremental() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Replay")
    first = add_node(client, canvas, title="First")
    add_node(client, canvas, title="Second")
    move_response = post_operation(
        client,
        canvas.id,
        operation_payload(
            "MOVE_NODE",
            node_id=first["id"],
            expected_position_version=1,
            position={"x": 3, "y": 4},
        ),
    )
    assert move_response.status_code == 200

    all_response = client.get(f"/api/canvases/{canvas.id}/operations?after=0")
    later_response = client.get(f"/api/canvases/{canvas.id}/operations?after=1")

    assert all_response.status_code == 200
    assert all_response.json()["canvas_revision"] == 3
    assert [item["canvas_revision"] for item in all_response.json()["operations"]] == [1, 2, 3]
    assert [item["canvas_revision"] for item in later_response.json()["operations"]] == [2, 3]
    assert [item["op"] for item in all_response.json()["operations"]] == [
        "ADD_NODE",
        "ADD_NODE",
        "MOVE_NODE",
    ]

    invalid_after = client.get(f"/api/canvases/{canvas.id}/operations?after=-1")
    assert invalid_after.status_code == 422
    assert invalid_after.json()["error"]["code"] == "invalid_revision"


def test_operation_envelope_and_taxonomy_validation() -> None:
    client = Client()
    canvas = Canvas.objects.create(title="Validation")

    invalid_json = client.post(
        f"/api/canvases/{canvas.id}/operations",
        data="{not-json",
        content_type="application/json",
    )
    invalid_key = post_operation(
        client,
        canvas.id,
        operation_payload("ADD_NODE", key="not-a-uuid", node={"kind": "goal", "title": "X"}),
    )
    unsupported = post_operation(
        client,
        canvas.id,
        operation_payload("REPLACE_GRAPH"),
    )
    invalid_kind = post_operation(
        client,
        canvas.id,
        operation_payload("ADD_NODE", node={"kind": "custom", "title": "X"}),
    )
    server_owned = post_operation(
        client,
        canvas.id,
        operation_payload(
            "ADD_NODE",
            node={"kind": "goal", "title": "X", "stale": True},
        ),
    )
    invalid_constraint = post_operation(
        client,
        canvas.id,
        operation_payload(
            "ADD_NODE",
            node={
                "kind": "constraint",
                "title": "Missing pinned",
                "metadata": {"context_scope": "global"},
            },
        ),
    )
    other_canvas = Canvas.objects.create(title="Other")
    source = Node.objects.create(canvas=canvas, kind=NodeKind.SOURCE, title="Source")
    foreign_target = Node.objects.create(
        canvas=other_canvas,
        kind=NodeKind.CLAIM,
        title="Foreign claim",
    )
    cross_canvas_edge = post_operation(
        client,
        canvas.id,
        operation_payload(
            "ADD_EDGE",
            edge={
                "source_node_id": str(source.id),
                "target_node_id": str(foreign_target.id),
                "kind": "supports",
            },
        ),
    )

    assert invalid_json.status_code == 400
    assert invalid_key.status_code == 422
    assert unsupported.status_code == 422
    assert invalid_kind.status_code == 422
    assert server_owned.status_code == 403
    assert invalid_constraint.status_code == 422
    assert cross_canvas_edge.status_code == 422
    assert GraphOperation.objects.filter(canvas=canvas).count() == 0
