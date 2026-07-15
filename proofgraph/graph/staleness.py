from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class InvalidationCapture:
    origin_entity_type: str
    origin_entity_id: uuid.UUID
    traversal_roots: tuple[uuid.UUID, ...] = ()
    include_roots: bool = False
    excluded_node_ids: tuple[uuid.UUID, ...] = ()
    precomputed_node_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class StalenessResult:
    stale_node_ids: tuple[uuid.UUID, ...]
    newly_stale_node_ids: tuple[uuid.UUID, ...]


def dependency_pair(
    source_node_id: uuid.UUID,
    target_node_id: uuid.UUID,
    kind: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    if kind in {EdgeKind.CONSTRAINED_BY, EdgeKind.EXTRACTED_FROM}:
        return target_node_id, source_node_id
    return source_node_id, target_node_id


def _parsed_uuid(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def reachable_dependents(
    canvas: Canvas,
    roots: set[uuid.UUID] | tuple[uuid.UUID, ...] | list[uuid.UUID],
    *,
    include_roots: bool,
    excluded_node_ids: set[uuid.UUID] | tuple[uuid.UUID, ...] = (),
) -> tuple[uuid.UUID, ...]:
    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for source_id, target_id, kind in Edge.objects.filter(canvas=canvas).values_list(
        "source_id",
        "target_id",
        "kind",
    ):
        ancestor_id, descendant_id = dependency_pair(source_id, target_id, kind)
        adjacency.setdefault(ancestor_id, set()).add(descendant_id)

    root_ids = set(roots)
    excluded = set(excluded_node_ids)
    visited = set(root_ids)
    affected = set(root_ids) if include_roots else set()
    queue = deque(sorted(root_ids, key=str))
    while queue:
        current = queue.popleft()
        for descendant_id in sorted(adjacency.get(current, ()), key=str):
            if descendant_id in visited:
                continue
            visited.add(descendant_id)
            affected.add(descendant_id)
            queue.append(descendant_id)
    affected.difference_update(excluded)
    return tuple(sorted(affected, key=str))


def capture_direct_invalidation(
    canvas: Canvas,
    payload: dict[str, Any],
) -> InvalidationCapture | None:
    operation = payload.get("op")
    if operation in {"UPDATE_NODE", "PATCH_NODE_METADATA"}:
        node_id = _parsed_uuid(payload.get("node_id"))
        if node_id is None:
            return None
        return InvalidationCapture(
            origin_entity_type="node",
            origin_entity_id=node_id,
            traversal_roots=(node_id,),
            excluded_node_ids=(node_id,),
        )

    if operation == "DELETE_NODE":
        node_id = _parsed_uuid(payload.get("node_id"))
        if node_id is None:
            return None
        affected = reachable_dependents(
            canvas,
            {node_id},
            include_roots=False,
            excluded_node_ids={node_id},
        )
        return InvalidationCapture(
            origin_entity_type="node",
            origin_entity_id=node_id,
            precomputed_node_ids=affected,
        )

    if operation in {"UPDATE_EDGE", "DELETE_EDGE"}:
        edge_id = _parsed_uuid(payload.get("edge_id"))
        if edge_id is None:
            return None
        edge = Edge.objects.filter(canvas=canvas, pk=edge_id).first()
        if edge is None:
            return None
        _ancestor_id, descendant_id = dependency_pair(
            edge.source_id,
            edge.target_id,
            edge.kind,
        )
        return InvalidationCapture(
            origin_entity_type="edge",
            origin_entity_id=edge.id,
            traversal_roots=(descendant_id,),
            include_roots=True,
        )

    if operation == "REPLACE_ASSUMPTION":
        assumption_id = _parsed_uuid(payload.get("node_id"))
        if assumption_id is None:
            return None
        owner_ids = tuple(
            Edge.objects.filter(
                canvas=canvas,
                target_id=assumption_id,
                kind=EdgeKind.DERIVED_FROM,
                source__kind=NodeKind.OPPORTUNITY,
            )
            .order_by("source_id")
            .values_list("source_id", flat=True)
        )
        return InvalidationCapture(
            origin_entity_type="node",
            origin_entity_id=assumption_id,
            traversal_roots=owner_ids,
            include_roots=True,
            excluded_node_ids=(assumption_id,),
        )
    if operation == "REJECT_EVIDENCE":
        evidence_id = _parsed_uuid(payload.get("node_id"))
        if evidence_id is None:
            return None
        evidence = Node.objects.filter(canvas=canvas, pk=evidence_id).first()
        if evidence is None or evidence.kind not in {NodeKind.SOURCE, NodeKind.CLAIM}:
            return None
        roots = (evidence_id,)
        excluded = (evidence_id,)
        if evidence.kind == NodeKind.SOURCE:
            claim_ids = tuple(
                Edge.objects.filter(
                    canvas=canvas,
                    kind=EdgeKind.EXTRACTED_FROM,
                    target_id=evidence_id,
                    source__kind=NodeKind.CLAIM,
                )
                .order_by("source_id")
                .values_list("source_id", flat=True)
                .distinct()
            )
            # Keep the source itself as a traversal root. Sources may support or
            # contradict opportunities directly, and those dependants must still
            # be invalidated even when no extracted claim exists. Claims whose
            # support was recomputed by the rejection transaction are traversal
            # waypoints, not stale outputs themselves.
            roots = (evidence_id, *claim_ids)
            excluded = (evidence_id, *claim_ids)
        return InvalidationCapture(
            origin_entity_type="node",
            origin_entity_id=evidence_id,
            traversal_roots=roots,
            excluded_node_ids=excluded,
        )
    return None


def resolve_direct_invalidation(
    canvas: Canvas,
    payload: dict[str, Any],
    capture: InvalidationCapture | None,
) -> tuple[uuid.UUID, ...]:
    if capture is None:
        return ()
    if capture.precomputed_node_ids:
        return capture.precomputed_node_ids

    roots = set(capture.traversal_roots)
    if payload.get("op") == "UPDATE_EDGE":
        edge_id = _parsed_uuid(payload.get("edge_id"))
        edge = Edge.objects.filter(canvas=canvas, pk=edge_id).first() if edge_id else None
        if edge is not None:
            _ancestor_id, descendant_id = dependency_pair(
                edge.source_id,
                edge.target_id,
                edge.kind,
            )
            roots.add(descendant_id)
    return reachable_dependents(
        canvas,
        roots,
        include_roots=capture.include_roots,
        excluded_node_ids=set(capture.excluded_node_ids),
    )


def apply_staleness(
    canvas: Canvas,
    *,
    node_ids: tuple[uuid.UUID, ...] | list[uuid.UUID] | set[uuid.UUID],
    graph_operation: GraphOperation,
    origin_entity_type: str,
    origin_entity_id: uuid.UUID,
    canvas_revision: int,
    now: Any | None = None,
) -> StalenessResult:
    now = now or timezone.now()
    nodes = list(
        Node.objects.select_for_update().filter(canvas=canvas, id__in=set(node_ids)).order_by("id")
    )
    eligible = [
        node
        for node in nodes
        if node.kind != NodeKind.GENERATION_PLACEHOLDER
        and node.metadata.get("review_status") != "rejected"
    ]
    NodeStalenessCause.objects.bulk_create(
        [
            NodeStalenessCause(
                canvas=canvas,
                node=node,
                cause_graph_operation=graph_operation,
                origin_entity_type=origin_entity_type,
                origin_entity_id=origin_entity_id,
                created_at=now,
            )
            for node in eligible
        ]
    )

    newly_stale = [node for node in eligible if not node.stale]
    for node in newly_stale:
        node.stale = True
        node.stale_since_revision = canvas_revision
        node.version += 1
        node.context_token_count = None
        node.context_content_hash = None
        node.semantic_updated_at = now
        node.updated_at = now
    if newly_stale:
        Node.objects.bulk_update(
            newly_stale,
            [
                "stale",
                "stale_since_revision",
                "version",
                "context_token_count",
                "context_content_hash",
                "semantic_updated_at",
                "updated_at",
            ],
        )
    return StalenessResult(
        stale_node_ids=tuple(node.id for node in eligible),
        newly_stale_node_ids=tuple(node.id for node in newly_stale),
    )
