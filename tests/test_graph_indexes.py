import uuid

import pytest
from django.db import connection, transaction

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


def test_graph_schema_has_every_required_access_path_index() -> None:
    expected_indexes = {
        "node_canvas_kind_idx",
        "node_canvas_stale_idx",
        "node_branch_root_idx",
        "edge_canvas_source_kind_idx",
        "edge_canvas_target_kind_idx",
        "graph_op_canvas_revision_idx",
        "node_staleness_active_idx",
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename IN (
                  'node', 'edge', 'graph_operation', 'node_staleness_cause'
              )
            """
        )
        actual_indexes = {row[0] for row in cursor.fetchall()}

    assert expected_indexes <= actual_indexes


def test_bidirectional_edge_traversal_uses_indexes_at_representative_cardinality() -> None:
    canvas = Canvas.objects.create(title="Index plan canvas")
    nodes = [
        Node(
            id=uuid.uuid4(),
            canvas=canvas,
            kind=NodeKind.CLAIM,
            title=f"Claim {index}",
        )
        for index in range(4_000)
    ]
    Node.objects.bulk_create(nodes, batch_size=500)
    edges = [
        Edge(
            id=uuid.uuid4(),
            canvas=canvas,
            source=nodes[index],
            target=nodes[(index + 1) % len(nodes)],
            kind=EdgeKind.DERIVED_FROM,
        )
        for index in range(len(nodes))
    ]
    Edge.objects.bulk_create(edges, batch_size=500)

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE edge")
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM edge
            WHERE canvas_id = %s AND source_node_id = %s AND kind = %s
            """,
            [str(canvas.id), str(nodes[1_337].id), EdgeKind.DERIVED_FROM],
        )
        source_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM edge
            WHERE canvas_id = %s AND target_node_id = %s AND kind = %s
            """,
            [str(canvas.id), str(nodes[2_711].id), EdgeKind.DERIVED_FROM],
        )
        target_plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "edge_canvas_source_kind_idx" in source_plan
    assert "Seq Scan" not in source_plan
    assert "edge_canvas_target_kind_idx" in target_plan
    assert "Seq Scan" not in target_plan


def test_operation_replay_and_active_staleness_use_indexes_at_representative_cardinality() -> None:
    canvas = Canvas.objects.create(title="Audit and staleness plan canvas")
    row_count = 2_000

    with transaction.atomic():
        operations = GraphOperation.objects.bulk_create(
            [
                GraphOperation(
                    canvas=canvas,
                    actor_type="system",
                    operation_key=f"operation-{index}",
                    request_fingerprint=f"fingerprint-{index}",
                    operation_type="MARK_STALE",
                    payload={"index": index},
                    result_payload={"canvas_revision": index + 1},
                    canvas_revision=index + 1,
                )
                for index in range(row_count)
            ],
            batch_size=500,
        )
        nodes = Node.objects.bulk_create(
            [
                Node(
                    id=uuid.uuid4(),
                    canvas=canvas,
                    kind=NodeKind.CLAIM,
                    title=f"Stale claim {index}",
                    stale=True,
                    stale_since_revision=index + 1,
                )
                for index in range(row_count)
            ],
            batch_size=500,
        )
        NodeStalenessCause.objects.bulk_create(
            [
                NodeStalenessCause(
                    canvas=canvas,
                    node=nodes[index],
                    cause_graph_operation=operations[index],
                    origin_entity_type="node",
                    origin_entity_id=uuid.uuid4(),
                )
                for index in range(row_count)
            ],
            batch_size=500,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE graph_operation")
        cursor.execute("ANALYZE node_staleness_cause")
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id, canvas_revision
            FROM graph_operation
            WHERE canvas_id = %s AND canvas_revision > %s
            ORDER BY canvas_revision, id
            """,
            [str(canvas.id), row_count - 10],
        )
        operation_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM node_staleness_cause
            WHERE canvas_id = %s AND node_id = %s AND cleared_at IS NULL
            """,
            [str(canvas.id), str(nodes[1_337].id)],
        )
        staleness_plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "graph_op_canvas_revision_idx" in operation_plan
    assert "Seq Scan" not in operation_plan
    assert "node_staleness_active_idx" in staleness_plan
    assert "Seq Scan" not in staleness_plan
