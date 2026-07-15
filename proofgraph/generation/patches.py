from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from proofgraph.demo.models import DemoSession
from proofgraph.demo.quotas import (
    consume_hybrid_quota,
    emit_replay_selected,
    validate_demo_profile,
)
from proofgraph.generation.composition import get_composition
from proofgraph.generation.context import validate_explicit_selection
from proofgraph.generation.models import (
    CanvasEventCursor,
    GenerationRun,
    GraphPatch,
    GraphPatchOperationDecision,
    PatchDecision,
    PatchStatus,
    RunStatus,
)
from proofgraph.generation.schemas import GenerationRunRequest, PatchRegenerationRequest
from proofgraph.generation.services import (
    ServiceResult,
    generation_request_fingerprint,
    serialize_generation_run,
)
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, Edge, Node

DIRECT_ACTOR_TYPE = "direct_api"


def _patch_operations(patch: GraphPatch) -> list[dict[str, Any]]:
    operations = patch.operations
    if not isinstance(operations, list) or not all(isinstance(item, dict) for item in operations):
        raise GraphAPIError(
            status=500,
            code="invalid_patch_contract",
            message="The stored graph patch does not contain valid candidate operations.",
        )
    return operations


def _operation_identity(operation: dict[str, Any], index: int) -> str:
    value = operation.get("operation_id")
    return value if isinstance(value, str) and value else f"operation-{index}"


def _operation_preview(
    operation: dict[str, Any],
    *,
    node: Node | None,
    edge: Edge | None,
) -> dict[str, Any]:
    op = operation.get("op")
    if op in {"ADD_NODE", "ADD_EDGE"}:
        change_type = "addition"
    elif op in {"DELETE_NODE", "DELETE_EDGE"}:
        change_type = "deletion"
    elif op == "MOVE_NODE":
        change_type = "position"
    else:
        change_type = "update"

    candidate_node = operation.get("node") if isinstance(operation.get("node"), dict) else {}
    candidate_edge = operation.get("edge") if isinstance(operation.get("edge"), dict) else {}
    changes = operation.get("changes") if isinstance(operation.get("changes"), dict) else {}
    metadata = candidate_node.get("metadata")
    if not isinstance(metadata, dict):
        metadata = node.metadata if node is not None and isinstance(node.metadata, dict) else {}

    entity_type = "edge" if op in {"ADD_EDGE", "UPDATE_EDGE", "DELETE_EDGE"} else "node"
    if entity_type == "node":
        semantic_role = candidate_node.get("kind") or (node.kind if node is not None else None)
        title = candidate_node.get("title") or changes.get("title")
        if title is None and node is not None:
            title = node.title
    else:
        semantic_role = candidate_edge.get("kind") or changes.get("kind")
        if semantic_role is None and edge is not None:
            semantic_role = edge.kind
        title = f"{semantic_role or 'edge'} relation"

    dimensions = metadata.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = None
    provenance = metadata.get("provenance_node_ids")
    if not isinstance(provenance, list):
        provenance = []

    return {
        "change_type": change_type,
        "entity_type": entity_type,
        "semantic_role": semantic_role,
        "title": title,
        "provenance_node_ids": provenance,
        "assumptions": metadata.get("assumptions")
        if isinstance(metadata.get("assumptions"), list)
        else [],
        "risks": metadata.get("risks") if isinstance(metadata.get("risks"), list) else [],
        "contradiction": metadata.get("contradiction"),
        "quality_dimensions": dimensions,
        "distribution_rationale": metadata.get("distribution_rationale"),
        "defensibility_rationale": metadata.get("defensibility"),
    }


def _serialize_decision(decision: GraphPatchOperationDecision) -> dict[str, Any]:
    return {
        "decision_id": str(decision.id),
        "operation_index": decision.operation_index,
        "decision": decision.decision,
        "reason": decision.reason,
        "actor_type": decision.actor_type,
        "actor_id": decision.actor_id,
        "graph_operation_id": (
            str(decision.graph_operation_id) if decision.graph_operation_id is not None else None
        ),
        "decided_at": decision.decided_at.isoformat(),
    }


def serialize_graph_patch(patch: GraphPatch) -> dict[str, Any]:
    operations = _patch_operations(patch)
    operation_ids = {
        _operation_identity(operation, index): index for index, operation in enumerate(operations)
    }
    node_ids = {
        operation.get("node_id")
        for operation in operations
        if isinstance(operation.get("node_id"), str)
    }
    edge_ids = {
        operation.get("edge_id")
        for operation in operations
        if isinstance(operation.get("edge_id"), str)
    }
    nodes = {
        str(node.id): node
        for node in Node.objects.filter(canvas_id=patch.canvas_id, id__in=node_ids)
    }
    edges = {
        str(edge.id): edge
        for edge in Edge.objects.filter(canvas_id=patch.canvas_id, id__in=edge_ids)
    }

    serialized_operations = []
    for index, operation in enumerate(operations):
        operation_id = _operation_identity(operation, index)
        dependencies = operation.get("depends_on")
        if not isinstance(dependencies, list):
            dependencies = []
        node_id = operation.get("node_id")
        edge_id = operation.get("edge_id")
        serialized_operations.append(
            {
                "operation_index": index,
                "operation_id": operation_id,
                "candidate": operation,
                "dependency_operation_ids": dependencies,
                "dependency_operation_indices": [
                    operation_ids[item] for item in dependencies if item in operation_ids
                ],
                "missing_dependency_operation_ids": [
                    item for item in dependencies if item not in operation_ids
                ],
                "review": _operation_preview(
                    operation,
                    node=nodes.get(node_id) if isinstance(node_id, str) else None,
                    edge=edges.get(edge_id) if isinstance(edge_id, str) else None,
                ),
            }
        )

    decisions = [
        _serialize_decision(decision)
        for decision in patch.decisions.all().order_by("operation_index", "id")
    ]
    return {
        "patch_id": str(patch.id),
        "run_id": str(patch.run_id),
        "canvas_id": str(patch.canvas_id),
        "base_canvas_revision": patch.base_canvas_revision,
        "status": patch.status,
        "operations": serialized_operations,
        "regeneration_target_ids": patch.regeneration_target_ids,
        "permitted_stale_resolution_ids": patch.permitted_stale_resolution_ids,
        "client_id_map": patch.client_id_map,
        "decisions": decisions,
        "regenerated_by_run_id": (
            str(patch.regenerated_by_run_id) if patch.regenerated_by_run_id is not None else None
        ),
        "created_at": patch.created_at.isoformat(),
        "decided_at": patch.decided_at.isoformat() if patch.decided_at else None,
        "applied_at": patch.applied_at.isoformat() if patch.applied_at else None,
    }


def get_graph_patch(patch_id: uuid.UUID) -> GraphPatch:
    patch = GraphPatch.objects.filter(pk=patch_id).first()
    if patch is None:
        raise GraphAPIError(
            status=404, code="graph_patch_not_found", message="Graph patch not found."
        )
    return patch


def reject_graph_patch(patch_id: uuid.UUID) -> ServiceResult:
    with transaction.atomic():
        patch = GraphPatch.objects.select_for_update().filter(pk=patch_id).first()
        if patch is None:
            raise GraphAPIError(
                status=404,
                code="graph_patch_not_found",
                message="Graph patch not found.",
            )
        operations = _patch_operations(patch)
        if patch.status == PatchStatus.REJECTED:
            result = ServiceResult({"patch": serialize_graph_patch(patch)}, 200)
            rejected_now = False
        elif patch.status != PatchStatus.PENDING:
            raise GraphAPIError(
                status=409,
                code="patch_not_pending",
                message="Only a pending graph patch can be rejected.",
                details={"status": patch.status},
            )
        else:
            if patch.decisions.exists():
                raise GraphAPIError(
                    status=409,
                    code="patch_already_decided",
                    message="The pending graph patch already contains operation decisions.",
                )
            decided_at = timezone.now()
            GraphPatchOperationDecision.objects.bulk_create(
                [
                    GraphPatchOperationDecision(
                        patch=patch,
                        canvas_id=patch.canvas_id,
                        operation_index=index,
                        decision=PatchDecision.REJECTED,
                        reason="user_rejected",
                        actor_type=DIRECT_ACTOR_TYPE,
                        decided_at=decided_at,
                    )
                    for index in range(len(operations))
                ]
            )
            patch.status = PatchStatus.REJECTED
            patch.decided_at = decided_at
            patch.save(update_fields=["status", "decided_at"])
            result = ServiceResult({"patch": serialize_graph_patch(patch)}, 200)
            rejected_now = True

    emit_telemetry(
        "patch.rejected" if rejected_now else "patch.reject_replayed",
        patch_id=patch.id,
        run_id=patch.run_id,
        canvas_id=patch.canvas_id,
        operation_count=len(operations),
    )
    return result


def _linked_instruction(run: GenerationRun) -> str | None:
    manifest = run.context_manifest if isinstance(run.context_manifest, dict) else {}
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    value = request.get("instruction")
    return value if isinstance(value, str) else None


def _regeneration_conflict(patch: GraphPatch, *, reason: str, message: str) -> None:
    emit_telemetry(
        "patch.regeneration_conflict",
        patch_id=patch.id,
        run_id=patch.run_id,
        canvas_id=patch.canvas_id,
        reason=reason,
    )
    raise GraphAPIError(
        status=409,
        code="patch_regeneration_conflict",
        message=message,
        details={"reason": reason, "status": patch.status},
    )


def regenerate_graph_patch(
    patch_id: uuid.UUID,
    request: PatchRegenerationRequest,
    *,
    demo_session_id: uuid.UUID | None = None,
) -> ServiceResult:
    replayed = False
    with transaction.atomic():
        demo_session = None
        if demo_session_id is not None:
            demo_session = (
                DemoSession.objects.select_for_update().filter(pk=demo_session_id).first()
            )
            if demo_session is None or demo_session.expires_at <= timezone.now():
                raise GraphAPIError(
                    status=401,
                    code="demo_session_expired",
                    message="This demo session expired. Reload to start a fresh isolated session.",
                )
        patch = (
            GraphPatch.objects.select_for_update(of=("self",))
            .select_related("run", "regenerated_by_run")
            .filter(pk=patch_id)
            .first()
        )
        if patch is None:
            emit_telemetry("patch.regeneration_requested", patch_id=patch_id)
            raise GraphAPIError(
                status=404,
                code="graph_patch_not_found",
                message="Graph patch not found.",
            )
        if demo_session is not None and (
            demo_session.active_canvas_id != patch.canvas_id
            or patch.run.demo_session_id != demo_session.id
        ):
            raise GraphAPIError(
                status=404,
                code="resource_not_found",
                message="The requested resource was not found.",
            )

        emit_telemetry(
            "patch.regeneration_requested",
            patch_id=patch.id,
            original_run_id=patch.run_id,
            canvas_id=patch.canvas_id,
        )

        linked_run = patch.regenerated_by_run
        if linked_run is not None:
            if (
                linked_run.idempotency_key == request.idempotency_key
                and _linked_instruction(linked_run) == request.instruction
            ):
                result = ServiceResult(
                    {
                        "patch": serialize_graph_patch(patch),
                        "regeneration_run": serialize_generation_run(linked_run),
                    },
                    202,
                )
                replayed = True
            else:
                _regeneration_conflict(
                    patch,
                    reason="idempotency_key_conflict",
                    message="The patch regeneration request does not match the linked run.",
                )
        elif patch.status != PatchStatus.PENDING:
            _regeneration_conflict(
                patch,
                reason="patch_not_pending",
                message="Only a pending graph patch can be regenerated.",
            )
        else:
            operations = _patch_operations(patch)
            if patch.decisions.exists():
                _regeneration_conflict(
                    patch,
                    reason="patch_already_decided",
                    message="The pending graph patch already contains operation decisions.",
                )

            canvas = Canvas.objects.select_for_update().filter(pk=patch.canvas_id).first()
            if canvas is None:
                _regeneration_conflict(
                    patch,
                    reason="canvas_missing",
                    message="The patch canvas no longer exists.",
                )

            original_run = patch.run
            try:
                selected_ids = [uuid.UUID(value) for value in original_run.selected_node_ids]
            except (TypeError, ValueError, AttributeError):
                _regeneration_conflict(
                    patch,
                    reason="invalid_original_selection",
                    message="The original generation selection cannot be reconstructed.",
                )
            selected_nodes = list(
                Node.objects.select_for_update()
                .filter(canvas=canvas, id__in=selected_ids)
                .order_by("id")
            )
            if len(selected_nodes) != len(selected_ids):
                _regeneration_conflict(
                    patch,
                    reason="selected_entity_missing",
                    message="An entity selected by the original run no longer exists.",
                )

            configuration_payload = original_run.execution_configuration
            profile_id = (
                configuration_payload.get("profile_id")
                if isinstance(configuration_payload, dict)
                else None
            )
            if not isinstance(profile_id, str) or not profile_id:
                _regeneration_conflict(
                    patch,
                    reason="execution_profile_missing",
                    message="The original execution profile cannot be reconstructed.",
                )
            if demo_session is not None:
                validate_demo_profile(demo_session, profile_id)
            manifest = (
                original_run.context_manifest
                if isinstance(original_run.context_manifest, dict)
                else {}
            )
            manifest_request = (
                manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
            )
            regeneration_scope = manifest_request.get("regeneration_scope")
            try:
                current_request = GenerationRunRequest(
                    operation=original_run.operation,
                    selected_node_ids=[node.id for node in selected_nodes],
                    expected_node_versions={node.id: node.version for node in selected_nodes},
                    instruction=request.instruction,
                    execution_profile_id=profile_id,
                    idempotency_key=request.idempotency_key,
                    regeneration_scope=regeneration_scope,
                )
            except ValidationError:
                _regeneration_conflict(
                    patch,
                    reason="invalid_original_selection",
                    message="The original generation request cannot be reconstructed.",
                )
            composition = get_composition()
            try:
                configuration = composition.profile_resolver.resolve(
                    profile_id,
                    product_request=True,
                )
                validate_explicit_selection(current_request, selected_nodes)
                context = composition.context_factory.build(
                    canvas=canvas,
                    request=current_request,
                    selected_nodes=selected_nodes,
                )
            except GraphAPIError as error:
                _regeneration_conflict(
                    patch,
                    reason=error.code,
                    message=(
                        "The original generation inputs or execution profile are no longer valid."
                    ),
                )

            if GenerationRun.objects.filter(
                canvas=canvas,
                idempotency_key=request.idempotency_key,
            ).exists():
                _regeneration_conflict(
                    patch,
                    reason="idempotency_key_conflict",
                    message="The idempotency key was already used for another generation run.",
                )

            events_after_sequence = CanvasEventCursor.objects.get(canvas=canvas).last_sequence
            if demo_session is not None:
                if profile_id == "demo_hybrid_v1":
                    consume_hybrid_quota(demo_session)
                else:
                    emit_replay_selected(demo_session)
            linked_run = GenerationRun.objects.create(
                canvas=canvas,
                demo_session=demo_session,
                operation=current_request.operation,
                idempotency_key=current_request.idempotency_key,
                request_fingerprint=generation_request_fingerprint(current_request),
                status=RunStatus.QUEUED,
                base_canvas_revision=canvas.revision,
                context_snapshot=context.snapshot,
                context_manifest=context.manifest,
                context_hash=context.context_hash,
                events_after_sequence=events_after_sequence,
                selected_node_ids=sorted(str(node.id) for node in selected_nodes),
                expected_node_versions={
                    str(node.id): node.version
                    for node in sorted(selected_nodes, key=lambda item: item.id)
                },
                execution_configuration=configuration.model_dump(mode="json"),
            )
            decided_at = timezone.now()
            GraphPatchOperationDecision.objects.bulk_create(
                [
                    GraphPatchOperationDecision(
                        patch=patch,
                        canvas_id=patch.canvas_id,
                        operation_index=index,
                        decision=PatchDecision.REJECTED,
                        reason="regeneration_requested",
                        actor_type=DIRECT_ACTOR_TYPE,
                        decided_at=decided_at,
                    )
                    for index in range(len(operations))
                ]
            )
            patch.status = PatchStatus.REJECTED
            patch.regenerated_by_run = linked_run
            patch.decided_at = decided_at
            patch.save(update_fields=["status", "regenerated_by_run", "decided_at"])
            result = ServiceResult(
                {
                    "patch": serialize_graph_patch(patch),
                    "regeneration_run": serialize_generation_run(linked_run),
                },
                202,
            )

    if replayed:
        emit_telemetry(
            "patch.regeneration_replayed",
            patch_id=patch.id,
            run_id=linked_run.id,
            canvas_id=patch.canvas_id,
        )
    else:
        emit_telemetry(
            "patch.regeneration_linked",
            patch_id=patch.id,
            original_run_id=patch.run_id,
            run_id=linked_run.id,
            canvas_id=patch.canvas_id,
            operation_count=len(operations),
        )
    return result
