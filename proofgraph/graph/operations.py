import hashlib
import json
import math
import uuid
from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, Edge, EdgeKind, GraphOperation, Node, NodeKind
from proofgraph.graph.serialization import serialize_edge, serialize_node
from proofgraph.graph.staleness import (
    apply_staleness,
    capture_direct_invalidation,
    resolve_direct_invalidation,
)
from proofgraph.graph.telemetry import emit_graph_telemetry

DIRECT_ACTOR_TYPE = "direct_api"
_MISSING = object()
_MAX_RETAINED_SOURCE_CHARS = 500
_SOURCE_TEXT_METADATA_FIELDS = frozenset({"description", "notes", "summary"})

_COMMON_METADATA_FIELDS = frozenset({"description", "notes", "summary", "tags"})
USER_METADATA_FIELDS: dict[str, frozenset[str]] = {
    NodeKind.GOAL: _COMMON_METADATA_FIELDS | {"desired_outcome", "success_criteria", "target_user"},
    NodeKind.CONSTRAINT: _COMMON_METADATA_FIELDS | {"category", "context_scope", "pinned"},
    NodeKind.STRATEGY: _COMMON_METADATA_FIELDS | {"approach", "rationale", "target_segment"},
    NodeKind.SOURCE: _COMMON_METADATA_FIELDS,
    NodeKind.CLAIM: _COMMON_METADATA_FIELDS
    | {"claim_type", "contradiction_target_key", "mechanism_tags", "topic_keys"},
    NodeKind.OPPORTUNITY: _COMMON_METADATA_FIELDS
    | {
        "business_model",
        "builder_fit",
        "buyer",
        "current_spend_or_workaround",
        "defensibility",
        "distribution_channel",
        "distribution_rationale",
        "mechanism",
        "operational_burden",
        "problem",
        "technical_feasibility",
    },
    NodeKind.ASSUMPTION: _COMMON_METADATA_FIELDS | {"category", "importance"},
    NodeKind.RISK: _COMMON_METADATA_FIELDS | {"category", "impact", "likelihood", "mitigation"},
    NodeKind.VALIDATION_EXPERIMENT: _COMMON_METADATA_FIELDS
    | {"hypothesis", "method", "metric", "success_criteria", "timebox"},
    NodeKind.GENERATION_PLACEHOLDER: _COMMON_METADATA_FIELDS,
}

SERVER_OWNED_METADATA_FIELDS = frozenset(
    {
        "accepted_by_operation_id",
        "canonical_url",
        "content_hash",
        "evidence",
        "eligible_independence_keys",
        "eligible_source_ids",
        "generated_by_run_id",
        "generation_run_id",
        "independence_key",
        "lineage",
        "model",
        "prompt_version",
        "provenance",
        "provenance_node_ids",
        "publisher",
        "regenerated_from_node_id",
        "regeneration_scope",
        "rejected_by_operation_id",
        "replacement_node_id",
        "retrieved_at",
        "review_status",
        "reviewed_by_operation_id",
        "source_id",
        "source_ids",
        "stale",
        "stale_since_revision",
        "staleness_causes",
        "status",
        "support_status",
        "support_reviewed_by_operation_id",
        "supported",
        "speculative",
        "url",
    }
)
SERVER_OWNED_METADATA_PREFIXES = (
    "generated_",
    "generation_",
    "lineage_",
    "provenance_",
    "regeneration_",
    "source_",
    "stale_",
    "support_",
)
SERVER_OWNED_NODE_FIELDS = frozenset(
    {
        "canvas_id",
        "context_content_hash",
        "context_representation_version",
        "context_token_count",
        "created_at",
        "id",
        "kind",
        "position_updated_at",
        "position_version",
        "semantic_updated_at",
        "stale",
        "stale_since_revision",
        "updated_at",
        "version",
    }
)
SERVER_OWNED_EDGE_FIELDS = frozenset({"canvas_id", "created_at", "id", "updated_at", "version"})


def _unprocessable(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> GraphAPIError:
    return GraphAPIError(status=422, code=code, message=message, details=details)


def _validate_keys(
    value: dict[str, Any],
    *,
    allowed: set[str] | frozenset[str],
    required: set[str] | frozenset[str] = frozenset(),
    protected: set[str] | frozenset[str] = frozenset(),
) -> None:
    keys = set(value)
    protected_fields = sorted(keys & protected)
    if protected_fields:
        raise GraphAPIError(
            status=403,
            code="server_owned_field",
            message="The request attempts to write server-owned fields.",
            details={"fields": protected_fields},
        )

    unknown_fields = sorted(keys - allowed - protected)
    if unknown_fields:
        raise _unprocessable(
            "unknown_field",
            "The request contains unknown or invalid fields for this operation.",
            details={"fields": unknown_fields},
        )

    missing_fields = sorted(required - keys)
    if missing_fields:
        raise _unprocessable(
            "missing_field",
            "The request is missing required fields.",
            details={"fields": missing_fields},
        )


def _parse_uuid(value: Any, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise _unprocessable(
            "invalid_uuid",
            f"{field} must be a UUID string.",
            details={"field": field},
        )
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise _unprocessable(
            "invalid_uuid",
            f"{field} must be a UUID string.",
            details={"field": field},
        ) from error


def _positive_version(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _unprocessable(
            "invalid_version",
            f"{field} must be a positive integer.",
            details={"field": field},
        )
    return value


def _validate_title(value: Any, *, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _unprocessable("invalid_title", "title must be a non-empty string.")
    title = value.strip()
    if kind == NodeKind.SOURCE and len(title) > _MAX_RETAINED_SOURCE_CHARS:
        raise _unprocessable(
            "retention_policy_violation",
            f"Source titles may contain at most {_MAX_RETAINED_SOURCE_CHARS} Unicode characters.",
            details={"field": "title", "max_characters": _MAX_RETAINED_SOURCE_CHARS},
        )
    return title


def _validate_body(value: Any, *, kind: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise _unprocessable("invalid_body", "body must be a string or null.")
    if kind == NodeKind.SOURCE and value is not None and len(value) > _MAX_RETAINED_SOURCE_CHARS:
        raise _unprocessable(
            "retention_policy_violation",
            f"Source excerpts may contain at most {_MAX_RETAINED_SOURCE_CHARS} Unicode characters.",
            details={"field": "body", "max_characters": _MAX_RETAINED_SOURCE_CHARS},
        )
    return value


def _validate_json_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _unprocessable(
            "invalid_object",
            f"{field} must be a JSON object.",
            details={"field": field},
        )
    return value


def _is_server_owned_metadata(field: str) -> bool:
    return field in SERVER_OWNED_METADATA_FIELDS or field.startswith(SERVER_OWNED_METADATA_PREFIXES)


def _validate_metadata(kind: str, metadata: Any) -> dict[str, Any]:
    metadata_object = _validate_json_object(metadata, "metadata")
    server_owned = sorted(field for field in metadata_object if _is_server_owned_metadata(field))
    if server_owned:
        raise GraphAPIError(
            status=403,
            code="server_owned_field",
            message="The request attempts to write server-owned metadata.",
            details={"fields": server_owned},
        )

    allowed_fields = USER_METADATA_FIELDS[kind]
    wrong_kind_fields = sorted(set(metadata_object) - allowed_fields)
    if wrong_kind_fields:
        raise _unprocessable(
            "wrong_kind_field",
            "The metadata contains fields that are not writable for this node kind.",
            details={"fields": wrong_kind_fields, "kind": kind},
        )
    if kind == NodeKind.SOURCE:
        for field in _SOURCE_TEXT_METADATA_FIELDS:
            value = metadata_object.get(field)
            if value is not None and not isinstance(value, str):
                raise _unprocessable(
                    "retention_policy_violation",
                    "Source metadata excerpts must be strings or null.",
                    details={"field": field},
                )
            if isinstance(value, str) and len(value) > _MAX_RETAINED_SOURCE_CHARS:
                raise _unprocessable(
                    "retention_policy_violation",
                    (
                        f"Source metadata excerpts may contain at most "
                        f"{_MAX_RETAINED_SOURCE_CHARS} Unicode characters."
                    ),
                    details={"field": field, "max_characters": _MAX_RETAINED_SOURCE_CHARS},
                )
        tags = metadata_object.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise _unprocessable(
                    "retention_policy_violation",
                    "Source tags must be a list of strings.",
                    details={"field": "tags"},
                )
            if sum(len(tag) for tag in tags) > _MAX_RETAINED_SOURCE_CHARS:
                raise _unprocessable(
                    "retention_policy_violation",
                    (
                        f"Source tags may contain at most {_MAX_RETAINED_SOURCE_CHARS} "
                        "Unicode characters in total."
                    ),
                    details={"field": "tags", "max_characters": _MAX_RETAINED_SOURCE_CHARS},
                )
    return metadata_object


def _validate_position(value: Any) -> dict[str, int | float]:
    position = _validate_json_object(value, "position")
    if set(position) != {"x", "y"}:
        raise _unprocessable(
            "invalid_position",
            "position must contain exactly numeric x and y fields.",
        )
    for coordinate in ("x", "y"):
        number = position[coordinate]
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
        ):
            raise _unprocessable(
                "invalid_position",
                "position must contain exactly numeric x and y fields.",
            )
    return position


def _validate_node_kind(value: Any) -> str:
    if value not in NodeKind.values:
        raise _unprocessable(
            "invalid_node_kind",
            "kind is not part of the frozen node taxonomy.",
        )
    return value


def _validate_edge_kind(value: Any) -> str:
    if value not in EdgeKind.values:
        raise _unprocessable(
            "invalid_edge_kind",
            "kind is not part of the frozen edge taxonomy.",
        )
    return value


def _locked_node(canvas: Canvas, value: Any) -> Node:
    node_id = _parse_uuid(value, "node_id")
    node = Node.objects.select_for_update().filter(canvas=canvas, pk=node_id).first()
    if node is None:
        raise GraphAPIError(status=404, code="node_not_found", message="Node not found.")
    return node


def _locked_edge(canvas: Canvas, value: Any) -> Edge:
    edge_id = _parse_uuid(value, "edge_id")
    edge = Edge.objects.select_for_update().filter(canvas=canvas, pk=edge_id).first()
    if edge is None:
        raise GraphAPIError(status=404, code="edge_not_found", message="Edge not found.")
    return edge


def _check_version(entity: Node | Edge, expected: int, *, position: bool = False) -> None:
    current = entity.position_version if position and isinstance(entity, Node) else entity.version
    if current != expected:
        raise GraphAPIError(
            status=409,
            code="version_conflict",
            message="The entity version no longer matches the request.",
            details={"expected_version": expected, "current_version": current},
        )


def _resolve_branch_root(canvas: Canvas, value: Any) -> Node:
    root_id = _parse_uuid(value, "branch_root_node_id")
    summary = Node.objects.filter(pk=root_id).values("canvas_id", "kind").first()
    if summary is None or summary["canvas_id"] != canvas.id:
        raise _unprocessable(
            "invalid_branch_root",
            "A branch root must be an existing node on the same canvas.",
        )
    if summary["kind"] not in {NodeKind.STRATEGY, NodeKind.CLAIM, NodeKind.OPPORTUNITY}:
        raise _unprocessable(
            "invalid_branch_root",
            "A branch root must be a strategy, claim, or opportunity node.",
        )
    return Node.objects.select_for_update().get(canvas=canvas, pk=root_id)


def _constraint_root(
    canvas: Canvas,
    *,
    kind: str,
    metadata: dict[str, Any],
    root_value: Any,
) -> Node | None:
    if kind != NodeKind.CONSTRAINT:
        if root_value is not _MISSING:
            raise _unprocessable(
                "wrong_kind_field",
                "branch_root_node_id is writable only for constraint nodes.",
                details={"fields": ["branch_root_node_id"], "kind": kind},
            )
        return None

    scope = metadata.get("context_scope")
    pinned = metadata.get("pinned")
    if scope not in {"global", "branch"}:
        raise _unprocessable(
            "invalid_constraint_metadata",
            "Constraint metadata requires context_scope set to global or branch.",
        )
    if not isinstance(pinned, bool):
        raise _unprocessable(
            "invalid_constraint_metadata",
            "Constraint metadata requires pinned set to a boolean.",
        )
    if scope == "global":
        if root_value is not _MISSING and root_value is not None:
            raise _unprocessable(
                "invalid_branch_root",
                "Global constraints cannot have a branch root.",
            )
        return None
    if root_value is _MISSING or root_value is None:
        raise _unprocessable(
            "invalid_branch_root",
            "Branch constraints require a branch root.",
        )
    return _resolve_branch_root(canvas, root_value)


def _lock_endpoints(canvas: Canvas, source_value: Any, target_value: Any) -> tuple[Node, Node]:
    source_id = _parse_uuid(source_value, "source_node_id")
    target_id = _parse_uuid(target_value, "target_node_id")
    node_ids = {source_id, target_id}
    nodes = list(
        Node.objects.select_for_update().filter(canvas=canvas, id__in=node_ids).order_by("id")
    )
    nodes_by_id = {node.id: node for node in nodes}
    missing_ids = sorted(str(node_id) for node_id in node_ids - set(nodes_by_id))
    if missing_ids:
        raise _unprocessable(
            "invalid_edge_endpoint",
            "Every edge endpoint must exist on the same canvas.",
            details={"node_ids": missing_ids},
        )
    return nodes_by_id[source_id], nodes_by_id[target_id]


def _add_node(canvas: Canvas, payload: dict[str, Any], now: Any) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "node"},
        required={"op", "operation_key", "node"},
    )
    node_data = _validate_json_object(payload["node"], "node")
    _validate_keys(
        node_data,
        allowed={"kind", "title", "body", "metadata", "branch_root_node_id", "position"},
        required={"kind", "title"},
        protected=SERVER_OWNED_NODE_FIELDS - {"kind"},
    )
    kind = _validate_node_kind(node_data["kind"])
    metadata = _validate_metadata(kind, node_data.get("metadata", {}))
    root_value = node_data.get("branch_root_node_id", _MISSING)
    branch_root = _constraint_root(
        canvas,
        kind=kind,
        metadata=metadata,
        root_value=root_value,
    )
    position = _validate_position(node_data["position"]) if "position" in node_data else {}
    node = Node.objects.create(
        canvas=canvas,
        kind=kind,
        title=_validate_title(node_data["title"], kind=kind),
        body=_validate_body(node_data.get("body"), kind=kind),
        metadata=metadata,
        branch_root=branch_root,
        position=position,
        created_at=now,
        semantic_updated_at=now,
        position_updated_at=now,
        updated_at=now,
    )
    return {"node": serialize_node(node)}


def _semantic_node_changes(
    canvas: Canvas,
    payload: dict[str, Any],
    *,
    metadata_only: bool,
    now: Any,
) -> dict[str, object]:
    if metadata_only:
        _validate_keys(
            payload,
            allowed={
                "op",
                "operation_key",
                "node_id",
                "expected_version",
                "metadata",
                "branch_root_node_id",
            },
            required={"op", "operation_key", "node_id", "expected_version", "metadata"},
        )
    else:
        _validate_keys(
            payload,
            allowed={"op", "operation_key", "node_id", "expected_version", "changes"},
            required={"op", "operation_key", "node_id", "expected_version", "changes"},
        )

    node = _locked_node(canvas, payload["node_id"])
    expected = _positive_version(payload["expected_version"], "expected_version")
    _check_version(node, expected)

    if metadata_only:
        changes = {"metadata": payload["metadata"]}
        if "branch_root_node_id" in payload:
            changes["branch_root_node_id"] = payload["branch_root_node_id"]
    else:
        changes = _validate_json_object(payload["changes"], "changes")

    _validate_keys(
        changes,
        allowed={"title", "body", "metadata", "branch_root_node_id"},
        protected=SERVER_OWNED_NODE_FIELDS,
    )
    if not changes or (
        set(changes) == {"metadata"} and not _validate_json_object(changes["metadata"], "metadata")
    ):
        raise _unprocessable("empty_update", "A semantic update must change at least one field.")

    metadata_patch = changes.get("metadata", {})
    validated_patch = _validate_metadata(node.kind, metadata_patch)
    final_metadata = {**node.metadata, **validated_patch}
    root_value = changes.get("branch_root_node_id", _MISSING)
    if (
        node.kind == NodeKind.CONSTRAINT
        and final_metadata.get("context_scope") == "global"
        and root_value is _MISSING
    ):
        root_value = None
    elif node.kind == NodeKind.CONSTRAINT and root_value is _MISSING:
        root_value = str(node.branch_root_id) if node.branch_root_id else _MISSING
    branch_root = _constraint_root(
        canvas,
        kind=node.kind,
        metadata=final_metadata,
        root_value=root_value,
    )

    if "title" in changes:
        node.title = _validate_title(changes["title"], kind=node.kind)
    if "body" in changes:
        node.body = _validate_body(changes["body"], kind=node.kind)
    node.metadata = final_metadata
    node.branch_root = branch_root
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
    return {"node": serialize_node(node)}


def _update_node(canvas: Canvas, payload: dict[str, Any], now: Any) -> dict[str, object]:
    return _semantic_node_changes(canvas, payload, metadata_only=False, now=now)


def _patch_node_metadata(
    canvas: Canvas,
    payload: dict[str, Any],
    now: Any,
) -> dict[str, object]:
    return _semantic_node_changes(canvas, payload, metadata_only=True, now=now)


def _move_node(canvas: Canvas, payload: dict[str, Any], now: Any) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "node_id", "expected_position_version", "position"},
        required={"op", "operation_key", "node_id", "expected_position_version", "position"},
    )
    node = _locked_node(canvas, payload["node_id"])
    expected = _positive_version(
        payload["expected_position_version"],
        "expected_position_version",
    )
    _check_version(node, expected, position=True)
    node.position = _validate_position(payload["position"])
    node.position_version += 1
    node.position_updated_at = now
    node.updated_at = now
    node.save(update_fields=["position", "position_version", "position_updated_at", "updated_at"])
    return {"node": serialize_node(node)}


def _delete_node(canvas: Canvas, payload: dict[str, Any], _now: Any) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "node_id", "expected_version"},
        required={"op", "operation_key", "node_id", "expected_version"},
    )
    node = _locked_node(canvas, payload["node_id"])
    expected = _positive_version(payload["expected_version"], "expected_version")
    _check_version(node, expected)

    incident_edges = list(
        Edge.objects.filter(canvas=canvas)
        .filter(Q(source=node) | Q(target=node))
        .order_by("id")
        .values("id", "version")
    )
    branch_constraints = list(
        Node.objects.filter(canvas=canvas, branch_root=node).order_by("id").values("id", "version")
    )
    if incident_edges or branch_constraints:
        raise GraphAPIError(
            status=409,
            code="node_has_dependencies",
            message=(
                "Delete incident edges and resolve branch constraints before deleting this node."
            ),
            details={
                "incident_edges": [
                    {"id": str(item["id"]), "version": item["version"]} for item in incident_edges
                ],
                "referencing_constraints": [
                    {"id": str(item["id"]), "version": item["version"]}
                    for item in branch_constraints
                ],
            },
        )

    deleted_node_id = str(node.id)
    node.delete()
    return {"deleted_node_id": deleted_node_id}


def _add_edge(canvas: Canvas, payload: dict[str, Any], now: Any) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "edge"},
        required={"op", "operation_key", "edge"},
    )
    edge_data = _validate_json_object(payload["edge"], "edge")
    _validate_keys(
        edge_data,
        allowed={"source_node_id", "target_node_id", "kind", "metadata"},
        required={"source_node_id", "target_node_id", "kind"},
        protected=SERVER_OWNED_EDGE_FIELDS,
    )
    source, target = _lock_endpoints(
        canvas,
        edge_data["source_node_id"],
        edge_data["target_node_id"],
    )
    metadata = _validate_json_object(edge_data.get("metadata", {}), "metadata")
    edge = Edge.objects.create(
        canvas=canvas,
        source=source,
        target=target,
        kind=_validate_edge_kind(edge_data["kind"]),
        metadata=metadata,
        created_at=now,
        updated_at=now,
    )
    return {"edge": serialize_edge(edge)}


def _update_edge(canvas: Canvas, payload: dict[str, Any], now: Any) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "edge_id", "expected_version", "changes"},
        required={"op", "operation_key", "edge_id", "expected_version", "changes"},
    )
    edge = _locked_edge(canvas, payload["edge_id"])
    expected = _positive_version(payload["expected_version"], "expected_version")
    _check_version(edge, expected)
    changes = _validate_json_object(payload["changes"], "changes")
    _validate_keys(
        changes,
        allowed={"source_node_id", "target_node_id", "kind", "metadata"},
        protected=SERVER_OWNED_EDGE_FIELDS,
    )
    if not changes:
        raise _unprocessable("empty_update", "An edge update must change at least one field.")

    source_id = changes.get("source_node_id", str(edge.source_id))
    target_id = changes.get("target_node_id", str(edge.target_id))
    source, target = _lock_endpoints(canvas, source_id, target_id)
    edge.source = source
    edge.target = target
    if "kind" in changes:
        edge.kind = _validate_edge_kind(changes["kind"])
    if "metadata" in changes:
        edge.metadata = _validate_json_object(changes["metadata"], "metadata")
    edge.version += 1
    edge.updated_at = now
    edge.save(update_fields=["source", "target", "kind", "metadata", "version", "updated_at"])
    return {"edge": serialize_edge(edge)}


def _delete_edge(canvas: Canvas, payload: dict[str, Any], _now: Any) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "edge_id", "expected_version"},
        required={"op", "operation_key", "edge_id", "expected_version"},
    )
    edge = _locked_edge(canvas, payload["edge_id"])
    expected = _positive_version(payload["expected_version"], "expected_version")
    _check_version(edge, expected)
    deleted_edge_id = str(edge.id)
    edge.delete()
    return {"deleted_edge_id": deleted_edge_id}


def _replace_assumption(
    canvas: Canvas,
    payload: dict[str, Any],
    now: Any,
) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "node_id", "expected_version", "replacement"},
        required={"op", "operation_key", "node_id", "expected_version", "replacement"},
    )
    assumption = _locked_node(canvas, payload["node_id"])
    if assumption.kind != NodeKind.ASSUMPTION:
        raise _unprocessable(
            "invalid_assumption",
            "Assumption replacement requires an assumption node.",
        )
    if assumption.metadata.get("review_status") != "accepted":
        raise _unprocessable(
            "invalid_assumption",
            "Assumption replacement requires an applied, non-rejected assumption.",
        )
    expected = _positive_version(payload["expected_version"], "expected_version")
    _check_version(assumption, expected)
    replacement = _validate_json_object(payload["replacement"], "replacement")
    _validate_keys(
        replacement,
        allowed={"title", "body"},
        required={"title"},
    )
    owner_ids = list(
        Edge.objects.filter(
            canvas=canvas,
            target=assumption,
            kind=EdgeKind.DERIVED_FROM,
            source__kind=NodeKind.OPPORTUNITY,
        )
        .order_by("source_id")
        .values_list("source_id", flat=True)
        .distinct()
    )
    owners = list(Node.objects.select_for_update().filter(id__in=owner_ids).order_by("id"))
    if len(owners) != 1:
        raise _unprocessable(
            "invalid_assumption_lineage",
            "The assumption must belong to exactly one opportunity family.",
            details={"owner_count": len(owners)},
        )
    previous = {
        "title": assumption.title,
        "body": assumption.body,
        "version": assumption.version,
        "opportunity_id": str(owners[0].id),
    }
    assumption.title = _validate_title(replacement["title"], kind=assumption.kind)
    if "body" in replacement:
        assumption.body = _validate_body(replacement["body"], kind=assumption.kind)
    else:
        assumption.body = assumption.title
    assumption.version += 1
    assumption.context_token_count = None
    assumption.context_content_hash = None
    assumption.semantic_updated_at = now
    assumption.updated_at = now
    assumption.save(
        update_fields=[
            "title",
            "body",
            "version",
            "context_token_count",
            "context_content_hash",
            "semantic_updated_at",
            "updated_at",
        ]
    )
    return {
        "node": serialize_node(assumption),
        "previous_assumption": previous,
        "opportunity_id": str(owners[0].id),
    }


def _reject_evidence(
    canvas: Canvas,
    payload: dict[str, Any],
    now: Any,
) -> dict[str, object]:
    _validate_keys(
        payload,
        allowed={"op", "operation_key", "node_id", "expected_version"},
        required={"op", "operation_key", "node_id", "expected_version"},
    )
    evidence = _locked_node(canvas, payload["node_id"])
    if evidence.kind not in {NodeKind.SOURCE, NodeKind.CLAIM}:
        raise _unprocessable(
            "invalid_evidence",
            "Evidence rejection requires a source or claim node.",
        )
    if evidence.metadata.get("review_status") == "provisional":
        raise _unprocessable(
            "invalid_evidence",
            "Provisional evidence must be accepted through patch review before rejection.",
        )
    if evidence.metadata.get("review_status") == "rejected":
        raise GraphAPIError(
            status=409,
            code="evidence_already_rejected",
            message="The evidence was already rejected.",
        )
    expected = _positive_version(payload["expected_version"], "expected_version")
    _check_version(evidence, expected)

    impacted_claims: list[Node] = []
    if evidence.kind == NodeKind.SOURCE:
        claim_ids = list(
            Edge.objects.filter(
                canvas=canvas,
                kind=EdgeKind.EXTRACTED_FROM,
                target=evidence,
                source__kind=NodeKind.CLAIM,
            )
            .order_by("source_id")
            .values_list("source_id", flat=True)
            .distinct()
        )
        impacted_claims = list(
            Node.objects.select_for_update()
            .filter(canvas=canvas, id__in=claim_ids)
            .filter(
                Q(metadata__review_status__isnull=True) | ~Q(metadata__review_status="rejected")
            )
            .order_by("id")
        )

    rejected_claim_ids: list[uuid.UUID] = []
    retained_claim_ids: list[uuid.UUID] = []
    for claim in impacted_claims:
        source_ids = list(
            Edge.objects.filter(
                canvas=canvas,
                kind=EdgeKind.EXTRACTED_FROM,
                source=claim,
                target__kind=NodeKind.SOURCE,
            )
            .exclude(target=evidence)
            .order_by("target_id")
            .values_list("target_id", flat=True)
            .distinct()
        )
        eligible_sources = list(
            Node.objects.select_for_update()
            .filter(canvas=canvas, id__in=source_ids)
            .filter(
                Q(metadata__review_status__isnull=True)
                | ~Q(metadata__review_status__in=["provisional", "rejected"])
            )
            .order_by("id")
        )
        eligible_keys = sorted(
            {
                key
                for source in eligible_sources
                for key in [source.metadata.get("independence_key")]
                if isinstance(key, str) and key
            }
        )
        claim.metadata = {
            **claim.metadata,
            "eligible_source_ids": [str(source.id) for source in eligible_sources],
            "eligible_independence_keys": eligible_keys,
            "independent_support_count": len(eligible_keys),
        }
        if eligible_sources:
            retained_claim_ids.append(claim.id)
        else:
            claim.metadata["review_status"] = "rejected"
            rejected_claim_ids.append(claim.id)
        claim.version += 1
        claim.context_token_count = None
        claim.context_content_hash = None
        claim.semantic_updated_at = now
        claim.updated_at = now

    evidence.metadata = {**evidence.metadata, "review_status": "rejected"}
    evidence.version += 1
    evidence.context_token_count = None
    evidence.context_content_hash = None
    evidence.semantic_updated_at = now
    evidence.updated_at = now
    if impacted_claims:
        Node.objects.bulk_update(
            impacted_claims,
            [
                "metadata",
                "version",
                "context_token_count",
                "context_content_hash",
                "semantic_updated_at",
                "updated_at",
            ],
        )
    evidence.save(
        update_fields=[
            "metadata",
            "version",
            "context_token_count",
            "context_content_hash",
            "semantic_updated_at",
            "updated_at",
        ]
    )
    return {
        "rejected_evidence_id": str(evidence.id),
        "rejected_evidence_kind": evidence.kind,
        "impacted_claim_ids": [str(claim.id) for claim in impacted_claims],
        "rejected_claim_ids": [str(node_id) for node_id in rejected_claim_ids],
        "retained_claim_ids": [str(node_id) for node_id in retained_claim_ids],
    }


def _stamp_evidence_review_operation(
    canvas: Canvas,
    graph_operation: GraphOperation,
    result: dict[str, object],
    now: Any,
) -> None:
    rejected_ids = {
        uuid.UUID(node_id)
        for field in ("rejected_evidence_id", "rejected_claim_ids")
        for node_id in (
            [result[field]] if isinstance(result.get(field), str) else result.get(field, [])
        )
        if isinstance(node_id, str)
    }
    retained_ids = {
        uuid.UUID(node_id)
        for node_id in result.get("retained_claim_ids", [])
        if isinstance(node_id, str)
    }
    nodes = list(
        Node.objects.select_for_update()
        .filter(canvas=canvas, id__in=rejected_ids | retained_ids)
        .order_by("id")
    )
    for node in nodes:
        node.metadata = {
            **node.metadata,
            "reviewed_by_operation_id": graph_operation.id,
            **(
                {"rejected_by_operation_id": graph_operation.id}
                if node.id in rejected_ids
                else {"support_reviewed_by_operation_id": graph_operation.id}
            ),
        }
        node.updated_at = now
    if nodes:
        Node.objects.bulk_update(nodes, ["metadata", "updated_at"])


OperationHandler = Callable[[Canvas, dict[str, Any], Any], dict[str, object]]
OPERATION_HANDLERS: dict[str, OperationHandler] = {
    "ADD_NODE": _add_node,
    "UPDATE_NODE": _update_node,
    "DELETE_NODE": _delete_node,
    "ADD_EDGE": _add_edge,
    "UPDATE_EDGE": _update_edge,
    "DELETE_EDGE": _delete_edge,
    "PATCH_NODE_METADATA": _patch_node_metadata,
    "MOVE_NODE": _move_node,
    "REPLACE_ASSUMPTION": _replace_assumption,
    "REJECT_EVIDENCE": _reject_evidence,
}


def _canonical_request(payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    op = payload.get("op")
    if not isinstance(op, str) or not op:
        raise _unprocessable("invalid_operation", "op must name a graph operation.")
    operation_key = _parse_uuid(payload.get("operation_key"), "operation_key")
    canonical_payload = dict(payload)
    canonical_payload["operation_key"] = str(operation_key)
    try:
        encoded = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as error:
        raise _unprocessable(
            "invalid_json_value",
            "The operation contains a value that cannot be persisted as JSON.",
        ) from error
    return canonical_payload, op, hashlib.sha256(encoded).hexdigest()


def apply_graph_operation(
    canvas_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    actor_type: str = DIRECT_ACTOR_TYPE,
    actor_id: str | None = None,
) -> dict[str, object]:
    canonical_payload, op, fingerprint = _canonical_request(payload)

    telemetry_events: list[tuple[str, dict[str, Any]]] = []
    with transaction.atomic():
        canvas = Canvas.objects.select_for_update().filter(pk=canvas_id).first()
        if canvas is None:
            raise GraphAPIError(
                status=404,
                code="canvas_not_found",
                message="Canvas not found.",
            )

        existing = GraphOperation.objects.filter(
            canvas=canvas,
            actor_type=actor_type,
            operation_key=canonical_payload["operation_key"],
        ).first()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise GraphAPIError(
                    status=409,
                    code="operation_key_conflict",
                    message="The operation key was already used for different content.",
                )
            emit_graph_telemetry(
                "graph.operation_replayed",
                canvas_id=canvas.id,
                graph_operation_id=existing.id,
                operation_key=existing.operation_key,
                operation_type=existing.operation_type,
                actor_type=existing.actor_type,
                canvas_revision=existing.canvas_revision,
            )
            return existing.result_payload

        handler = OPERATION_HANDLERS.get(op)
        if handler is None:
            raise _unprocessable(
                "unsupported_operation",
                "op is not a supported localized graph operation.",
                details={"op": op},
            )

        now = timezone.now()
        invalidation = capture_direct_invalidation(canvas, canonical_payload)
        result = handler(canvas, canonical_payload, now)
        invalidated_node_ids = resolve_direct_invalidation(
            canvas,
            canonical_payload,
            invalidation,
        )
        eligible_nodes = list(
            Node.objects.filter(canvas=canvas, id__in=invalidated_node_ids)
            .exclude(kind=NodeKind.GENERATION_PLACEHOLDER)
            .filter(
                Q(metadata__review_status__isnull=True) | ~Q(metadata__review_status="rejected")
            )
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
            actor_type=actor_type,
            actor_id=actor_id,
            operation_key=canonical_payload["operation_key"],
            request_fingerprint=fingerprint,
            operation_type=op,
            payload=canonical_payload,
            result_payload=result_payload,
            canvas_revision=new_revision,
            created_at=now,
        )
        telemetry_events.append(
            (
                "graph.operation_committed",
                {
                    "canvas_id": canvas.id,
                    "graph_operation_id": graph_operation.id,
                    "operation_key": graph_operation.operation_key,
                    "operation_type": graph_operation.operation_type,
                    "actor_type": graph_operation.actor_type,
                    "canvas_revision": graph_operation.canvas_revision,
                    "stale_count": len(eligible_node_ids),
                    "newly_stale_count": len(newly_stale_node_ids),
                },
            )
        )
        if op == "REJECT_EVIDENCE":
            _stamp_evidence_review_operation(canvas, graph_operation, result, now)
            telemetry_events.append(
                (
                    "graph.evidence_rejected",
                    {
                        "canvas_id": canvas.id,
                        "graph_operation_id": graph_operation.id,
                        "evidence_kind": result["rejected_evidence_kind"],
                        "impacted_claim_count": len(result["impacted_claim_ids"]),
                        "rejected_claim_count": len(result["rejected_claim_ids"]),
                        "retained_claim_count": len(result["retained_claim_ids"]),
                    },
                )
            )
        if invalidation is not None and eligible_node_ids:
            staleness = apply_staleness(
                canvas,
                node_ids=eligible_node_ids,
                graph_operation=graph_operation,
                origin_entity_type=invalidation.origin_entity_type,
                origin_entity_id=invalidation.origin_entity_id,
                canvas_revision=new_revision,
                now=now,
            )
            telemetry_events.append(
                (
                    "graph.staleness_propagated",
                    {
                        "canvas_id": canvas.id,
                        "graph_operation_id": graph_operation.id,
                        "origin_entity_type": invalidation.origin_entity_type,
                        "origin_entity_id": invalidation.origin_entity_id,
                        "stale_count": len(staleness.stale_node_ids),
                        "newly_stale_count": len(staleness.newly_stale_node_ids),
                    },
                )
            )
        canvas.revision = new_revision
        canvas.updated_at = now
        canvas.save(update_fields=["revision", "updated_at"])
    for event_name, telemetry in telemetry_events:
        emit_graph_telemetry(event_name, **telemetry)
    return result_payload
