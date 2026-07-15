import json
import uuid
from typing import Any

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db(transaction=True)


def post_operation(
    client: Client,
    canvas_id: str,
    csrf_token: str,
    op: str,
    **fields: Any,
) -> dict[str, Any]:
    response = client.post(
        f"/api/canvases/{canvas_id}/operations",
        data=json.dumps(
            {
                "op": op,
                "operation_key": str(uuid.uuid4()),
                **fields,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 200, response.content
    return response.json()


def test_canonical_goal_and_builder_constraints_survive_save_and_reload() -> None:
    client = Client(enforce_csrf_checks=True)
    health_response = client.get("/api/health")
    csrf_token = health_response.cookies["csrftoken"].value
    create_response = client.post(
        "/api/canvases",
        data=json.dumps({"title": "Solo Python opportunity map"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert create_response.status_code == 201
    canvas_id = create_response.json()["canvas"]["id"]

    goal_result = post_operation(
        client,
        canvas_id,
        csrf_token,
        "ADD_NODE",
        node={
            "kind": "goal",
            "title": "Find a recurring-revenue software opportunity",
            "body": "A credible opportunity for a solo Python developer.",
            "metadata": {
                "desired_outcome": "A testable B2B subscription opportunity",
                "target_user": "Technical founder",
            },
            "position": {"x": 72, "y": 72},
        },
    )
    goal_id = goal_result["node"]["id"]

    constraint_specs = [
        ("Skills", "Python, Django, backend systems"),
        ("Team size", "One"),
        ("Capital", "Low"),
        ("Time to MVP", "Eight weeks"),
        ("Preferred model", "B2B subscription"),
        ("Operational tolerance", "Low"),
        ("Sales preference", "Self-service"),
    ]
    constraint_ids: list[str] = []
    for index, (title, body) in enumerate(constraint_specs):
        result = post_operation(
            client,
            canvas_id,
            csrf_token,
            "ADD_NODE",
            node={
                "kind": "constraint",
                "title": title,
                "body": body,
                "metadata": {
                    "category": title.lower().replace(" ", "_"),
                    "context_scope": "global",
                    "pinned": True,
                },
                "position": {"x": 72, "y": 248 + index * 176},
            },
        )
        constraint_ids.append(result["node"]["id"])

    semantic_result = post_operation(
        client,
        canvas_id,
        csrf_token,
        "UPDATE_NODE",
        node_id=goal_id,
        expected_version=1,
        changes={
            "title": "Find a defensible recurring-revenue software opportunity",
            "body": "A source-backed opportunity a solo Python developer can ship.",
            "metadata": {"success_criteria": "A cheap falsifiable validation experiment"},
        },
    )
    assert semantic_result["node"]["version"] == 2
    assert semantic_result["node"]["position_version"] == 1

    move_result = post_operation(
        client,
        canvas_id,
        csrf_token,
        "MOVE_NODE",
        node_id=goal_id,
        expected_position_version=1,
        position={"x": 358, "y": 72},
    )
    assert move_result["node"]["version"] == 2
    assert move_result["node"]["position_version"] == 2

    edge_result = post_operation(
        client,
        canvas_id,
        csrf_token,
        "ADD_EDGE",
        edge={
            "source_node_id": goal_id,
            "target_node_id": constraint_ids[0],
            "kind": "constrained_by",
            "metadata": {"display_order": 1},
        },
    )
    updated_edge_result = post_operation(
        client,
        canvas_id,
        csrf_token,
        "UPDATE_EDGE",
        edge_id=edge_result["edge"]["id"],
        expected_version=1,
        changes={"metadata": {"display_order": 2}},
    )
    assert updated_edge_result["edge"]["version"] == 2
    assert updated_edge_result["newly_stale_node_ids"] == [goal_id]

    reload_response = client.get(f"/api/canvases/{canvas_id}")
    assert reload_response.status_code == 200
    reloaded = reload_response.json()["canvas"]
    assert reloaded["title"] == "Solo Python opportunity map"
    assert reloaded["revision"] == 12
    assert len(reloaded["nodes"]) == 8
    assert len(reloaded["edges"]) == 1

    nodes_by_id = {node["id"]: node for node in reloaded["nodes"]}
    goal = nodes_by_id[goal_id]
    assert goal == {
        **goal,
        "title": "Find a defensible recurring-revenue software opportunity",
        "body": "A source-backed opportunity a solo Python developer can ship.",
        "metadata": {
            "desired_outcome": "A testable B2B subscription opportunity",
            "target_user": "Technical founder",
            "success_criteria": "A cheap falsifiable validation experiment",
        },
        "position": {"x": 358, "y": 72},
        "stale": True,
        "stale_since_revision": 12,
        "version": 3,
        "position_version": 2,
    }
    for constraint_id in constraint_ids:
        constraint = nodes_by_id[constraint_id]
        assert constraint["metadata"]["context_scope"] == "global"
        assert constraint["metadata"]["pinned"] is True
        assert constraint["version"] == 1
        assert constraint["position_version"] == 1

    assert reloaded["edges"][0] == {
        **reloaded["edges"][0],
        "source_node_id": goal_id,
        "target_node_id": constraint_ids[0],
        "kind": "constrained_by",
        "metadata": {"display_order": 2},
        "version": 2,
    }

    replay_response = client.get(f"/api/canvases/{canvas_id}/operations?after=8")
    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert replay["canvas_revision"] == 12
    assert [operation["canvas_revision"] for operation in replay["operations"]] == [
        9,
        10,
        11,
        12,
    ]
    assert [operation["op"] for operation in replay["operations"]] == [
        "UPDATE_NODE",
        "MOVE_NODE",
        "ADD_EDGE",
        "UPDATE_EDGE",
    ]
