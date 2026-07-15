from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import ValidationError

from proofgraph.generation.context import canonical_json
from proofgraph.generation.models import (
    GraphPatch,
    GraphPatchOperationDecision,
    PatchDecision,
    PatchStatus,
)
from proofgraph.generation.patches import (
    DIRECT_ACTOR_TYPE,
    _patch_operations,
    serialize_graph_patch,
)
from proofgraph.generation.pipeline_schemas import PatchOperationCandidate
from proofgraph.generation.schemas import PatchApplyRequest
from proofgraph.generation.services import ServiceResult
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, Edge, EdgeKind, GraphOperation, Node, NodeKind
from proofgraph.graph.serialization import serialize_edge, serialize_node

PATCH_ACTOR_TYPE = "graph_patch"


@dataclass(frozen=True)
class AppliedCandidate:
    graph_operation: GraphOperation
    result: dict[str, Any]


def _conflict(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> GraphAPIError:
    return GraphAPIError(status=409, code=code, message=message, details=details)


def _validated_operations(patch: GraphPatch) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for index, operation in enumerate(_patch_operations(patch)):
        try:
            candidate = PatchOperationCandidate.model_validate_json(
                json.dumps(operation, ensure_ascii=False, allow_nan=False)
            )
        except ValidationError as error:
            raise GraphAPIError(
                status=500,
                code="invalid_patch_contract",
                message="A stored candidate operation is not schema-valid.",
                details={"operation_index": index},
            ) from error
        validated.append(candidate.model_dump(mode="json"))
    operation_ids = [operation["operation_id"] for operation in validated]
    if len(operation_ids) != len(set(operation_ids)):
        raise GraphAPIError(
            status=500,
            code="invalid_patch_contract",
            message="Stored candidate operation IDs are not unique.",
        )
    return validated


def _selection(
    operations: list[dict[str, Any]],
    request: PatchApplyRequest,
) -> tuple[set[str], list[dict[str, Any]]]:
    operation_by_id = {operation["operation_id"]: operation for operation in operations}
    selected_ids = (
        set(operation_by_id)
        if request.selected_operation_ids is None
        else set(request.selected_operation_ids)
    )
    unknown = sorted(selected_ids - operation_by_id.keys())
    if unknown:
        raise GraphAPIError(
            status=422,
            code="unknown_patch_operation",
            message="The patch selection contains unknown operation IDs.",
            details={"operation_ids": unknown},
        )
    missing_dependencies = {
        operation_id: sorted(set(operation_by_id[operation_id]["depends_on"]) - selected_ids)
        for operation_id in selected_ids
        if set(operation_by_id[operation_id]["depends_on"]) - selected_ids
    }
    if missing_dependencies:
        raise _conflict(
            "patch_dependency_incomplete",
            "Selected operations require additional prerequisite operations.",
            details={"missing_dependencies": missing_dependencies},
        )

    remaining = set(selected_ids)
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [
            operation
            for operation in operations
            if operation["operation_id"] in remaining
            and not (set(operation["depends_on"]) & remaining)
        ]
        if not ready:
            raise GraphAPIError(
                status=500,
                code="invalid_patch_contract",
                message="Stored candidate operation dependencies contain a cycle.",
            )
        ordered.extend(ready)
        remaining.difference_update(operation["operation_id"] for operation in ready)
    return selected_ids, ordered


def _parse_uuid(value: Any, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise _conflict(
            "invalid_patch_reference",
            f"{field} must be a UUID or accepted patch-local identity.",
            details={"field": field, "value": value},
        )
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise _conflict(
            "invalid_patch_reference",
            f"{field} must be a UUID or accepted patch-local identity.",
            details={"field": field, "value": value},
        ) from error


def _resolved_uuid(value: Any, field: str, client_id_map: dict[str, str]) -> uuid.UUID:
    if isinstance(value, str) and value in client_id_map:
        return uuid.UUID(client_id_map[value])
    return _parse_uuid(value, field)


def _resolve_local_references(value: Any, client_id_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return client_id_map.get(value, value)
    if isinstance(value, list):
        return [_resolve_local_references(item, client_id_map) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_local_references(item, client_id_map) for key, item in value.items()}
    return value


def _lock_touched_entities(
    canvas: Canvas,
    operations: list[dict[str, Any]],
    local_ids: set[str],
) -> None:
    node_ids: set[uuid.UUID] = set()
    edge_ids: set[uuid.UUID] = set()
    delete_node_ids: set[uuid.UUID] = set()
    for operation in operations:
        node_id = operation.get("node_id")
        if isinstance(node_id, str) and node_id not in local_ids:
            parsed = _parse_uuid(node_id, "node_id")
            node_ids.add(parsed)
            if operation["op"] == "DELETE_NODE":
                delete_node_ids.add(parsed)
        edge_id = operation.get("edge_id")
        if isinstance(edge_id, str) and edge_id not in local_ids:
            edge_ids.add(_parse_uuid(edge_id, "edge_id"))
        for field in ("required_branch_constraint_ids",):
            for value in operation.get(field, []):
                if value not in local_ids:
                    node_ids.add(_parse_uuid(value, field))
        for field in ("required_incident_edge_ids",):
            for value in operation.get(field, []):
                if value not in local_ids:
                    edge_ids.add(_parse_uuid(value, field))
        candidate_edge = operation.get("edge")
        if isinstance(candidate_edge, dict):
            for field in ("source_node_id", "target_node_id"):
                value = candidate_edge.get(field)
                if isinstance(value, str) and value not in local_ids:
                    node_ids.add(_parse_uuid(value, field))
        changes = operation.get("changes")
        if isinstance(changes, dict):
            for field in ("source_node_id", "target_node_id", "branch_root_node_id"):
                value = changes.get(field)
                if isinstance(value, str) and value not in local_ids:
                    node_ids.add(_parse_uuid(value, field))

    if delete_node_ids:
        node_ids.update(
            Node.objects.filter(canvas=canvas, branch_root_id__in=delete_node_ids).values_list(
                "id", flat=True
            )
        )
        edge_ids.update(
            Edge.objects.filter(canvas=canvas)
            .filter(Q(source_id__in=delete_node_ids) | Q(target_id__in=delete_node_ids))
            .values_list("id", flat=True)
        )
    list(Node.objects.select_for_update().filter(canvas=canvas, id__in=node_ids).order_by("id"))
    list(Edge.objects.select_for_update().filter(canvas=canvas, id__in=edge_ids).order_by("id"))


def _node(canvas: Canvas, node_id: uuid.UUID) -> Node:
    node = Node.objects.filter(canvas=canvas, pk=node_id).first()
    if node is None:
        raise _conflict(
            "node_not_found",
            "A node touched by the patch no longer exists.",
            details={"node_id": str(node_id)},
        )
    return node


def _edge(canvas: Canvas, edge_id: uuid.UUID) -> Edge:
    edge = Edge.objects.filter(canvas=canvas, pk=edge_id).first()
    if edge is None:
        raise _conflict(
            "edge_not_found",
            "An edge touched by the patch no longer exists.",
            details={"edge_id": str(edge_id)},
        )
    return edge


def _check_version(entity: Node | Edge, expected: Any, *, position: bool = False) -> None:
    current = entity.position_version if position and isinstance(entity, Node) else entity.version
    if expected != current:
        raise _conflict(
            "version_conflict",
            "An entity version no longer matches the candidate patch.",
            details={
                "entity_id": str(entity.id),
                "expected_version": expected,
                "current_version": current,
            },
        )


def _preflight_operation(
    canvas: Canvas,
    operation: dict[str, Any],
    client_id_map: dict[str, str],
    selected_operations: list[dict[str, Any]],
) -> None:
    op = operation["op"]
    if op == "ADD_NODE":
        node_id = _resolved_uuid(
            operation["client_generated_id"],
            "client_generated_id",
            client_id_map,
        )
        if Node.objects.filter(pk=node_id).exists():
            raise _conflict(
                "client_id_collision",
                "The deterministic node identity already exists.",
                details={"node_id": str(node_id)},
            )
        provenance = operation["node"].get("metadata", {}).get("provenance_node_ids", [])
        for value in provenance:
            if value not in client_id_map:
                _node(canvas, _parse_uuid(value, "provenance_node_ids"))
        return

    if op in {"UPDATE_NODE", "PATCH_NODE_METADATA", "MOVE_NODE", "DELETE_NODE"}:
        node = _node(
            canvas,
            _resolved_uuid(operation["node_id"], "node_id", client_id_map),
        )
        if op == "MOVE_NODE":
            _check_version(node, operation["expected_position_version"], position=True)
        else:
            _check_version(node, operation["expected_version"])
        if op != "DELETE_NODE":
            return

        delete_edge_ids = {
            candidate["edge_id"]
            for candidate in selected_operations
            if candidate["op"] == "DELETE_EDGE"
        }
        resolved_constraint_ids = {
            candidate["node_id"]
            for candidate in selected_operations
            if candidate["op"] == "DELETE_NODE"
            or (candidate["op"] == "UPDATE_NODE" and "branch_root_node_id" in candidate["changes"])
        }
        incident_edges = list(
            Edge.objects.filter(canvas=canvas)
            .filter(Q(source=node) | Q(target=node))
            .exclude(id__in=[_parse_uuid(value, "edge_id") for value in delete_edge_ids])
            .order_by("id")
            .values("id", "version")
        )
        branch_constraints = list(
            Node.objects.filter(canvas=canvas, branch_root=node)
            .exclude(id__in=[_parse_uuid(value, "node_id") for value in resolved_constraint_ids])
            .order_by("id")
            .values("id", "version")
        )
        if incident_edges or branch_constraints:
            raise _conflict(
                "node_has_dependencies",
                "The node has dependencies not resolved by the selected patch operations.",
                details={
                    "incident_edges": [
                        {"id": str(item["id"]), "version": item["version"]}
                        for item in incident_edges
                    ],
                    "referencing_constraints": [
                        {"id": str(item["id"]), "version": item["version"]}
                        for item in branch_constraints
                    ],
                },
            )
        return

    if op == "ADD_EDGE":
        edge_id = _resolved_uuid(
            operation["client_generated_id"],
            "client_generated_id",
            client_id_map,
        )
        if Edge.objects.filter(pk=edge_id).exists():
            raise _conflict(
                "client_id_collision",
                "The deterministic edge identity already exists.",
                details={"edge_id": str(edge_id)},
            )
        for field in ("source_node_id", "target_node_id"):
            value = operation["edge"][field]
            if value not in client_id_map:
                _node(canvas, _parse_uuid(value, field))
        return

    if op in {"UPDATE_EDGE", "DELETE_EDGE"}:
        edge = _edge(
            canvas,
            _resolved_uuid(operation["edge_id"], "edge_id", client_id_map),
        )
        _check_version(edge, operation["expected_version"])
        if op == "UPDATE_EDGE":
            for field in ("source_node_id", "target_node_id"):
                value = operation["changes"].get(field)
                if value is not None and value not in client_id_map:
                    _node(canvas, _parse_uuid(value, field))
        return

    raise GraphAPIError(
        status=500,
        code="invalid_patch_contract",
        message="The stored candidate operation type is unsupported.",
    )


def _validated_position(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise _conflict("invalid_position", "A candidate position must contain x and y.")
    return {"x": value["x"], "y": value["y"]}


def _branch_root(
    canvas: Canvas,
    node: Node,
    metadata: dict[str, Any],
    value: Any,
    client_id_map: dict[str, str],
) -> Node | None:
    if node.kind != NodeKind.CONSTRAINT:
        if value is not None:
            raise _conflict(
                "invalid_branch_root",
                "Only constraint nodes may have a branch root.",
            )
        return None
    scope = metadata.get("context_scope")
    if scope == "global":
        if value is not None:
            raise _conflict(
                "invalid_branch_root",
                "Global constraints cannot have a branch root.",
            )
        return None
    if scope != "branch" or value is None:
        raise _conflict(
            "invalid_branch_root",
            "Branch constraints require a valid branch root.",
        )
    root = _node(canvas, _resolved_uuid(value, "branch_root_node_id", client_id_map))
    if root.kind not in {NodeKind.STRATEGY, NodeKind.CLAIM, NodeKind.OPPORTUNITY}:
        raise _conflict(
            "invalid_branch_root",
            "A branch root must be a strategy, claim, or opportunity.",
        )
    return root


def _apply_entity_change(
    patch: GraphPatch,
    canvas: Canvas,
    operation: dict[str, Any],
    client_id_map: dict[str, str],
    now: Any,
) -> tuple[dict[str, Any], Node | Edge | None]:
    op = operation["op"]
    if op == "ADD_NODE":
        data = operation["node"]
        node_id = _resolved_uuid(
            operation["client_generated_id"], "client_generated_id", client_id_map
        )
        if Node.objects.filter(pk=node_id).exists():
            raise _conflict(
                "client_id_collision",
                "The deterministic node identity already exists.",
                details={"node_id": str(node_id)},
            )
        kind = data["kind"]
        if kind not in NodeKind.values:
            raise _conflict("invalid_node_kind", "The candidate node kind is invalid.")
        metadata = _resolve_local_references(data.get("metadata", {}), client_id_map)
        metadata = {
            **metadata,
            "review_status": "accepted",
            "source_patch_id": str(patch.id),
        }
        node = Node.objects.create(
            id=node_id,
            canvas=canvas,
            kind=kind,
            title=data["title"].strip(),
            body=data.get("body"),
            metadata=metadata,
            position=data.get("position") or {},
            created_at=now,
            semantic_updated_at=now,
            position_updated_at=now,
            updated_at=now,
        )
        return {"node": serialize_node(node)}, node

    if op in {"UPDATE_NODE", "PATCH_NODE_METADATA"}:
        node = _node(canvas, _resolved_uuid(operation["node_id"], "node_id", client_id_map))
        _check_version(node, operation["expected_version"])
        changes = (
            operation["changes"] if op == "UPDATE_NODE" else {"metadata": operation["metadata"]}
        )
        changes = _resolve_local_references(changes, client_id_map)
        if "title" in changes:
            node.title = changes["title"].strip()
        if "body" in changes:
            node.body = changes["body"]
        metadata = {**node.metadata, **changes.get("metadata", {})}
        node.metadata = metadata
        if "branch_root_node_id" in changes or node.kind == NodeKind.CONSTRAINT:
            root_value = changes.get(
                "branch_root_node_id",
                str(node.branch_root_id) if node.branch_root_id else None,
            )
            node.branch_root = _branch_root(
                canvas,
                node,
                metadata,
                root_value,
                client_id_map,
            )
        node.version += 1
        node.context_token_count = None
        node.context_content_hash = None
        node.semantic_updated_at = now
        node.updated_at = now
        node.save(
            update_fields=[
                "title",
                "body",
                "metadata",
                "branch_root",
                "version",
                "context_token_count",
                "context_content_hash",
                "semantic_updated_at",
                "updated_at",
            ]
        )
        return {"node": serialize_node(node)}, node

    if op == "MOVE_NODE":
        node = _node(canvas, _resolved_uuid(operation["node_id"], "node_id", client_id_map))
        _check_version(node, operation["expected_position_version"], position=True)
        node.position = _validated_position(operation["position"])
        node.position_version += 1
        node.position_updated_at = now
        node.updated_at = now
        node.save(
            update_fields=["position", "position_version", "position_updated_at", "updated_at"]
        )
        return {"node": serialize_node(node)}, node

    if op == "DELETE_NODE":
        node = _node(canvas, _resolved_uuid(operation["node_id"], "node_id", client_id_map))
        _check_version(node, operation["expected_version"])
        incident_edges = list(
            Edge.objects.filter(canvas=canvas)
            .filter(Q(source=node) | Q(target=node))
            .order_by("id")
            .values("id", "version")
        )
        branch_constraints = list(
            Node.objects.filter(canvas=canvas, branch_root=node)
            .order_by("id")
            .values("id", "version")
        )
        if incident_edges or branch_constraints:
            raise _conflict(
                "node_has_dependencies",
                "The node still has incident edges or branch constraints.",
                details={
                    "incident_edges": [
                        {"id": str(item["id"]), "version": item["version"]}
                        for item in incident_edges
                    ],
                    "referencing_constraints": [
                        {"id": str(item["id"]), "version": item["version"]}
                        for item in branch_constraints
                    ],
                },
            )
        node_id = str(node.id)
        node.delete()
        return {"deleted_node_id": node_id}, None

    if op == "ADD_EDGE":
        data = operation["edge"]
        edge_id = _resolved_uuid(
            operation["client_generated_id"], "client_generated_id", client_id_map
        )
        if Edge.objects.filter(pk=edge_id).exists():
            raise _conflict(
                "client_id_collision",
                "The deterministic edge identity already exists.",
                details={"edge_id": str(edge_id)},
            )
        source = _node(
            canvas,
            _resolved_uuid(data["source_node_id"], "source_node_id", client_id_map),
        )
        target = _node(
            canvas,
            _resolved_uuid(data["target_node_id"], "target_node_id", client_id_map),
        )
        if data["kind"] not in EdgeKind.values:
            raise _conflict("invalid_edge_kind", "The candidate edge kind is invalid.")
        metadata = _resolve_local_references(data.get("metadata", {}), client_id_map)
        edge = Edge.objects.create(
            id=edge_id,
            canvas=canvas,
            source=source,
            target=target,
            kind=data["kind"],
            metadata={**metadata, "source_patch_id": str(patch.id)},
            created_at=now,
            updated_at=now,
        )
        return {"edge": serialize_edge(edge)}, edge

    if op == "UPDATE_EDGE":
        edge = _edge(canvas, _resolved_uuid(operation["edge_id"], "edge_id", client_id_map))
        _check_version(edge, operation["expected_version"])
        changes = _resolve_local_references(operation["changes"], client_id_map)
        if "source_node_id" in changes:
            edge.source = _node(
                canvas,
                _resolved_uuid(changes["source_node_id"], "source_node_id", client_id_map),
            )
        if "target_node_id" in changes:
            edge.target = _node(
                canvas,
                _resolved_uuid(changes["target_node_id"], "target_node_id", client_id_map),
            )
        if "kind" in changes:
            if changes["kind"] not in EdgeKind.values:
                raise _conflict("invalid_edge_kind", "The candidate edge kind is invalid.")
            edge.kind = changes["kind"]
        if "metadata" in changes:
            edge.metadata = changes["metadata"]
        edge.version += 1
        edge.updated_at = now
        edge.save(update_fields=["source", "target", "kind", "metadata", "version", "updated_at"])
        return {"edge": serialize_edge(edge)}, edge

    if op == "DELETE_EDGE":
        edge = _edge(canvas, _resolved_uuid(operation["edge_id"], "edge_id", client_id_map))
        _check_version(edge, operation["expected_version"])
        edge_id = str(edge.id)
        edge.delete()
        return {"deleted_edge_id": edge_id}, None

    raise GraphAPIError(
        status=500,
        code="invalid_patch_contract",
        message="The stored candidate operation type is unsupported.",
    )


def _apply_candidate(
    patch: GraphPatch,
    canvas: Canvas,
    operation: dict[str, Any],
    client_id_map: dict[str, str],
) -> AppliedCandidate:
    operation_id = operation["operation_id"]
    operation_key = str(uuid.uuid5(patch.id, operation_id))
    audit_payload = {
        "op": operation["op"],
        "operation_key": operation_key,
        "patch_id": str(patch.id),
        "patch_operation_id": operation_id,
        "candidate": operation,
    }
    fingerprint = hashlib.sha256(canonical_json(audit_payload).encode()).hexdigest()
    existing = GraphOperation.objects.filter(
        canvas=canvas,
        actor_type=PATCH_ACTOR_TYPE,
        operation_key=operation_key,
    ).first()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise _conflict(
                "operation_key_conflict",
                "A deterministic patch operation key was reused for different content.",
            )
        return AppliedCandidate(existing, existing.result_payload)

    now = timezone.now()
    result, _entity = _apply_entity_change(patch, canvas, operation, client_id_map, now)
    new_revision = canvas.revision + 1
    result_payload = {"canvas_revision": new_revision, **result}
    graph_operation = GraphOperation.objects.create(
        canvas=canvas,
        actor_type=PATCH_ACTOR_TYPE,
        actor_id=str(patch.id),
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        operation_type=operation["op"],
        payload=audit_payload,
        result_payload=result_payload,
        canvas_revision=new_revision,
        created_at=now,
    )
    canvas.revision = new_revision
    canvas.updated_at = now
    canvas.save(update_fields=["revision", "updated_at"])
    return AppliedCandidate(graph_operation, result_payload)


def _retry_result(
    patch: GraphPatch,
    operations: list[dict[str, Any]],
    selected_ids: set[str],
    request: PatchApplyRequest,
) -> ServiceResult:
    operation_id_by_index = {
        index: operation["operation_id"] for index, operation in enumerate(operations)
    }
    decisions = list(patch.decisions.all().order_by("operation_index", "id"))
    previously_selected = {
        operation_id_by_index[decision.operation_index]
        for decision in decisions
        if decision.decision in {PatchDecision.ACCEPTED, PatchDecision.SKIPPED_CONFLICT}
    }
    skipped = [
        decision for decision in decisions if decision.decision == PatchDecision.SKIPPED_CONFLICT
    ]
    if selected_ids != previously_selected or (skipped and not request.apply_nonconflicting_only):
        raise _conflict(
            "patch_apply_request_conflict",
            "The graph patch was already reviewed with a different apply selection.",
            details={"status": patch.status},
        )
    accepted_revisions = [
        decision.graph_operation.canvas_revision
        for decision in decisions
        if decision.graph_operation_id is not None
    ]
    conflicts: list[dict[str, Any]] = []
    for decision in skipped:
        try:
            parsed_reason = json.loads(decision.reason or "{}")
        except json.JSONDecodeError:
            parsed_reason = {}
        if not isinstance(parsed_reason, dict):
            parsed_reason = {}
        parsed_reason.setdefault(
            "operation_id",
            operation_id_by_index[decision.operation_index],
        )
        conflicts.append(parsed_reason)
    return ServiceResult(
        {
            "patch": serialize_graph_patch(patch),
            "canvas_revision": (
                max(accepted_revisions)
                if accepted_revisions
                else Canvas.objects.get(pk=patch.canvas_id).revision
            ),
            "client_id_map": patch.client_id_map,
            "conflicts": conflicts,
        },
        200,
    )


def apply_graph_patch(patch_id: uuid.UUID, request: PatchApplyRequest) -> ServiceResult:
    started = time.perf_counter()
    accepted_count = 0
    rejected_count = 0
    skipped_count = 0
    conflicts: list[dict[str, Any]] = []
    with transaction.atomic():
        patch = GraphPatch.objects.select_for_update(of=("self",)).filter(pk=patch_id).first()
        if patch is None:
            raise GraphAPIError(
                status=404,
                code="graph_patch_not_found",
                message="Graph patch not found.",
            )
        operations = _validated_operations(patch)
        selected_ids, ordered = _selection(operations, request)
        if patch.status != PatchStatus.PENDING:
            result = _retry_result(patch, operations, selected_ids, request)
            decisions = list(patch.decisions.all())
            accepted_count = sum(
                decision.decision == PatchDecision.ACCEPTED for decision in decisions
            )
            rejected_count = sum(
                decision.decision == PatchDecision.REJECTED for decision in decisions
            )
            skipped_count = sum(
                decision.decision == PatchDecision.SKIPPED_CONFLICT for decision in decisions
            )
            conflicts = result.payload["conflicts"]
            replayed = True
        else:
            if patch.decisions.exists():
                raise _conflict(
                    "patch_already_decided",
                    "The pending graph patch already contains operation decisions.",
                )
            canvas = Canvas.objects.select_for_update().filter(pk=patch.canvas_id).first()
            if canvas is None:
                raise GraphAPIError(
                    status=404,
                    code="canvas_not_found",
                    message="Canvas not found.",
                )
            selected_operations = [
                operation for operation in operations if operation["operation_id"] in selected_ids
            ]
            local_ids = {
                operation["client_generated_id"]
                for operation in selected_operations
                if operation.get("client_generated_id") is not None
            }
            _lock_touched_entities(canvas, selected_operations, local_ids)
            client_id_map = {
                local_id: str(uuid.uuid5(patch.id, local_id)) for local_id in sorted(local_ids)
            }
            applied: dict[str, AppliedCandidate] = {}
            skipped: dict[str, dict[str, Any]] = {}
            for operation in ordered:
                operation_id = operation["operation_id"]
                blocked_by = sorted(
                    dependency for dependency in operation["depends_on"] if dependency in skipped
                )
                if blocked_by:
                    conflict = {
                        "operation_id": operation_id,
                        "code": "dependency_conflict",
                        "message": "A prerequisite operation could not be applied.",
                        "details": {"blocked_by": blocked_by},
                    }
                    skipped[operation_id] = conflict
                    conflicts.append(conflict)
                    continue
                try:
                    _preflight_operation(
                        canvas,
                        operation,
                        client_id_map,
                        selected_operations,
                    )
                except GraphAPIError as error:
                    conflict = {
                        "operation_id": operation_id,
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                    if not request.apply_nonconflicting_only:
                        emit_telemetry(
                            "patch.apply_conflict",
                            patch_id=patch.id,
                            run_id=patch.run_id,
                            canvas_id=patch.canvas_id,
                            **conflict,
                        )
                        raise _conflict(
                            "patch_apply_conflict",
                            "The graph patch conflicts with current canvas state.",
                            details={
                                "conflicts": [conflict],
                                "can_apply_nonconflicting": True,
                            },
                        ) from error
                    skipped[operation_id] = conflict
                    conflicts.append(conflict)

            for operation in ordered:
                operation_id = operation["operation_id"]
                if operation_id in skipped:
                    continue
                blocked_by = sorted(
                    dependency for dependency in operation["depends_on"] if dependency in skipped
                )
                if blocked_by:
                    conflict = {
                        "operation_id": operation_id,
                        "code": "dependency_conflict",
                        "message": "A prerequisite operation could not be applied.",
                        "details": {"blocked_by": blocked_by},
                    }
                    skipped[operation_id] = conflict
                    conflicts.append(conflict)
                    continue
                try:
                    applied[operation_id] = _apply_candidate(
                        patch,
                        canvas,
                        operation,
                        client_id_map,
                    )
                except GraphAPIError as error:
                    conflict = {
                        "operation_id": operation_id,
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                    if not request.apply_nonconflicting_only:
                        raise _conflict(
                            "patch_apply_conflict",
                            "The graph patch changed after preflight validation.",
                            details={"conflicts": [conflict]},
                        ) from error
                    skipped[operation_id] = conflict
                    conflicts.append(conflict)

            now = timezone.now()
            operation_index = {
                operation["operation_id"]: index for index, operation in enumerate(operations)
            }
            decisions: list[GraphPatchOperationDecision] = []
            for operation in operations:
                operation_id = operation["operation_id"]
                if operation_id in applied:
                    decisions.append(
                        GraphPatchOperationDecision(
                            patch=patch,
                            canvas=canvas,
                            operation_index=operation_index[operation_id],
                            decision=PatchDecision.ACCEPTED,
                            reason="user_accepted",
                            actor_type=DIRECT_ACTOR_TYPE,
                            graph_operation=applied[operation_id].graph_operation,
                            decided_at=now,
                        )
                    )
                elif operation_id in skipped:
                    decisions.append(
                        GraphPatchOperationDecision(
                            patch=patch,
                            canvas=canvas,
                            operation_index=operation_index[operation_id],
                            decision=PatchDecision.SKIPPED_CONFLICT,
                            reason=json.dumps(skipped[operation_id], sort_keys=True),
                            actor_type=DIRECT_ACTOR_TYPE,
                            decided_at=now,
                        )
                    )
                else:
                    decisions.append(
                        GraphPatchOperationDecision(
                            patch=patch,
                            canvas=canvas,
                            operation_index=operation_index[operation_id],
                            decision=PatchDecision.REJECTED,
                            reason="user_not_selected",
                            actor_type=DIRECT_ACTOR_TYPE,
                            decided_at=now,
                        )
                    )
            GraphPatchOperationDecision.objects.bulk_create(decisions)
            accepted_count = len(applied)
            skipped_count = len(skipped)
            rejected_count = len(operations) - accepted_count - skipped_count
            accepted_local_ids = {
                operation["client_generated_id"]
                for operation in operations
                if operation["operation_id"] in applied
                and operation.get("client_generated_id") is not None
            }
            patch.client_id_map = {
                local_id: client_id_map[local_id] for local_id in sorted(accepted_local_ids)
            }
            if accepted_count and accepted_count == len(operations):
                patch.status = PatchStatus.APPLIED
            elif accepted_count:
                patch.status = PatchStatus.PARTIALLY_APPLIED
            else:
                patch.status = PatchStatus.REJECTED
            patch.decided_at = now
            patch.applied_at = now if accepted_count else None
            patch.save(
                update_fields=[
                    "client_id_map",
                    "status",
                    "decided_at",
                    "applied_at",
                ]
            )
            result = ServiceResult(
                {
                    "patch": serialize_graph_patch(patch),
                    "canvas_revision": canvas.revision,
                    "client_id_map": patch.client_id_map,
                    "conflicts": conflicts,
                },
                200,
            )
            replayed = False

    duration_ms = round((time.perf_counter() - started) * 1_000, 3)
    emit_telemetry(
        "patch.apply_replayed" if replayed else "patch.applied",
        patch_id=patch.id,
        run_id=patch.run_id,
        canvas_id=patch.canvas_id,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        skipped_conflict_count=skipped_count,
        accepted_operation_ratio=(accepted_count / len(operations) if operations else 0.0),
        conflict_count=len(conflicts),
        duration_ms=duration_ms,
    )
    return result
