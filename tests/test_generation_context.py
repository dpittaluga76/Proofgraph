from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from proofgraph.generation.context import (
    CanonicalTokenCounter,
    GraphRunContextFactory,
    _independent_support_count,
)
from proofgraph.generation.schemas import GenerationRunRequest
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

pytestmark = pytest.mark.django_db


def request_for(
    operation: str,
    nodes: list[Node],
) -> GenerationRunRequest:
    return GenerationRunRequest.model_validate(
        {
            "operation": operation,
            "selected_node_ids": [node.id for node in nodes],
            "expected_node_versions": {node.id: node.version for node in nodes},
            "execution_profile_id": "replay_v1",
            "idempotency_key": f"context-{uuid.uuid4()}",
        }
    )


def test_operation_context_follows_dependency_direction_and_branch_anchors() -> None:
    canvas = Canvas.objects.create(title="Directed context")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Goal")
    strategy = Node.objects.create(canvas=canvas, kind=NodeKind.STRATEGY, title="Strategy")
    claim = Node.objects.create(canvas=canvas, kind=NodeKind.CLAIM, title="Selected claim")
    source = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Source",
        body="A sanitized excerpt.",
    )
    risk = Node.objects.create(canvas=canvas, kind=NodeKind.RISK, title="Risk")
    unrelated = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.OPPORTUNITY,
        title="Unrelated branch",
    )
    other_strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Other strategy",
    )
    global_constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Global constraint",
        metadata={"context_scope": "global", "pinned": True},
    )
    included_branch_constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Included branch constraint",
        metadata={"context_scope": "branch", "pinned": True},
        branch_root=strategy,
    )
    excluded_branch_constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Excluded branch constraint",
        metadata={"context_scope": "branch", "pinned": True},
        branch_root=other_strategy,
    )
    Edge.objects.bulk_create(
        [
            Edge(canvas=canvas, source=goal, target=strategy, kind=EdgeKind.DERIVED_FROM),
            Edge(canvas=canvas, source=strategy, target=claim, kind=EdgeKind.DERIVED_FROM),
            # Stored claim -> source; dependency direction is source -> claim.
            Edge(canvas=canvas, source=claim, target=source, kind=EdgeKind.EXTRACTED_FROM),
            Edge(canvas=canvas, source=claim, target=risk, kind=EdgeKind.DERIVED_FROM),
            # An accidental cycle must not alter or prevent deterministic traversal.
            Edge(canvas=canvas, source=risk, target=claim, kind=EdgeKind.DERIVED_FROM),
        ]
    )

    context = GraphRunContextFactory().build(
        canvas=canvas,
        request=request_for("synthesize_opportunities", [strategy, claim]),
        selected_nodes=[strategy, claim],
    )
    included = set(context.manifest["included_node_ids"])

    assert str(goal.id) in included
    assert str(source.id) in included
    assert str(risk.id) in included
    assert str(global_constraint.id) in included
    assert str(included_branch_constraint.id) in included
    assert str(excluded_branch_constraint.id) not in included
    assert str(unrelated.id) not in included
    assert str(unrelated.id) in context.manifest["excluded_out_of_scope"]
    assert context.manifest["ancestor_distances"][str(source.id)] == 1


def test_layout_and_ui_state_do_not_change_context_hash_or_token_cache() -> None:
    canvas = Canvas.objects.create(title="Layout independence")
    goal = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.GOAL,
        title="Goal",
        metadata={"business_model": "subscription", "style": {"color": "red"}},
        position={"x": 1, "y": 2},
    )
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Constraint",
        metadata={"context_scope": "global", "pinned": True, "selected": True},
    )
    request = request_for("generate_strategies", [goal, constraint])
    factory = GraphRunContextFactory()

    before = factory.build(canvas=canvas, request=request, selected_nodes=[goal, constraint])
    goal.refresh_from_db()
    cached_count = goal.context_token_count
    cached_hash = goal.context_content_hash
    semantic_updated_at = goal.semantic_updated_at

    goal.position = {"x": 900, "y": -30}
    goal.position_version += 1
    goal.position_updated_at = timezone.now()
    goal.metadata = {**goal.metadata, "style": {"color": "blue"}}
    goal.save(update_fields=["position", "position_version", "position_updated_at", "metadata"])
    after = factory.build(canvas=canvas, request=request, selected_nodes=[goal, constraint])
    goal.refresh_from_db()

    assert after.context_hash == before.context_hash
    assert after.manifest == before.manifest
    assert goal.context_token_count == cached_count
    assert goal.context_content_hash == cached_hash
    assert goal.semantic_updated_at == semantic_updated_at
    serialized = str(after.snapshot)
    assert "position" not in serialized
    assert "style" not in serialized
    assert "selected" not in serialized


def test_contradicting_evidence_is_reserved_before_stable_support_packing() -> None:
    canvas = Canvas.objects.create(title="Evidence budget")
    fixed_time = timezone.now()
    strategy = Node.objects.create(
        id=uuid.UUID(int=1),
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Strategy",
        semantic_updated_at=fixed_time,
    )
    contradiction = Node.objects.create(
        id=uuid.UUID(int=2),
        canvas=canvas,
        kind=NodeKind.CLAIM,
        title="Contradiction",
        body="Material contradictory evidence.",
        metadata={"classification": "contradicting", "strength": "strong"},
        semantic_updated_at=fixed_time,
    )
    first_support = Node.objects.create(
        id=uuid.UUID(int=3),
        canvas=canvas,
        kind=NodeKind.CLAIM,
        title="First support",
        body="x" * 650,
        metadata={"classification": "observed", "strength": "strong"},
        semantic_updated_at=fixed_time,
    )
    second_support = Node.objects.create(
        id=uuid.UUID(int=4),
        canvas=canvas,
        kind=NodeKind.CLAIM,
        title="Second support",
        body="x" * 650,
        metadata={"classification": "observed", "strength": "strong"},
        semantic_updated_at=fixed_time,
    )
    Edge.objects.bulk_create(
        [
            Edge(
                canvas=canvas,
                source=strategy,
                target=contradiction,
                kind=EdgeKind.DERIVED_FROM,
            ),
            Edge(
                canvas=canvas,
                source=strategy,
                target=first_support,
                kind=EdgeKind.DERIVED_FROM,
            ),
            Edge(
                canvas=canvas,
                source=strategy,
                target=second_support,
                kind=EdgeKind.DERIVED_FROM,
            ),
        ]
    )
    factory = GraphRunContextFactory(
        hard_input_limit=8_000,
        response_budget=1_000,
        fixed_reserve=1_000,
    )

    first = factory.build(
        canvas=canvas,
        request=request_for("research_evidence", [strategy]),
        selected_nodes=[strategy],
    )
    second = factory.build(
        canvas=canvas,
        request=request_for("research_evidence", [strategy]),
        selected_nodes=[strategy],
    )

    assert first.manifest["evidence"][0] == str(contradiction.id)
    assert str(first_support.id) in first.manifest["evidence"]
    assert str(second_support.id) in first.manifest["excluded_due_to_budget"]
    assert first.manifest["evidence"] == second.manifest["evidence"]
    assert first.context_hash == second.context_hash


def test_mandatory_overflow_fails_before_queueing_with_context_too_large() -> None:
    canvas = Canvas.objects.create(title="Mandatory overflow")
    goal = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.GOAL,
        title="Goal",
        body="x" * 2_000,
    )
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Constraint",
        body="y" * 2_000,
        metadata={"context_scope": "global", "pinned": False},
    )
    factory = GraphRunContextFactory(
        hard_input_limit=1_000,
        response_budget=100,
        fixed_reserve=100,
    )

    with pytest.raises(GraphAPIError) as captured:
        factory.build(
            canvas=canvas,
            request=request_for("generate_strategies", [goal, constraint]),
            selected_nodes=[goal, constraint],
        )

    assert captured.value.status == 422
    assert captured.value.code == "context_too_large"
    assert captured.value.details["phase"] == "mandatory_nodes"


def test_fully_serialized_context_stays_within_the_hard_limit() -> None:
    canvas = Canvas.objects.create(title="Serialized cap")
    strategy = Node.objects.create(canvas=canvas, kind=NodeKind.STRATEGY, title="Strategy")
    for index in range(12):
        claim = Node.objects.create(
            canvas=canvas,
            kind=NodeKind.CLAIM,
            title=f"Claim {index}",
            body="evidence" * 80,
        )
        Edge.objects.create(
            canvas=canvas,
            source=strategy,
            target=claim,
            kind=EdgeKind.DERIVED_FROM,
        )
    factory = GraphRunContextFactory(
        hard_input_limit=8_000,
        response_budget=1_000,
        fixed_reserve=1_000,
    )
    context = factory.build(
        canvas=canvas,
        request=request_for("research_evidence", [strategy]),
        selected_nodes=[strategy],
    )
    counter = CanonicalTokenCounter()
    serialized_upper_bound = counter.count(
        {"snapshot": context.snapshot, "manifest": context.manifest}
    )

    assert serialized_upper_bound + 2_000 <= 8_000
    assert context.manifest["excluded_due_to_budget"]


def test_selected_claim_source_provenance_is_mandatory_under_budget_pressure() -> None:
    canvas = Canvas.objects.create(title="Mandatory selected provenance")
    strategy = Node.objects.create(canvas=canvas, kind=NodeKind.STRATEGY, title="Strategy")
    claim = Node.objects.create(canvas=canvas, kind=NodeKind.CLAIM, title="Selected claim")
    source = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Accepted source",
        body="A bounded accepted source excerpt.",
        metadata={
            "review_status": "accepted",
            "independence_key": "publisher:accepted.example",
        },
    )
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=source,
        kind=EdgeKind.EXTRACTED_FROM,
    )
    for index in range(8):
        optional = Node.objects.create(
            canvas=canvas,
            kind=NodeKind.RISK,
            title=f"Optional risk {index}",
            body="optional" * 120,
        )
        Edge.objects.create(
            canvas=canvas,
            source=claim,
            target=optional,
            kind=EdgeKind.DERIVED_FROM,
        )

    context = GraphRunContextFactory(
        hard_input_limit=6_000,
        response_budget=500,
        fixed_reserve=500,
    ).build(
        canvas=canvas,
        request=request_for("synthesize_opportunities", [strategy, claim]),
        selected_nodes=[strategy, claim],
    )

    assert str(source.id) in context.manifest["included_node_ids"]
    assert context.manifest["selected_source_provenance_ids"] == [str(source.id)]
    assert context.manifest["excluded_due_to_budget"]


def test_independent_support_count_comes_from_accepted_source_relations() -> None:
    canvas = Canvas.objects.create(title="Independent evidence ranking")
    claim = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CLAIM,
        title="Claim",
        metadata={"independent_support_count": 99},
    )
    sources = [
        Node.objects.create(
            canvas=canvas,
            kind=NodeKind.SOURCE,
            title=f"Source {index}",
            metadata={"independence_key": independence_key},
        )
        for index, independence_key in enumerate(
            ("publisher:one.example", "publisher:two.example", "publisher:two.example")
        )
    ]
    edges = [
        Edge.objects.create(
            canvas=canvas,
            source=claim,
            target=source,
            kind=EdgeKind.EXTRACTED_FROM,
        )
        for source in sources
    ]
    node_by_id = {node.id: node for node in [claim, *sources]}

    assert _independent_support_count(claim, edges=edges, node_by_id=node_by_id) == 2


def test_regeneration_normalizes_and_deduplicates_an_opportunity_family() -> None:
    canvas = Canvas.objects.create(title="Opportunity-family regeneration")
    metadata = {"generated_by_run_id": "fixture", "review_status": "accepted"}
    opportunity = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.OPPORTUNITY,
        title="Stale opportunity",
        metadata=metadata,
    )
    assumption = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.ASSUMPTION,
        title="Stale assumption",
        metadata=metadata,
    )
    risk = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.RISK,
        title="Selected stale risk",
        metadata=metadata,
    )
    experiment = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.VALIDATION_EXPERIMENT,
        title="Stale experiment",
        metadata=metadata,
    )
    Edge.objects.bulk_create(
        [
            Edge(
                canvas=canvas,
                source=opportunity,
                target=assumption,
                kind=EdgeKind.DERIVED_FROM,
            ),
            Edge(
                canvas=canvas,
                source=opportunity,
                target=risk,
                kind=EdgeKind.DERIVED_FROM,
            ),
            Edge(
                canvas=canvas,
                source=opportunity,
                target=experiment,
                kind=EdgeKind.REQUIRES_VALIDATION,
            ),
        ]
    )
    stale_operation = GraphOperation.objects.create(
        canvas=canvas,
        actor_type="test",
        operation_key="stale-opportunity-family",
        request_fingerprint="stale-opportunity-family",
        operation_type="MARK_STALE",
        payload={},
        result_payload={},
        canvas_revision=1,
    )
    for node in (opportunity, assumption, risk, experiment):
        node.stale = True
        node.stale_since_revision = 1
        node.save(update_fields=["stale", "stale_since_revision"])
        NodeStalenessCause.objects.create(
            canvas=canvas,
            node=node,
            cause_graph_operation=stale_operation,
            origin_entity_type="node",
            origin_entity_id=node.id,
        )
    request = GenerationRunRequest(
        operation="regenerate_stale",
        selected_node_ids=[risk.id],
        expected_node_versions={risk.id: risk.version},
        execution_profile_id="replay_v1",
        idempotency_key="family-normalization",
        regeneration_scope="node",
    )

    context = GraphRunContextFactory().build(
        canvas=canvas,
        request=request,
        selected_nodes=[risk],
    )
    targets = context.manifest["regeneration"]["targets"]

    assert len(targets) == 1
    assert targets[0]["node_id"] == str(opportunity.id)
    assert targets[0]["kind"] == NodeKind.OPPORTUNITY
    assert targets[0]["member_node_ids"] == sorted(
        str(node.id) for node in (opportunity, assumption, risk, experiment)
    )
    assert targets[0]["stale_node_ids"] == targets[0]["member_node_ids"]
