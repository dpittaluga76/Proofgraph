from proofgraph.graph.models import Canvas, Edge, GraphOperation, Node


def serialize_node(node: Node) -> dict[str, object]:
    return {
        "id": str(node.id),
        "canvas_id": str(node.canvas_id),
        "kind": node.kind,
        "title": node.title,
        "body": node.body,
        "metadata": node.metadata,
        "branch_root_node_id": str(node.branch_root_id) if node.branch_root_id else None,
        "position": node.position,
        "stale": node.stale,
        "stale_since_revision": node.stale_since_revision,
        "version": node.version,
        "position_version": node.position_version,
        "context_token_count": node.context_token_count,
        "context_representation_version": node.context_representation_version,
        "context_content_hash": node.context_content_hash,
        "created_at": node.created_at.isoformat(),
        "semantic_updated_at": node.semantic_updated_at.isoformat(),
        "position_updated_at": node.position_updated_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
    }


def serialize_edge(edge: Edge) -> dict[str, object]:
    return {
        "id": str(edge.id),
        "canvas_id": str(edge.canvas_id),
        "source_node_id": str(edge.source_id),
        "target_node_id": str(edge.target_id),
        "kind": edge.kind,
        "metadata": edge.metadata,
        "version": edge.version,
        "created_at": edge.created_at.isoformat(),
        "updated_at": edge.updated_at.isoformat(),
    }


def serialize_canvas(canvas: Canvas, *, include_graph: bool = True) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(canvas.id),
        "title": canvas.title,
        "revision": canvas.revision,
        "created_at": canvas.created_at.isoformat(),
        "updated_at": canvas.updated_at.isoformat(),
    }
    if include_graph:
        result["nodes"] = [serialize_node(node) for node in canvas.nodes.order_by("id")]
        result["edges"] = [serialize_edge(edge) for edge in canvas.edges.order_by("id")]
    return result


def serialize_graph_operation(operation: GraphOperation) -> dict[str, object]:
    return {
        "id": operation.id,
        "canvas_id": str(operation.canvas_id),
        "actor_type": operation.actor_type,
        "actor_id": operation.actor_id,
        "operation_key": operation.operation_key,
        "op": operation.operation_type,
        "payload": operation.payload,
        "result": operation.result_payload,
        "canvas_revision": operation.canvas_revision,
        "created_at": operation.created_at.isoformat(),
    }
