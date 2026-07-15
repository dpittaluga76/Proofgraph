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
from proofgraph.graph.models import (
    Canvas,
    Edge,
    EdgeKind,
    GraphOperation,
    Node,
    NodeKind,
    NodeStalenessCause,
)
from proofgraph.graph.serialization import serialize_edge, serialize_node
from proofgraph.graph.staleness import (
    apply_staleness,
    capture_direct_invalidation,
    resolve_direct_invalidation,
)

PATCH_ACTOR_TYPE = "graph_patch"


@dataclass(frozen=True)
class AppliedCandidate:
    graph_operation: GraphOperation
    result: dict[str, Any]


@dataclass(frozen=True)
class RegenerationApplicationContract:
    scope: str
    target_ids: tuple[uuid.UUID, ...]
    permitted_stale_ids: tuple[uuid.UUID, ...]
    successors: tuple[tuple[uuid.UUID, str, str], ...]
    operation_groups: tuple[tuple[str, tuple[str, ...]], ...]
    preserved_nodes: tuple[tuple[uuid.UUID, int, int | None], ...]
    preserved_causes: tuple[tuple[int, int | None, Any | None], ...]


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


def _regeneration_conflict(
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> GraphAPIError:
    return _conflict("patch_regeneration_contract_invalid", message, details=details)


def _manifest_regeneration_contract(
    patch: GraphPatch,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    manifest = patch.run.context_manifest
    regeneration = manifest.get("regeneration") if isinstance(manifest, dict) else None
    request = manifest.get("request") if isinstance(manifest, dict) else None
    if not isinstance(regeneration, dict) or not isinstance(request, dict):
        raise _regeneration_conflict("The regeneration run has no frozen workset contract.")
    scope = regeneration.get("scope")
    if scope not in {"node", "branch"} or request.get("regeneration_scope") != scope:
        raise _regeneration_conflict("The regeneration scope does not match the frozen request.")
    targets = regeneration.get("targets")
    if not isinstance(targets, list) or not targets:
        raise _regeneration_conflict("The regeneration run has no frozen production targets.")
    target_ids: list[str] = []
    permitted_ids: set[str] = set()
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("node_id"), str):
            raise _regeneration_conflict("A frozen regeneration target is malformed.")
        target_id = target["node_id"]
        target_ids.append(target_id)
        stale_ids = target.get("stale_node_ids") or [target_id]
        if not isinstance(stale_ids, list) or not all(
            isinstance(value, str) for value in stale_ids
        ):
            raise _regeneration_conflict("A frozen regeneration stale-member set is malformed.")
        permitted_ids.update(stale_ids)
    if len(target_ids) != len(set(target_ids)):
        raise _regeneration_conflict("Frozen regeneration targets are not unique.")
    return scope, tuple(sorted(target_ids)), tuple(sorted(permitted_ids))


_REGENERATION_ROOT_KINDS = {NodeKind.STRATEGY, NodeKind.CLAIM, NodeKind.OPPORTUNITY}
_CONSTRAINT_CLONE_SYSTEM_METADATA = {
    "generated_by_run_id",
    "provenance_node_ids",
    "review_status",
    "source_patch_id",
}


def _constraint_semantic_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in _CONSTRAINT_CLONE_SYSTEM_METADATA
    }


def _validate_regeneration_application(
    patch: GraphPatch,
    canvas: Canvas,
    operations: list[dict[str, Any]],
    selected_ids: set[str],
) -> RegenerationApplicationContract | None:
    if patch.run.operation != "regenerate_stale":
        if patch.regeneration_target_ids or patch.permitted_stale_resolution_ids:
            raise _regeneration_conflict(
                "A non-regeneration patch may not declare stale regeneration targets."
            )
        return None

    scope, frozen_targets, frozen_permitted = _manifest_regeneration_contract(patch)
    declared_targets = tuple(sorted(str(value) for value in patch.regeneration_target_ids))
    declared_permitted = tuple(sorted(str(value) for value in patch.permitted_stale_resolution_ids))
    if declared_targets != frozen_targets or declared_permitted != frozen_permitted:
        raise _regeneration_conflict(
            "The patch declarations do not match the frozen regeneration workset.",
            details={
                "expected_target_ids": list(frozen_targets),
                "declared_target_ids": list(declared_targets),
                "expected_permitted_stale_ids": list(frozen_permitted),
                "declared_permitted_stale_ids": list(declared_permitted),
            },
        )
    if any(operation["op"] not in {"ADD_NODE", "ADD_EDGE"} for operation in operations):
        raise _regeneration_conflict(
            "Always-parallel regeneration patches may only add successor nodes and edges."
        )

    target_ids = tuple(_parse_uuid(value, "regeneration_target_ids") for value in frozen_targets)
    permitted_ids = tuple(
        _parse_uuid(value, "permitted_stale_resolution_ids") for value in frozen_permitted
    )
    preserved_node_ids = set(target_ids) | set(permitted_ids)
    locked_nodes = list(
        Node.objects.select_for_update()
        .filter(canvas=canvas, id__in=preserved_node_ids)
        .order_by("id")
    )
    nodes_by_id = {node.id: node for node in locked_nodes}
    missing_ids = sorted(str(node_id) for node_id in preserved_node_ids - nodes_by_id.keys())
    active_cause_node_ids = set(
        NodeStalenessCause.objects.filter(
            canvas=canvas,
            node_id__in=set(permitted_ids),
            cleared_at__isnull=True,
        ).values_list("node_id", flat=True)
    )
    non_stale_ids = sorted(
        str(node_id)
        for node_id in permitted_ids
        if node_id in nodes_by_id
        and (not nodes_by_id[node_id].stale or node_id not in active_cause_node_ids)
    )
    if missing_ids or non_stale_ids:
        raise _regeneration_conflict(
            "Every frozen regeneration member must still exist with an active stale cause.",
            details={"missing_node_ids": missing_ids, "non_stale_node_ids": non_stale_ids},
        )
    operation_by_id = {operation["operation_id"]: operation for operation in operations}
    successor_by_target: dict[uuid.UUID, tuple[dict[str, Any], str]] = {}
    for operation in operations:
        if operation["op"] != "ADD_NODE":
            continue
        node = operation["node"]
        metadata = node.get("metadata", {})
        if "regenerates_node_id" in metadata:
            raise _regeneration_conflict(
                "Regeneration patches may not use the obsolete regenerates_node_id metadata."
            )
        kind = node["kind"]
        old_value = metadata.get("regenerated_from_node_id")
        if kind not in _REGENERATION_ROOT_KINDS:
            if old_value is not None:
                raise _regeneration_conflict(
                    "Only successor production roots may declare regenerated_from_node_id."
                )
            continue
        if not isinstance(old_value, str):
            raise _regeneration_conflict(
                "Every successor production root requires regenerated_from_node_id."
            )
        old_id = _parse_uuid(old_value, "regenerated_from_node_id")
        if old_id not in set(target_ids):
            raise _regeneration_conflict(
                "A successor production root references a node outside the frozen workset."
            )
        if old_id in successor_by_target:
            raise _regeneration_conflict(
                "Every frozen production root must have exactly one successor."
            )
        old_node = nodes_by_id[old_id]
        if kind != old_node.kind:
            raise _regeneration_conflict(
                "A successor production root must preserve its predecessor node kind."
            )
        if (
            metadata.get("regeneration_scope") != scope
            or metadata.get("lineage_mode") != "parallel"
        ):
            raise _regeneration_conflict(
                "Successor metadata must declare the frozen scope and parallel lineage mode."
            )
        if metadata.get("generated_by_run_id") != str(patch.run_id):
            raise _regeneration_conflict(
                "Successor metadata must identify the originating regeneration run."
            )
        local_id = operation.get("client_generated_id")
        if not isinstance(local_id, str):
            raise _regeneration_conflict("A successor production root requires a patch-local ID.")
        successor_by_target[old_id] = (operation, local_id)
    if set(successor_by_target) != set(target_ids):
        raise _regeneration_conflict(
            "The patch must contain exactly one successor for every frozen production root.",
            details={
                "missing_successor_ids": sorted(
                    str(node_id) for node_id in set(target_ids) - successor_by_target.keys()
                )
            },
        )

    lineage_by_target: dict[uuid.UUID, dict[str, Any]] = {}
    for old_id, (_successor, local_id) in successor_by_target.items():
        matches = [
            operation
            for operation in operations
            if operation["op"] == "ADD_EDGE"
            and operation["edge"]["kind"] == EdgeKind.EVOLVES_INTO
            and operation["edge"]["source_node_id"] == str(old_id)
            and operation["edge"]["target_node_id"] == local_id
        ]
        if len(matches) != 1:
            raise _regeneration_conflict(
                "Every successor requires exactly one old-to-new evolves_into lineage edge."
            )
        lineage = matches[0]
        successor_operation_id = successor_by_target[old_id][0]["operation_id"]
        if successor_operation_id not in lineage["depends_on"]:
            raise _regeneration_conflict(
                "A lineage edge must depend on its successor node operation."
            )
        if lineage["edge"].get("metadata", {}).get("generated_by_run_id") != str(patch.run_id):
            raise _regeneration_conflict(
                "A lineage edge must identify the originating regeneration run."
            )
        lineage_by_target[old_id] = lineage

    constraints = list(
        Node.objects.select_for_update()
        .filter(canvas=canvas, kind=NodeKind.CONSTRAINT, branch_root_id__in=set(target_ids))
        .order_by("id")
    )
    constraints_by_id = {constraint.id: constraint for constraint in constraints}
    clone_by_constraint: dict[uuid.UUID, dict[str, Any]] = {}
    for operation in operations:
        if operation["op"] != "ADD_NODE" or operation["node"]["kind"] != NodeKind.CONSTRAINT:
            continue
        node = operation["node"]
        provenance = node.get("metadata", {}).get("provenance_node_ids")
        if not isinstance(provenance, list) or len(provenance) != 1:
            raise _regeneration_conflict(
                "A cloned branch constraint requires exactly one predecessor constraint."
            )
        constraint_id = _parse_uuid(provenance[0], "provenance_node_ids")
        constraint = constraints_by_id.get(constraint_id)
        if constraint is None or constraint_id in clone_by_constraint:
            raise _regeneration_conflict(
                "A cloned branch constraint must uniquely match a current frozen anchor."
            )
        successor = successor_by_target[constraint.branch_root_id]
        if node.get("branch_root_node_id") != successor[1]:
            raise _regeneration_conflict(
                "A cloned branch constraint must anchor to its corresponding successor."
            )
        metadata = node.get("metadata", {})
        if (
            metadata.get("generated_by_run_id") != str(patch.run_id)
            or metadata.get("review_status") != "provisional"
        ):
            raise _regeneration_conflict(
                "A cloned branch constraint must carry provisional generated provenance."
            )
        if node.get("title") != constraint.title or node.get("body") != constraint.body:
            raise _regeneration_conflict(
                "A cloned branch constraint must preserve its predecessor content."
            )
        if _constraint_semantic_metadata(metadata) != _constraint_semantic_metadata(
            constraint.metadata
        ):
            raise _regeneration_conflict(
                "A cloned branch constraint must preserve user-owned semantic metadata."
            )
        lineage_operation_id = lineage_by_target[constraint.branch_root_id]["operation_id"]
        if lineage_operation_id not in operation["depends_on"]:
            raise _regeneration_conflict(
                "A cloned branch constraint must depend on the successor lineage edge."
            )
        clone_by_constraint[constraint_id] = operation
    if set(clone_by_constraint) != set(constraints_by_id):
        raise _regeneration_conflict(
            "The patch must clone every applicable branch-scoped constraint.",
            details={
                "missing_constraint_ids": sorted(
                    str(node_id) for node_id in set(constraints_by_id) - clone_by_constraint.keys()
                )
            },
        )

    successors: list[tuple[uuid.UUID, str, str]] = []
    operation_groups: list[tuple[str, tuple[str, ...]]] = []
    for old_id in sorted(target_ids, key=str):
        successor, local_id = successor_by_target[old_id]
        lineage = lineage_by_target[old_id]
        clone_operation_ids = sorted(
            clone["operation_id"]
            for constraint_id, clone in clone_by_constraint.items()
            if constraints_by_id[constraint_id].branch_root_id == old_id
        )
        group = tuple([successor["operation_id"], lineage["operation_id"], *clone_operation_ids])
        selected_group = set(group) & selected_ids
        if selected_group and selected_group != set(group):
            raise _conflict(
                "patch_regeneration_dependency_incomplete",
                (
                    "A successor, its lineage edge, and its cloned constraints must be "
                    "reviewed together."
                ),
                details={
                    "target_node_id": str(old_id),
                    "required_operation_ids": list(group),
                    "selected_operation_ids": sorted(selected_group),
                },
            )
        successors.append((old_id, local_id, successor["operation_id"]))
        operation_groups.append((str(old_id), group))

    preserved_nodes = tuple(
        (node.id, node.version, node.stale_since_revision) for node in locked_nodes
    )
    preserved_causes = tuple(
        NodeStalenessCause.objects.filter(canvas=canvas, node_id__in=set(permitted_ids))
        .order_by("id")
        .values_list("id", "cleared_by_graph_operation_id", "cleared_at")
    )
    # Keep operation lookup validation close to the group construction. The schema
    # normally guarantees this, but apply-time DQ-004 checks must not trust a corrupt row.
    if any(
        operation_id not in operation_by_id
        for _, group in operation_groups
        for operation_id in group
    ):
        raise _regeneration_conflict(
            "A regeneration dependency group references a missing operation."
        )
    return RegenerationApplicationContract(
        scope=scope,
        target_ids=target_ids,
        permitted_stale_ids=permitted_ids,
        successors=tuple(successors),
        operation_groups=tuple(operation_groups),
        preserved_nodes=preserved_nodes,
        preserved_causes=preserved_causes,
    )


def _propagate_regeneration_group_skips(
    contract: RegenerationApplicationContract | None,
    skipped: dict[str, dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> None:
    if contract is None:
        return
    for target_id, group in contract.operation_groups:
        blocked_by = sorted(set(group) & skipped.keys())
        if not blocked_by:
            continue
        for operation_id in group:
            if operation_id in skipped:
                continue
            conflict = {
                "operation_id": operation_id,
                "code": "regeneration_group_conflict",
                "message": "The successor lineage group could not be applied atomically.",
                "details": {"target_node_id": target_id, "blocked_by": blocked_by},
            }
            skipped[operation_id] = conflict
            conflicts.append(conflict)


def _validate_regeneration_preserved(
    contract: RegenerationApplicationContract | None,
    patch: GraphPatch,
    applied_operation_ids: set[str],
    client_id_map: dict[str, str],
) -> None:
    if contract is None:
        return
    preserved_node_ids = {
        node_id for node_id, _version, _stale_revision in contract.preserved_nodes
    }
    current_nodes = tuple(
        Node.objects.filter(canvas=patch.canvas, id__in=preserved_node_ids)
        .order_by("id")
        .values_list("id", "version", "stale_since_revision")
    )
    if current_nodes != contract.preserved_nodes or any(
        not stale
        for stale in Node.objects.filter(
            canvas=patch.canvas, id__in=set(contract.permitted_stale_ids)
        ).values_list("stale", flat=True)
    ):
        raise _regeneration_conflict(
            "Applying a parallel regeneration patch changed an old stale production member."
        )
    current_causes = tuple(
        NodeStalenessCause.objects.filter(
            canvas=patch.canvas, node_id__in=set(contract.permitted_stale_ids)
        )
        .order_by("id")
        .values_list("id", "cleared_by_graph_operation_id", "cleared_at")
    )
    if current_causes != contract.preserved_causes:
        raise _regeneration_conflict(
            "Applying a parallel regeneration patch changed an old staleness cause."
        )
    for old_id, local_id, operation_id in contract.successors:
        if operation_id not in applied_operation_ids:
            continue
        successor = Node.objects.filter(pk=_parse_uuid(client_id_map[local_id], local_id)).first()
        if successor is None or successor.metadata.get("review_status") != "accepted":
            raise _regeneration_conflict("An applied regeneration successor was not accepted.")
        if (
            successor.metadata.get("regenerated_from_node_id") != str(old_id)
            or successor.metadata.get("regeneration_scope") != contract.scope
            or successor.metadata.get("lineage_mode") != "parallel"
        ):
            raise _regeneration_conflict(
                "An applied successor lost its canonical lineage metadata."
            )


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
        node_data = operation["node"]
        provenance = node_data.get("metadata", {}).get("provenance_node_ids", [])
        for value in provenance:
            if value not in client_id_map:
                _node(canvas, _parse_uuid(value, "provenance_node_ids"))
        branch_root_value = node_data.get("branch_root_node_id")
        if node_data["kind"] != NodeKind.CONSTRAINT:
            if branch_root_value is not None:
                raise _conflict(
                    "invalid_branch_root",
                    "Only constraint nodes may have a branch root.",
                )
            return
        scope = node_data.get("metadata", {}).get("context_scope")
        if scope == "global":
            if branch_root_value is not None:
                raise _conflict(
                    "invalid_branch_root",
                    "Global constraints cannot have a branch root.",
                )
            return
        if scope != "branch" or branch_root_value is None:
            raise _conflict(
                "invalid_branch_root",
                "Branch constraints require a valid branch root.",
            )
        if branch_root_value in client_id_map:
            root_operation = next(
                (
                    candidate
                    for candidate in selected_operations
                    if candidate.get("client_generated_id") == branch_root_value
                ),
                None,
            )
            if (
                root_operation is None
                or root_operation["op"] != "ADD_NODE"
                or root_operation["node"]["kind"]
                not in {NodeKind.STRATEGY, NodeKind.CLAIM, NodeKind.OPPORTUNITY}
            ):
                raise _conflict(
                    "invalid_branch_root",
                    "A branch root must be a strategy, claim, or opportunity.",
                )
        else:
            root = _node(canvas, _parse_uuid(branch_root_value, "branch_root_node_id"))
            if root.kind not in {NodeKind.STRATEGY, NodeKind.CLAIM, NodeKind.OPPORTUNITY}:
                raise _conflict(
                    "invalid_branch_root",
                    "A branch root must be a strategy, claim, or opportunity.",
                )
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
        node = Node(
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
        node.branch_root = _branch_root(
            canvas,
            node,
            metadata,
            data.get("branch_root_node_id"),
            client_id_map,
        )
        node.save(force_insert=True)
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
    staleness_excluded_node_ids: set[uuid.UUID],
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
    invalidation = capture_direct_invalidation(canvas, operation)
    result, _entity = _apply_entity_change(patch, canvas, operation, client_id_map, now)
    invalidated_node_ids = resolve_direct_invalidation(canvas, operation, invalidation)
    eligible_nodes = list(
        Node.objects.filter(canvas=canvas, id__in=invalidated_node_ids)
        .exclude(id__in=staleness_excluded_node_ids)
        .exclude(kind=NodeKind.GENERATION_PLACEHOLDER)
        .filter(Q(metadata__review_status__isnull=True) | ~Q(metadata__review_status="rejected"))
        .order_by("id")
    )
    eligible_node_ids = tuple(node.id for node in eligible_nodes)
    newly_stale_node_ids = tuple(node.id for node in eligible_nodes if not node.stale)
    new_revision = canvas.revision + 1
    result_payload = {
        "canvas_revision": new_revision,
        **result,
        "stale_node_ids": [str(node_id) for node_id in eligible_node_ids],
        "newly_stale_node_ids": [str(node_id) for node_id in newly_stale_node_ids],
    }
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
    if invalidation is not None and eligible_node_ids:
        apply_staleness(
            canvas,
            node_ids=eligible_node_ids,
            graph_operation=graph_operation,
            origin_entity_type=invalidation.origin_entity_type,
            origin_entity_id=invalidation.origin_entity_id,
            canvas_revision=new_revision,
            now=now,
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
    regeneration_contract: RegenerationApplicationContract | None = None
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
            regeneration_contract = _validate_regeneration_application(
                patch,
                canvas,
                operations,
                selected_ids,
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
            staleness_excluded_node_ids = {
                _resolved_uuid(operation["node_id"], "node_id", client_id_map)
                for operation in selected_operations
                if operation["op"] == "DELETE_NODE"
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

            _propagate_regeneration_group_skips(
                regeneration_contract,
                skipped,
                conflicts,
            )

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
                        staleness_excluded_node_ids,
                    )
                except GraphAPIError as error:
                    conflict = {
                        "operation_id": operation_id,
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                    if regeneration_contract is not None:
                        raise _conflict(
                            "patch_apply_conflict",
                            (
                                "A regeneration lineage group changed after preflight; "
                                "the transaction was rolled back."
                            ),
                            details={"conflicts": [conflict]},
                        ) from error
                    if not request.apply_nonconflicting_only:
                        raise _conflict(
                            "patch_apply_conflict",
                            "The graph patch changed after preflight validation.",
                            details={"conflicts": [conflict]},
                        ) from error
                    skipped[operation_id] = conflict
                    conflicts.append(conflict)

            _validate_regeneration_preserved(
                regeneration_contract,
                patch,
                set(applied),
                client_id_map,
            )

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
    regeneration_telemetry: dict[str, Any] = {}
    if patch.run.operation == "regenerate_stale":
        scope = (
            regeneration_contract.scope
            if regeneration_contract is not None
            else (patch.run.context_manifest.get("regeneration") or {}).get("scope")
        )
        regeneration_telemetry = {
            "regeneration_scope": scope,
            "regeneration_workset_size": len(patch.regeneration_target_ids),
            "permitted_stale_resolution_count": len(patch.permitted_stale_resolution_ids),
            "accepted_resolution_count": 0,
            "lineage_mode": "parallel",
        }
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
        **regeneration_telemetry,
    )
    return result
