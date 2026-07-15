from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from proofgraph.generation.models import RunOperation
from proofgraph.generation.schemas import GenerationRunRequest, RunContextEnvelope
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, Edge, EdgeKind, Node, NodeKind

GENERATED_KINDS = {
    NodeKind.STRATEGY,
    NodeKind.CLAIM,
    NodeKind.OPPORTUNITY,
    NodeKind.ASSUMPTION,
    NodeKind.RISK,
    NodeKind.VALIDATION_EXPERIMENT,
}
OPPORTUNITY_FAMILY_KINDS = {
    NodeKind.OPPORTUNITY,
    NodeKind.ASSUMPTION,
    NodeKind.RISK,
    NodeKind.VALIDATION_EXPERIMENT,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _selection_error(message: str, **details: Any) -> GraphAPIError:
    return GraphAPIError(
        status=422,
        code="invalid_generation_selection",
        message=message,
        details=details,
    )


def _is_applied(node: Node) -> bool:
    metadata = node.metadata
    review_status = metadata.get("review_status")
    if metadata.get("provisional") is True or review_status in {"provisional", "rejected"}:
        return False
    generated = any(
        metadata.get(key)
        for key in ("generated", "generated_by_run_id", "generation_run_id", "source_patch_id")
    )
    return not generated or review_status == "accepted"


def _is_manually_authored(node: Node) -> bool:
    metadata = node.metadata
    return not any(
        metadata.get(key)
        for key in ("generated", "generated_by_run_id", "generation_run_id", "source_patch_id")
    )


def _is_eligible_context(node: Node) -> bool:
    return _is_applied(node) and not node.stale


def validate_explicit_selection(
    request: GenerationRunRequest,
    selected_nodes: list[Node],
) -> None:
    if len(selected_nodes) != len(request.selected_node_ids):
        found = {node.id for node in selected_nodes}
        missing = sorted(str(node_id) for node_id in set(request.selected_node_ids) - found)
        raise _selection_error(
            "Every selected node must belong to the canvas.", missing_node_ids=missing
        )

    mismatches = [
        {
            "node_id": str(node.id),
            "expected": request.expected_node_versions[node.id],
            "actual": node.version,
        }
        for node in selected_nodes
        if request.expected_node_versions[node.id] != node.version
    ]
    if mismatches:
        raise _selection_error("One or more selected node versions changed.", mismatches=mismatches)

    by_kind: dict[str, list[Node]] = {}
    for node in selected_nodes:
        by_kind.setdefault(node.kind, []).append(node)

    if request.operation == RunOperation.GENERATE_STRATEGIES:
        goals = by_kind.get(NodeKind.GOAL, [])
        constraints = by_kind.get(NodeKind.CONSTRAINT, [])
        if (
            len(goals) != 1
            or not constraints
            or len(goals) + len(constraints) != len(selected_nodes)
        ):
            raise _selection_error(
                "Strategy generation requires one goal and at least one constraint."
            )
        if any(
            node.stale or not _is_applied(node) or not _is_manually_authored(node)
            for node in selected_nodes
        ):
            raise _selection_error(
                "Strategy generation accepts only fresh, applied, manually authored nodes."
            )
        return

    if request.operation == RunOperation.RESEARCH_EVIDENCE:
        if len(selected_nodes) != 1 or selected_nodes[0].kind != NodeKind.STRATEGY:
            raise _selection_error("Evidence research requires exactly one strategy.")
        node = selected_nodes[0]
        if node.stale or not _is_applied(node):
            raise _selection_error("The selected strategy must be fresh and applied.")
        return

    if request.operation == RunOperation.SYNTHESIZE_OPPORTUNITIES:
        strategies = by_kind.get(NodeKind.STRATEGY, [])
        claims = by_kind.get(NodeKind.CLAIM, [])
        if (
            len(strategies) != 1
            or not claims
            or len(strategies) + len(claims) != len(selected_nodes)
        ):
            raise _selection_error(
                "Opportunity synthesis requires one strategy and at least one claim."
            )
        if any(node.stale or not _is_applied(node) for node in selected_nodes):
            raise _selection_error("Opportunity synthesis accepts only fresh, applied selections.")
        return

    if request.operation == RunOperation.REGENERATE_STALE:
        if len(selected_nodes) != 1:
            raise _selection_error("Stale regeneration requires exactly one node.")
        node = selected_nodes[0]
        if node.kind not in GENERATED_KINDS or not node.stale or not _is_applied(node):
            raise _selection_error("Stale regeneration requires one applied stale generated node.")
        return

    raise _selection_error("Unsupported generation operation.")


CONTEXT_REPRESENTATION_VERSION = 1
MODEL_INPUT_LIMIT = 128_000
MODEL_RESPONSE_BUDGET = 16_000
FIXED_SERIALIZATION_RESERVE = 16_000
CONTRADICTION_RESERVE_FRACTION = 0.25
CONTEXT_BUDGET = {
    "selected_nodes": 0.30,
    "global_constraints": 0.15,
    "provenance": 0.20,
    "evidence": 0.20,
    "descendants": 0.10,
    "related_summary": 0.05,
}

_UI_METADATA_KEYS = {
    "collapsed",
    "color",
    "expanded",
    "height",
    "layout",
    "position",
    "selected",
    "style",
    "ui_state",
    "viewport",
    "width",
    "x",
    "y",
}
_EDGE_RELEVANCE = {
    EdgeKind.CONTRADICTS: 7,
    EdgeKind.EXTRACTED_FROM: 6,
    EdgeKind.SUPPORTS: 5,
    EdgeKind.CONSTRAINED_BY: 4,
    EdgeKind.DERIVED_FROM: 3,
    EdgeKind.EVOLVES_INTO: 2,
    EdgeKind.REQUIRES_VALIDATION: 1,
}
_STRENGTH_RANK = {"strong": 3, "medium": 2, "weak": 1}


@dataclass(frozen=True)
class TraversalRank:
    distance: int
    edge_relevance: int


class CanonicalTokenCounter:
    """A deterministic upper bound: a normal tokenizer cannot emit more tokens than bytes."""

    identity = "utf8_upper_bound_v1"

    def count(self, value: Any) -> int:
        return len(canonical_json(value).encode("utf-8"))


def _dependency_pair(edge: Edge) -> tuple[uuid.UUID, uuid.UUID]:
    if edge.kind in {EdgeKind.CONSTRAINED_BY, EdgeKind.EXTRACTED_FROM}:
        return edge.target_id, edge.source_id
    return edge.source_id, edge.target_id


def _opportunity_family_owner(
    node: Node,
    *,
    edges: list[Edge],
    node_by_id: dict[uuid.UUID, Node],
) -> Node | None:
    if node.kind == NodeKind.OPPORTUNITY:
        return node
    for edge in edges:
        owner_id: uuid.UUID | None = None
        if edge.target_id == node.id and (
            (
                node.kind in {NodeKind.ASSUMPTION, NodeKind.RISK}
                and edge.kind == EdgeKind.DERIVED_FROM
            )
            or (
                node.kind == NodeKind.VALIDATION_EXPERIMENT
                and edge.kind == EdgeKind.REQUIRES_VALIDATION
            )
        ):
            owner_id = edge.source_id
        owner = node_by_id.get(owner_id) if owner_id is not None else None
        if owner is not None and owner.kind == NodeKind.OPPORTUNITY:
            return owner
    return None


def _opportunity_family_members(
    owner: Node,
    *,
    all_nodes: list[Node],
    edges: list[Edge],
    node_by_id: dict[uuid.UUID, Node],
) -> list[Node]:
    members = {owner.id: owner}
    for node in all_nodes:
        if node.kind not in OPPORTUNITY_FAMILY_KINDS - {NodeKind.OPPORTUNITY}:
            continue
        if _opportunity_family_owner(node, edges=edges, node_by_id=node_by_id) == owner:
            members[node.id] = node
    return sorted(members.values(), key=lambda member: str(member.id))


def _traverse(
    roots: list[uuid.UUID],
    adjacency: dict[uuid.UUID, list[tuple[uuid.UUID, str]]],
) -> dict[uuid.UUID, TraversalRank]:
    ranks = {node_id: TraversalRank(distance=0, edge_relevance=99) for node_id in roots}
    queue = deque(sorted(roots, key=str))
    while queue:
        current = queue.popleft()
        current_rank = ranks[current]
        neighbors = sorted(
            adjacency.get(current, ()),
            key=lambda value: (-_EDGE_RELEVANCE[value[1]], str(value[0])),
        )
        for neighbor, edge_kind in neighbors:
            candidate = TraversalRank(
                distance=current_rank.distance + 1,
                edge_relevance=_EDGE_RELEVANCE[edge_kind],
            )
            existing = ranks.get(neighbor)
            if existing is not None and (
                existing.distance < candidate.distance
                or (
                    existing.distance == candidate.distance
                    and existing.edge_relevance >= candidate.edge_relevance
                )
            ):
                continue
            ranks[neighbor] = candidate
            queue.append(neighbor)
    return ranks


def _filter_semantic_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in sorted(metadata.items()) if key not in _UI_METADATA_KEYS}


def _semantic_node(node: Node) -> dict[str, Any]:
    semantic = {
        "id": str(node.id),
        "kind": node.kind,
        "title": node.title,
        "metadata": _filter_semantic_metadata(node.metadata),
        "branch_root_node_id": str(node.branch_root_id) if node.branch_root_id else None,
        "stale": node.stale,
        "version": node.version,
    }
    if node.kind == NodeKind.SOURCE:
        if node.body is not None:
            semantic["sanitized_excerpt"] = node.body
    else:
        semantic["body"] = node.body
    return semantic


def _semantic_recency_key(node: Node) -> tuple[float, str]:
    return (-node.semantic_updated_at.timestamp(), str(node.id))


def _authority_rank(node: Node) -> int:
    authority = node.metadata.get("authority")
    if isinstance(authority, dict):
        value = authority.get("hierarchy_rank")
        if isinstance(value, int):
            return value
    value = node.metadata.get("authority_rank")
    return value if isinstance(value, int) else 7


def _independent_support_count(
    node: Node,
    *,
    edges: list[Edge],
    node_by_id: dict[uuid.UUID, Node],
) -> int:
    if node.kind == NodeKind.CLAIM:
        keys = {
            source.metadata.get("independence_key")
            for edge in edges
            if edge.kind == EdgeKind.EXTRACTED_FROM and edge.source_id == node.id
            for source in [node_by_id.get(edge.target_id)]
            if source is not None
            and source.kind == NodeKind.SOURCE
            and isinstance(source.metadata.get("independence_key"), str)
        }
        if keys:
            return len(keys)
        metadata_keys = node.metadata.get("independence_keys")
        if isinstance(metadata_keys, list):
            return len({value for value in metadata_keys if isinstance(value, str)})
    if node.kind == NodeKind.SOURCE and isinstance(node.metadata.get("independence_key"), str):
        return 1
    value = node.metadata.get("independent_support_count")
    return value if isinstance(value, int) else 0


def _is_contradicting(node: Node) -> bool:
    return node.metadata.get("classification") == "contradicting"


class GraphRunContextFactory:
    def __init__(
        self,
        *,
        hard_input_limit: int = MODEL_INPUT_LIMIT,
        response_budget: int = MODEL_RESPONSE_BUDGET,
        fixed_reserve: int = FIXED_SERIALIZATION_RESERVE,
        token_counter: CanonicalTokenCounter | None = None,
    ) -> None:
        if hard_input_limit <= response_budget + fixed_reserve:
            raise ValueError("the hard input limit must exceed all fixed reserves")
        self.hard_input_limit = hard_input_limit
        self.response_budget = response_budget
        self.fixed_reserve = fixed_reserve
        self.token_counter = token_counter or CanonicalTokenCounter()

    @property
    def semantic_budget(self) -> int:
        return self.hard_input_limit - self.response_budget - self.fixed_reserve

    def _precompute_token_counts(self, nodes: list[Node]) -> dict[uuid.UUID, int]:
        counts: dict[uuid.UUID, int] = {}
        changed: list[Node] = []
        for node in nodes:
            representation = _semantic_node(node)
            content_hash = sha256_json(representation)
            if (
                node.context_representation_version != CONTEXT_REPRESENTATION_VERSION
                or node.context_content_hash != content_hash
                or node.context_token_count is None
            ):
                node.context_representation_version = CONTEXT_REPRESENTATION_VERSION
                node.context_content_hash = content_hash
                node.context_token_count = self.token_counter.count(representation)
                changed.append(node)
            counts[node.id] = node.context_token_count
        if changed:
            Node.objects.bulk_update(
                changed,
                [
                    "context_representation_version",
                    "context_content_hash",
                    "context_token_count",
                ],
            )
        return counts

    def _raise_context_too_large(self, *, required: int, phase: str) -> None:
        raise GraphAPIError(
            status=422,
            code="context_too_large",
            message="The mandatory semantic context exceeds the model input budget.",
            details={
                "phase": phase,
                "required_upper_bound_tokens": required,
                "hard_input_limit": self.hard_input_limit,
                "response_budget": self.response_budget,
                "fixed_reserve": self.fixed_reserve,
                "counter": self.token_counter.identity,
            },
        )

    def build(
        self,
        *,
        canvas: Canvas,
        request: GenerationRunRequest,
        selected_nodes: list[Node],
    ) -> RunContextEnvelope:
        all_nodes = list(Node.objects.filter(canvas=canvas).order_by("id"))
        node_by_id = {node.id: node for node in all_nodes}
        edges = list(Edge.objects.filter(canvas=canvas).order_by("id"))
        token_counts = self._precompute_token_counts(all_nodes)

        forward: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
        reverse: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
        for edge in edges:
            ancestor, descendant = _dependency_pair(edge)
            forward.setdefault(ancestor, []).append((descendant, edge.kind))
            reverse.setdefault(descendant, []).append((ancestor, edge.kind))

        explicit_ids = sorted((node.id for node in selected_nodes), key=str)
        mandatory_ids = set(explicit_ids)
        selected_claim_ids = {node.id for node in selected_nodes if node.kind == NodeKind.CLAIM}
        selected_source_ids = {
            ancestor_id
            for edge in edges
            for ancestor_id, descendant_id in (_dependency_pair(edge),)
            if descendant_id in selected_claim_ids
            and ancestor_id in node_by_id
            and node_by_id[ancestor_id].kind == NodeKind.SOURCE
            and _is_eligible_context(node_by_id[ancestor_id])
        }
        mandatory_ids.update(selected_source_ids)
        ancestor_ranks: dict[uuid.UUID, TraversalRank] = {}
        descendant_ranks: dict[uuid.UUID, TraversalRank] = {}
        if request.operation != RunOperation.GENERATE_STRATEGIES:
            ancestor_ranks = _traverse(explicit_ids, reverse)
            descendant_ranks = _traverse(explicit_ids, forward)

        global_constraints = sorted(
            (
                node
                for node in all_nodes
                if node.kind == NodeKind.CONSTRAINT
                and node.metadata.get("context_scope") == "global"
                and node.metadata.get("pinned") is True
                and _is_eligible_context(node)
            ),
            key=_semantic_recency_key,
        )
        mandatory_ids.update(node.id for node in global_constraints)

        regeneration: dict[str, Any] | None = None
        mandatory_generated_ids = {
            node.id for node in selected_nodes if node.kind in GENERATED_KINDS
        }
        if request.operation == RunOperation.REGENERATE_STALE:
            root = selected_nodes[0]
            workset_ranks = _traverse([root.id], forward)
            if request.regeneration_scope == "branch":
                target_nodes = [
                    node_by_id[node_id]
                    for node_id in workset_ranks
                    if node_id in node_by_id
                    and node_by_id[node_id].stale
                    and node_by_id[node_id].kind in GENERATED_KINDS
                ]
            else:
                workset_ranks = {root.id: TraversalRank(0, 99)}
                target_nodes = [root]
            target_nodes.sort(key=lambda node: (workset_ranks[node.id].distance, str(node.id)))
            production_units: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}
            for node in target_nodes:
                owner = (
                    _opportunity_family_owner(node, edges=edges, node_by_id=node_by_id)
                    if node.kind in OPPORTUNITY_FAMILY_KINDS
                    else None
                )
                unit_node = owner or node
                unit_kind = NodeKind.OPPORTUNITY if owner is not None else node.kind
                unit_key = (unit_kind, unit_node.id)
                unit = production_units.setdefault(
                    unit_key,
                    {
                        "node": unit_node,
                        "kind": unit_kind,
                        "distance": workset_ranks[node.id].distance,
                        "stale_nodes": [],
                        "members": (
                            _opportunity_family_members(
                                unit_node,
                                all_nodes=all_nodes,
                                edges=edges,
                                node_by_id=node_by_id,
                            )
                            if owner is not None or node.kind == NodeKind.OPPORTUNITY
                            else [node]
                        ),
                    },
                )
                unit["distance"] = min(unit["distance"], workset_ranks[node.id].distance)
                unit["stale_nodes"].append(node)
            ordered_units = sorted(
                production_units.values(),
                key=lambda unit: (unit["distance"], str(unit["node"].id)),
            )
            for unit in ordered_units:
                unit["stale_nodes"] = [member for member in unit["members"] if member.stale]
            mandatory_generated_ids = {
                member.id for unit in ordered_units for member in unit["members"]
            }
            mandatory_ids.update(mandatory_generated_ids)
            target_ancestors = _traverse(
                sorted(mandatory_generated_ids, key=str),
                reverse,
            )
            for node_id, rank in target_ancestors.items():
                current = ancestor_ranks.get(node_id)
                if current is None or (rank.distance, -rank.edge_relevance) < (
                    current.distance,
                    -current.edge_relevance,
                ):
                    ancestor_ranks[node_id] = rank
            regeneration = {
                "scope": request.regeneration_scope,
                "root_node_id": str(root.id),
                "targets": [
                    {
                        "node_id": str(unit["node"].id),
                        "kind": unit["kind"],
                        "version": unit["node"].version,
                        "distance": unit["distance"],
                        "branch_anchor_id": (
                            str(unit["node"].branch_root_id)
                            if unit["node"].branch_root_id
                            else None
                        ),
                        "member_node_ids": sorted(str(member.id) for member in unit["members"]),
                        "stale_node_ids": sorted(str(member.id) for member in unit["stale_nodes"]),
                    }
                    for unit in ordered_units
                ],
            }

        branch_constraints: list[Node] = []
        if mandatory_generated_ids:
            for node in all_nodes:
                if (
                    node.kind != NodeKind.CONSTRAINT
                    or node.metadata.get("context_scope") != "branch"
                    or node.metadata.get("pinned") is not True
                    or node.branch_root_id is None
                    or not _is_eligible_context(node)
                ):
                    continue
                reachable = set(_traverse([node.branch_root_id], forward))
                if mandatory_generated_ids <= reachable:
                    branch_constraints.append(node)
            branch_constraints.sort(key=_semantic_recency_key)
            mandatory_ids.update(node.id for node in branch_constraints)

        mandatory_count = sum(token_counts[node_id] for node_id in mandatory_ids)
        if mandatory_count > self.semantic_budget:
            self._raise_context_too_large(required=mandatory_count, phase="mandatory_nodes")

        eligible_ancestor_ids = {
            node_id
            for node_id in ancestor_ranks
            if node_id in node_by_id
            and node_id not in mandatory_ids
            and _is_eligible_context(node_by_id[node_id])
        }
        eligible_descendant_ids = {
            node_id
            for node_id in descendant_ranks
            if node_id in node_by_id
            and node_id not in mandatory_ids
            and _is_eligible_context(node_by_id[node_id])
        }
        evidence_ids = {
            node_id
            for node_id in eligible_ancestor_ids | eligible_descendant_ids
            if node_by_id[node_id].kind in {NodeKind.SOURCE, NodeKind.CLAIM}
        }
        provenance_ids = eligible_ancestor_ids - evidence_ids
        descendant_ids = eligible_descendant_ids - evidence_ids - provenance_ids

        related_ids: set[uuid.UUID] = set()
        for ancestor_id in eligible_ancestor_ids:
            for sibling_id, _edge_kind in forward.get(ancestor_id, ()):
                sibling = node_by_id.get(sibling_id)
                if (
                    sibling is not None
                    and sibling_id not in mandatory_ids
                    and sibling_id not in evidence_ids
                    and sibling_id not in provenance_ids
                    and sibling_id not in descendant_ids
                    and sibling.kind in {NodeKind.OPPORTUNITY, NodeKind.RISK, NodeKind.ASSUMPTION}
                    and _is_eligible_context(sibling)
                ):
                    related_ids.add(sibling_id)

        tier_ids: dict[str, list[uuid.UUID]] = {
            "selected": explicit_ids,
            "constraints": [node.id for node in [*global_constraints, *branch_constraints]],
            "ancestors": [],
            "evidence": sorted(selected_source_ids, key=str),
            "descendants": [],
            "related_summary": [],
        }
        included_ids = set(mandatory_ids)
        optional_order: list[uuid.UUID] = []
        excluded_due_to_budget: set[uuid.UUID] = set()
        remaining_total = self.semantic_budget - mandatory_count

        def pack(
            tier: str,
            candidates: list[uuid.UUID],
            budget: int,
        ) -> int:
            used = 0
            for node_id in candidates:
                count = token_counts[node_id]
                if count <= budget - used:
                    tier_ids[tier].append(node_id)
                    included_ids.add(node_id)
                    optional_order.append(node_id)
                    used += count
                else:
                    excluded_due_to_budget.add(node_id)
            return used

        provenance_ranked = sorted(
            provenance_ids,
            key=lambda node_id: (
                ancestor_ranks[node_id].distance,
                -ancestor_ranks[node_id].edge_relevance,
                *_semantic_recency_key(node_by_id[node_id]),
            ),
        )
        provenance_cap = min(
            int(self.semantic_budget * CONTEXT_BUDGET["provenance"]), remaining_total
        )
        used = pack("ancestors", provenance_ranked, provenance_cap)
        remaining_total -= used

        evidence_ranked = sorted(
            evidence_ids,
            key=lambda node_id: (
                -_STRENGTH_RANK.get(str(node_by_id[node_id].metadata.get("strength")), 0),
                _authority_rank(node_by_id[node_id]),
                -_independent_support_count(
                    node_by_id[node_id],
                    edges=edges,
                    node_by_id=node_by_id,
                ),
                *_semantic_recency_key(node_by_id[node_id]),
            ),
        )
        contradiction_ids = [
            node_id for node_id in evidence_ranked if _is_contradicting(node_by_id[node_id])
        ]
        supporting_ids = [
            node_id for node_id in evidence_ranked if not _is_contradicting(node_by_id[node_id])
        ]
        evidence_cap = min(int(self.semantic_budget * CONTEXT_BUDGET["evidence"]), remaining_total)
        contradiction_cap = int(evidence_cap * CONTRADICTION_RESERVE_FRACTION)
        contradiction_used = pack("evidence", contradiction_ids, contradiction_cap)
        supporting_used = pack(
            "evidence",
            supporting_ids,
            evidence_cap - contradiction_used,
        )
        remaining_total -= contradiction_used + supporting_used

        descendant_ranked = sorted(
            descendant_ids,
            key=lambda node_id: (
                descendant_ranks[node_id].distance,
                -descendant_ranks[node_id].edge_relevance,
                *_semantic_recency_key(node_by_id[node_id]),
            ),
        )
        descendant_cap = min(
            int(self.semantic_budget * CONTEXT_BUDGET["descendants"]), remaining_total
        )
        used = pack("descendants", descendant_ranked, descendant_cap)
        remaining_total -= used

        related_ranked = sorted(
            related_ids, key=lambda node_id: _semantic_recency_key(node_by_id[node_id])
        )
        related_cap = min(
            int(self.semantic_budget * CONTEXT_BUDGET["related_summary"]), remaining_total
        )
        pack("related_summary", related_ranked, related_cap)

        def assemble() -> tuple[dict[str, Any], dict[str, Any], int]:
            frozen_nodes = sorted(
                (node_by_id[node_id] for node_id in included_ids),
                key=lambda node: str(node.id),
            )
            included_edges = [
                edge
                for edge in edges
                if edge.source_id in included_ids and edge.target_id in included_ids
            ]
            included_edge_ids = {edge.id for edge in included_edges}
            excluded_node_ids = set(node_by_id) - included_ids
            excluded_edge_ids = {edge.id for edge in edges} - included_edge_ids
            snapshot = {
                "canvas_id": str(canvas.id),
                "nodes": [_semantic_node(node) for node in frozen_nodes],
                "edges": [
                    {
                        "id": str(edge.id),
                        "kind": edge.kind,
                        "source_node_id": str(edge.source_id),
                        "target_node_id": str(edge.target_id),
                        "metadata": _filter_semantic_metadata(edge.metadata),
                        "version": edge.version,
                    }
                    for edge in included_edges
                ],
            }
            manifest = {
                "request": {
                    "operation": request.operation,
                    "instruction": request.instruction,
                    "regeneration_scope": request.regeneration_scope,
                },
                "explicit_node_ids": [str(node_id) for node_id in explicit_ids],
                "selected_source_provenance_ids": sorted(
                    str(node_id) for node_id in selected_source_ids
                ),
                "included_node_ids": sorted(str(node_id) for node_id in included_ids),
                "excluded_node_ids": sorted(str(node_id) for node_id in excluded_node_ids),
                "included_edge_ids": sorted(str(edge_id) for edge_id in included_edge_ids),
                "excluded_edge_ids": sorted(str(edge_id) for edge_id in excluded_edge_ids),
                "selected": [str(node_id) for node_id in tier_ids["selected"]],
                "constraints": [str(node_id) for node_id in tier_ids["constraints"]],
                "ancestors": [str(node_id) for node_id in tier_ids["ancestors"]],
                "evidence": [str(node_id) for node_id in tier_ids["evidence"]],
                "descendants": [str(node_id) for node_id in tier_ids["descendants"]],
                "related_summary": [str(node_id) for node_id in tier_ids["related_summary"]],
                "excluded_due_to_budget": sorted(
                    str(node_id) for node_id in excluded_due_to_budget
                ),
                "excluded_out_of_scope": sorted(
                    str(node_id) for node_id in excluded_node_ids - excluded_due_to_budget
                ),
                "node_versions": {str(node.id): node.version for node in frozen_nodes},
                "ancestor_distances": {
                    str(node_id): rank.distance
                    for node_id, rank in sorted(
                        ancestor_ranks.items(), key=lambda item: str(item[0])
                    )
                    if node_id in included_ids
                },
                "branch_constraint_anchors": {
                    str(node.id): str(node.branch_root_id) for node in branch_constraints
                },
                "regeneration": regeneration,
                "budget": {
                    "hard_input_limit": self.hard_input_limit,
                    "response_budget": self.response_budget,
                    "fixed_reserve": self.fixed_reserve,
                    "semantic_budget": self.semantic_budget,
                    "counter": self.token_counter.identity,
                    "context_representation_version": CONTEXT_REPRESENTATION_VERSION,
                },
            }
            canonical = {"snapshot": snapshot, "manifest": manifest}
            total = self.token_counter.count(canonical) + self.response_budget + self.fixed_reserve
            return snapshot, manifest, total

        snapshot, manifest, total = assemble()
        while total > self.hard_input_limit and optional_order:
            removed = optional_order.pop()
            included_ids.remove(removed)
            excluded_due_to_budget.add(removed)
            for values in tier_ids.values():
                if removed in values:
                    values.remove(removed)
            snapshot, manifest, total = assemble()
        if total > self.hard_input_limit:
            self._raise_context_too_large(required=total, phase="serialized_request")

        canonical = {"snapshot": snapshot, "manifest": manifest}
        return RunContextEnvelope(
            snapshot=snapshot,
            manifest=manifest,
            context_hash=sha256_json(canonical),
            included_node_ids=tuple(sorted(included_ids, key=str)),
            node_versions=manifest["node_versions"],
        )
