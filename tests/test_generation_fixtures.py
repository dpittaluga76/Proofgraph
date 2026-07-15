from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import transaction

from proofgraph.generation.composition import build_production_composition
from proofgraph.generation.execution import process_claimed_run
from proofgraph.generation.fixtures import FixtureBundle, StrictFixtureProviders
from proofgraph.generation.models import GenerationEventType, GenerationRun, RunStatus
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.queue import claim_run
from proofgraph.generation.schemas import GenerationRunRequest, RunExecutionConfiguration
from proofgraph.generation.services import create_generation_run
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


class BombClient:
    @property
    def responses(self):  # type: ignore[no-untyped-def]
        raise AssertionError("replay fixtures must never access a live OpenAI client")


def canonical_canvas() -> tuple[Canvas, list[Node]]:
    canvas = Canvas.objects.create(title="Security questionnaire fixture")
    goal = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.GOAL,
        title="Reduce security questionnaire work",
        body=(
            "Help a small B2B SaaS team reduce repeated security questionnaire effort and "
            "deal delay."
        ),
        metadata={"fixture_role": "goal"},
    )
    constraints = [
        Node.objects.create(
            canvas=canvas,
            kind=NodeKind.CONSTRAINT,
            title=title,
            body=body,
            metadata={
                "fixture_role": role,
                "context_scope": "global",
                "pinned": True,
            },
        )
        for role, title, body in (
            (
                "constraint_horizon",
                "Six-week MVP",
                "A useful MVP must be buildable within six weeks.",
            ),
            (
                "constraint_sources",
                "Approved evidence only",
                "Use public or user-approved evidence only.",
            ),
            (
                "constraint_team",
                "Small technical team",
                "The builder is a small technical team.",
            ),
        )
    ]
    return canvas, [goal, *constraints]


def replay_request(nodes: list[Node], key: str) -> GenerationRunRequest:
    return GenerationRunRequest(
        operation="generate_strategies",
        selected_node_ids=[node.id for node in nodes],
        expected_node_versions={node.id: node.version for node in nodes},
        execution_profile_id="replay_v1",
        idempotency_key=key,
    )


def canonical_research_canvas() -> tuple[Canvas, Node]:
    canvas, nodes = canonical_canvas()
    goal = nodes[0]
    strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Productize the recurring questionnaire workflow",
        body=(
            "Turn repeated answer gathering, review, and delivery into a reviewer-controlled "
            "workflow."
        ),
        metadata={
            "fixture_role": "strategy",
            "review_status": "accepted",
            "strategy_template_id": "productize_recurring_service",
        },
    )
    Edge.objects.create(
        canvas=canvas,
        source=goal,
        target=strategy,
        kind=EdgeKind.EVOLVES_INTO,
        metadata={"fixture_role": "goal_strategy"},
    )
    return canvas, strategy


def research_request(strategy: Node, key: str) -> GenerationRunRequest:
    return GenerationRunRequest(
        operation="research_evidence",
        selected_node_ids=[strategy.id],
        expected_node_versions={strategy.id: strategy.version},
        execution_profile_id="replay_v1",
        idempotency_key=key,
    )


def canonical_synthesis_canvas() -> tuple[Canvas, Node, list[Node]]:
    canvas, strategy = canonical_research_canvas()
    source_benchmark = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Synthetic security questionnaire workflow benchmark",
        body=(
            "Synthetic teams reported repeated answer lookup and reviewer handoffs across "
            "enterprise questionnaires."
        ),
        metadata={
            "fixture_role": "source_benchmark",
            "review_status": "accepted",
            "content_hash": f"sha256:{'a' * 64}",
            "independence_key": "publisher:proofgraph-fixtures.invalid",
            "authority": {"authoritative": False, "hierarchy_rank": 4},
        },
    )
    source_interviews = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Synthetic buyer interview summary",
        body=(
            "Synthetic buyers described deal delay, spreadsheet tracking, and a need to keep "
            "human approval in the loop."
        ),
        metadata={
            "fixture_role": "source_interviews",
            "review_status": "accepted",
            "content_hash": f"sha256:{'b' * 64}",
            "independence_key": "dataset:synthetic-interviews-v1",
            "authority": {"authoritative": False, "hierarchy_rank": 5},
        },
    )
    definitions = (
        (
            "claim_repeated_labor",
            "Questionnaire response work repeats",
            "Security questionnaire response work repeats across enterprise sales cycles.",
            "observed",
            "workflow_recurrence",
            ["security_questionnaire"],
            ["automate_mandatory_work"],
            None,
            "strong",
            [source_benchmark, source_interviews],
        ),
        (
            "claim_deal_delay",
            "Questionnaire handoffs can delay deals",
            "Answer gathering and approval handoffs can delay enterprise deals.",
            "observed",
            "labor_cost",
            ["deal_delay", "security_questionnaire"],
            ["rebundle_fragmented_workflow"],
            None,
            "medium",
            [source_interviews],
        ),
        (
            "claim_workaround_pain",
            "Spreadsheet handoffs create coordination pain",
            (
                "Teams coordinate questionnaire answers through fragile spreadsheet and "
                "document handoffs."
            ),
            "observed",
            "customer_pain",
            ["security_questionnaire", "spreadsheet_workaround"],
            ["replace_critical_spreadsheet"],
            None,
            "medium",
            [source_interviews],
        ),
        (
            "claim_trust_burden",
            "Human review remains necessary",
            "Teams may reject automation that removes human security review.",
            "contradicting",
            "adoption_risk",
            ["security_questionnaire", "trust"],
            ["reviewer_control"],
            "automate_mandatory_work",
            "medium",
            [source_interviews],
        ),
    )
    claims = []
    for (
        role,
        title,
        body,
        classification,
        evidence_type,
        topic_keys,
        mechanism_tags,
        contradiction_target,
        strength,
        sources,
    ) in definitions:
        metadata = {
            "fixture_role": role,
            "review_status": "accepted",
            "classification": classification,
            "evidence_type": evidence_type,
            "topic_keys": topic_keys,
            "mechanism_tags": mechanism_tags,
            "strength": strength,
            "limitations": ["Synthetic fixture evidence"],
            "source_ids": [str(source.id) for source in sources],
            "independence_keys": sorted(
                {source.metadata["independence_key"] for source in sources}
            ),
        }
        if contradiction_target is not None:
            metadata["contradiction_target_key"] = contradiction_target
        claim = Node.objects.create(
            canvas=canvas,
            kind=NodeKind.CLAIM,
            title=title,
            body=body,
            metadata=metadata,
        )
        claims.append(claim)
        for source in sources:
            Edge.objects.create(
                canvas=canvas,
                source=claim,
                target=source,
                kind=EdgeKind.EXTRACTED_FROM,
                metadata={"fixture_role": f"{role}_{source.metadata['fixture_role']}"},
            )
    return canvas, strategy, claims


def synthesis_request(strategy: Node, claims: list[Node], key: str) -> GenerationRunRequest:
    nodes = [strategy, *claims]
    return GenerationRunRequest(
        operation="synthesize_opportunities",
        selected_node_ids=[node.id for node in nodes],
        expected_node_versions={node.id: node.version for node in nodes},
        execution_profile_id="replay_v1",
        idempotency_key=key,
    )


def fixture_root() -> Path:
    return Path(settings.GENERATION_FIXTURE_ROOT)


def mark_stale(node: Node) -> None:
    with transaction.atomic():
        revision = GraphOperation.objects.filter(canvas=node.canvas).count() + 1
        operation = GraphOperation.objects.create(
            canvas=node.canvas,
            actor_type="test",
            operation_key=str(uuid.uuid4()),
            request_fingerprint=str(uuid.uuid4()),
            operation_type="MARK_STALE",
            payload={},
            result_payload={},
            canvas_revision=revision,
        )
        node.stale = True
        node.stale_since_revision = revision
        node.save(update_fields=["stale", "stale_since_revision"])
        NodeStalenessCause.objects.create(
            canvas=node.canvas,
            node=node,
            cause_graph_operation=operation,
            origin_entity_type="node",
            origin_entity_id=node.id,
        )


def replay_regeneration_request(
    target: Node, key: str, *, scope: str = "node"
) -> GenerationRunRequest:
    return GenerationRunRequest(
        operation="regenerate_stale",
        selected_node_ids=[target.id],
        expected_node_versions={target.id: target.version},
        execution_profile_id="replay_v1",
        idempotency_key=key,
        regeneration_scope=scope,
    )


def execute_replay(monkeypatch, canvas: Canvas, request: GenerationRunRequest) -> GenerationRun:
    composition = build_production_composition(
        openai_client=BombClient(),
        live_product_selectable=False,
        fixture_root=fixture_root(),
    )
    monkeypatch.setattr("proofgraph.generation.services.get_composition", lambda: composition)
    created = create_generation_run(canvas.id, request)
    lease = claim_run("fixture-worker")
    assert lease is not None
    process_claimed_run(lease, composition=composition)
    return GenerationRun.objects.get(pk=created.payload["run_id"])


def regeneration_strategy_canvas() -> tuple[Canvas, Node]:
    canvas, nodes = canonical_canvas()
    target = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Reviewer-controlled questionnaire workflow",
        body="Reuse approved answers with provenance and explicit review.",
        metadata={
            "fixture_role": "target",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    mark_stale(target)
    Edge.objects.create(
        canvas=canvas,
        source=nodes[0],
        target=target,
        kind=EdgeKind.EVOLVES_INTO,
        metadata={"fixture_role": "goal_target"},
    )
    return canvas, target


def _regeneration_source(canvas: Canvas) -> Node:
    return Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Synthetic buyer interview summary",
        body=(
            "Synthetic buyers described deal delay, spreadsheet tracking, and a need to keep "
            "human approval in the loop."
        ),
        metadata={
            "fixture_role": "source_interviews",
            "review_status": "accepted",
            "content_hash": f"sha256:{'b' * 64}",
            "independence_key": "dataset:synthetic-interviews-v1",
            "authority": {"authoritative": False, "hierarchy_rank": 5},
        },
    )


def _add_benchmark_provenance(canvas: Canvas, claim: Node) -> None:
    source = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.SOURCE,
        title="Synthetic security questionnaire workflow benchmark",
        body="Synthetic benchmark of repeated answer lookup and reviewer handoffs.",
        metadata={
            "fixture_role": "source_benchmark",
            "review_status": "accepted",
            "content_hash": f"sha256:{'a' * 64}",
            "independence_key": "publisher:proofgraph-fixtures.invalid",
            "authority": {"authoritative": False, "hierarchy_rank": 5},
            "url": "https://fixtures.proofgraph.invalid/security-questionnaire-benchmark",
        },
    )
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=source,
        kind=EdgeKind.EXTRACTED_FROM,
        metadata={"fixture_role": "claim_repeated_labor_source_benchmark"},
    )
    claim.metadata = {
        **claim.metadata,
        "source_ids": [str(source.id), *claim.metadata["source_ids"]],
        "independence_keys": sorted(
            [
                *claim.metadata["independence_keys"],
                "publisher:proofgraph-fixtures.invalid",
            ]
        ),
    }
    claim.save(update_fields=["metadata"])


def _regeneration_strategy(canvas: Canvas, goal: Node) -> Node:
    strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Reviewer-controlled questionnaire workflow",
        body="Reuse approved answers with provenance and explicit review.",
        metadata={"fixture_role": "strategy", "review_status": "accepted"},
    )
    Edge.objects.create(
        canvas=canvas,
        source=goal,
        target=strategy,
        kind=EdgeKind.EVOLVES_INTO,
        metadata={"fixture_role": "goal_strategy"},
    )
    return strategy


def _regeneration_claim(
    canvas: Canvas,
    strategy: Node,
    source: Node,
    *,
    role: str,
    title: str,
    stale: bool = False,
) -> Node:
    claim = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CLAIM,
        title=title,
        body="Questionnaire work repeats across enterprise deals.",
        metadata={
            "fixture_role": role,
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
            "classification": "observed",
            "evidence_type": "workflow_recurrence",
            "topic_keys": ["security_questionnaire"],
            "mechanism_tags": ["automate_mandatory_work"],
            "strength": "medium",
            "limitations": ["Synthetic fixture evidence"],
            "source_ids": [str(source.id)],
            "independence_keys": ["dataset:synthetic-interviews-v1"],
        },
    )
    if stale:
        mark_stale(claim)
    Edge.objects.create(
        canvas=canvas,
        source=claim,
        target=source,
        kind=EdgeKind.EXTRACTED_FROM,
        metadata={"fixture_role": f"{role}_source_interviews"},
    )
    Edge.objects.create(
        canvas=canvas,
        source=strategy,
        target=claim,
        kind=EdgeKind.DERIVED_FROM,
        metadata={"fixture_role": f"strategy_{role}"},
    )
    return claim


def regeneration_claim_canvas() -> tuple[Canvas, Node]:
    canvas, nodes = canonical_canvas()
    strategy = _regeneration_strategy(canvas, nodes[0])
    source = _regeneration_source(canvas)
    target = _regeneration_claim(
        canvas,
        strategy,
        source,
        role="target",
        title="Stale recurrence claim",
        stale=True,
    )
    return canvas, target


def regeneration_opportunity_family_canvas(kind: str) -> tuple[Canvas, Node, tuple[Node, ...]]:
    canvas, nodes = canonical_canvas()
    strategy = _regeneration_strategy(canvas, nodes[0])
    source = _regeneration_source(canvas)
    claims = [
        _regeneration_claim(
            canvas,
            strategy,
            source,
            role=role,
            title=title,
        )
        for role, title in (
            ("claim_deal_delay", "Questionnaire handoffs can delay deals"),
            ("claim_repeated_labor", "Questionnaire response work repeats"),
            ("claim_workaround_pain", "Spreadsheet handoffs create coordination pain"),
            ("claim_trust_burden", "Human review remains necessary"),
        )
    ]
    claims[0].metadata = {**claims[0].metadata, "evidence_type": "labor_cost"}
    claims[0].save(update_fields=["metadata"])
    _add_benchmark_provenance(canvas, claims[1])
    claims[2].metadata = {**claims[2].metadata, "evidence_type": "customer_pain"}
    claims[2].save(update_fields=["metadata"])
    claims[3].metadata = {
        **claims[3].metadata,
        "classification": "contradicting",
        "contradiction_target_key": "automate_mandatory_work",
    }
    claims[3].save(update_fields=["metadata"])
    opportunity = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.OPPORTUNITY,
        title="Stale opportunity",
        body="A stale generated opportunity production unit.",
        metadata={
            "fixture_role": "target",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    for index, claim in enumerate(claims):
        Edge.objects.create(
            canvas=canvas,
            source=claim,
            target=opportunity,
            kind=EdgeKind.CONTRADICTS if index == 3 else EdgeKind.SUPPORTS,
            metadata={"fixture_role": f"{claim.metadata['fixture_role']}_target"},
        )
    assumption = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.ASSUMPTION,
        title="Stale recurring-volume assumption",
        body="The questionnaire workflow recurs frequently enough.",
        metadata={
            "fixture_role": "target_assumption",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    risk = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.RISK,
        title="Stale trust risk",
        body="Trust requirements may slow adoption.",
        metadata={
            "fixture_role": "target_risk",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    experiment = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.VALIDATION_EXPERIMENT,
        title="Stale willingness-to-pay experiment",
        body="Run a concierge workflow with five design partners.",
        metadata={
            "fixture_role": "target_experiment",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    for member, edge_kind, role in (
        (assumption, EdgeKind.DERIVED_FROM, "target_assumption_edge"),
        (risk, EdgeKind.DERIVED_FROM, "target_risk_edge"),
        (experiment, EdgeKind.REQUIRES_VALIDATION, "target_experiment_edge"),
    ):
        Edge.objects.create(
            canvas=canvas,
            source=opportunity,
            target=member,
            kind=edge_kind,
            metadata={"fixture_role": role},
        )
    family = (opportunity, assumption, risk, experiment)
    for member in family:
        mark_stale(member)
    selected = next(member for member in family if member.kind == kind)
    return canvas, selected, family


def regeneration_branch_canvas() -> tuple[Canvas, tuple[Node, Node, Node]]:
    canvas, nodes = canonical_canvas()
    strategy = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.STRATEGY,
        title="Reviewer-controlled questionnaire workflow",
        body="Reuse approved answers with provenance and explicit review.",
        metadata={
            "fixture_role": "target_strategy",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    mark_stale(strategy)
    Edge.objects.create(
        canvas=canvas,
        source=nodes[0],
        target=strategy,
        kind=EdgeKind.EVOLVES_INTO,
        metadata={"fixture_role": "goal_target_strategy"},
    )
    source = _regeneration_source(canvas)
    claim = _regeneration_claim(
        canvas,
        strategy,
        source,
        role="target_claim",
        title="Stale branch claim",
        stale=True,
    )
    supporting_claims = [
        _regeneration_claim(
            canvas,
            strategy,
            source,
            role=role,
            title=title,
        )
        for role, title in (
            ("claim_deal_delay", "Questionnaire handoffs can delay deals"),
            ("claim_repeated_labor", "Questionnaire response work repeats"),
            ("claim_trust_burden", "Human review remains necessary"),
        )
    ]
    supporting_claims[0].metadata = {
        **supporting_claims[0].metadata,
        "evidence_type": "labor_cost",
    }
    supporting_claims[0].save(update_fields=["metadata"])
    _add_benchmark_provenance(canvas, supporting_claims[1])
    supporting_claims[2].metadata = {
        **supporting_claims[2].metadata,
        "classification": "contradicting",
        "contradiction_target_key": "automate_mandatory_work",
    }
    supporting_claims[2].save(update_fields=["metadata"])
    opportunity = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.OPPORTUNITY,
        title="Stale branch opportunity",
        body="A stale opportunity.",
        metadata={
            "fixture_role": "target_opportunity",
            "review_status": "accepted",
            "generated_by_run_id": "fixture",
        },
    )
    mark_stale(opportunity)
    for current_claim in [claim, *supporting_claims]:
        Edge.objects.create(
            canvas=canvas,
            source=current_claim,
            target=opportunity,
            kind=(
                EdgeKind.CONTRADICTS
                if current_claim.metadata["fixture_role"] == "claim_trust_burden"
                else EdgeKind.SUPPORTS
            ),
            metadata={
                "fixture_role": (f"{current_claim.metadata['fixture_role']}_target_opportunity")
            },
        )
    return canvas, (strategy, claim, opportunity)


def test_bundle_is_complete_hashed_and_derived_evidence_only() -> None:
    bundle = FixtureBundle.load(fixture_root())

    assert bundle.manifest.scenario_id == "security_questionnaires_v1"
    assert bundle.manifest.fixture_version == "1"
    assert {case.stage for case in bundle.manifest.cases} >= {
        "planning",
        "constructing_patch",
    }
    assert bundle.manifest.content_policy == ("synthetic_or_redistributable_derived_evidence_only")
    assert bundle.manifest.patch_builder_version == "checkpoint_patch_builder_v1"
    commitments = bundle.documents["semantic-input-hashes.json"]["semantic_input_hashes"]
    assert set(commitments) == {case.semantic_input_key for case in bundle.manifest.cases}
    assert all(
        commitments[case.semantic_input_key] == case.semantic_input_hash
        for case in bundle.manifest.cases
    )
    assert set(bundle.manifest.document_hashes) == {
        "claims.json",
        "critique-outputs.json",
        "patch-construction-outputs.json",
        "planning-outputs.json",
        "progress-events.json",
        "semantic-input-hashes.json",
        "sources.json",
        "synthesis-outputs.json",
    }


def test_bundle_rejects_tampered_dynamic_patch_asset(tmp_path: Path) -> None:
    copied = tmp_path / "bundle"
    shutil.copytree(fixture_root(), copied)
    patch_path = copied / "patch-construction-outputs.json"
    document = json.loads(patch_path.read_text(encoding="utf-8"))
    document["outputs"]["canonical_generate_patch"] = {
        "$build_from_validated_checkpoints": "changed-without-version"
    }
    patch_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="document hash mismatch"):
        FixtureBundle.load(copied)


def test_fixture_provider_rejects_execution_identity_mismatch() -> None:
    provider = StrictFixtureProviders(FixtureBundle.load(fixture_root()))
    request = ProviderStageRequest(
        stage_input={},
        configuration=RunExecutionConfiguration(
            profile_id="replay_v1",
            provider_identity="fixture:security_questionnaires_v1:v1",
            pipeline_version="intelligence_pipeline_v1",
            prompt_version="opportunity_pipeline_prompts_v1",
            strategy_version="opportunity_strategies_v1",
            fixture_bundle_id="security_questionnaires_v1",
            fixture_version="2",
        ),
    )

    with pytest.raises(ProviderExecutionError) as captured:
        provider.plan(request)

    assert captured.value.code == "fixture_input_mismatch"
    assert captured.value.details["actual"]["fixture_version"] == "2"


def test_full_replay_reaches_patch_ready_without_live_provider_access(monkeypatch) -> None:
    canvas, nodes = canonical_canvas()
    composition = build_production_composition(
        openai_client=BombClient(),
        live_product_selectable=False,
        fixture_root=fixture_root(),
    )
    monkeypatch.setattr("proofgraph.generation.services.get_composition", lambda: composition)
    created = create_generation_run(
        canvas.id,
        replay_request(nodes, "fixture-full-replay"),
    )
    lease = claim_run("fixture-worker")
    assert lease is not None

    process_claimed_run(lease, composition=composition)

    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    assert run.patch.operations
    assert run.events.filter(event_type=GenerationEventType.PATCH_READY).count() == 1
    assert run.events.filter(event_type=GenerationEventType.CANDIDATE_GENERATED).count() == 3
    assert run.stages.get(name="planning").openai_response_id.startswith("fixture:")


def test_fixture_semantic_mismatch_fails_recoverably_without_fallback(monkeypatch) -> None:
    canvas, nodes = canonical_canvas()
    nodes[0].title = "A different semantic goal"
    nodes[0].save(update_fields=["title"])
    composition = build_production_composition(
        openai_client=BombClient(),
        live_product_selectable=False,
        fixture_root=fixture_root(),
    )
    monkeypatch.setattr("proofgraph.generation.services.get_composition", lambda: composition)
    created = create_generation_run(
        canvas.id,
        replay_request(nodes, "fixture-mismatch"),
    )
    lease = claim_run("fixture-worker")
    assert lease is not None

    process_claimed_run(lease, composition=composition)

    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert run.status == RunStatus.FAILED
    assert run.error["code"] == "fixture_input_mismatch"
    assert run.error["retryable"] is True
    assert not run.events.filter(event_type=GenerationEventType.PATCH_READY).exists()


def test_replay_research_emits_provisional_evidence_and_reviewable_patch(monkeypatch) -> None:
    canvas, strategy = canonical_research_canvas()
    composition = build_production_composition(
        openai_client=BombClient(),
        live_product_selectable=False,
        fixture_root=fixture_root(),
    )
    monkeypatch.setattr("proofgraph.generation.services.get_composition", lambda: composition)
    created = create_generation_run(
        canvas.id,
        research_request(strategy, "fixture-research"),
    )
    lease = claim_run("fixture-worker")
    assert lease is not None

    process_claimed_run(lease, composition=composition)

    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    assert run.stages.filter(status="completed").count() == 5
    assert run.events.filter(event_type=GenerationEventType.RESEARCH_SOURCE_FOUND).exists()
    assert run.events.filter(event_type=GenerationEventType.EVIDENCE_EXTRACTED).count() == 3
    assert all(
        event.payload["provisional"] is True
        for event in run.events.filter(
            event_type__in=(
                GenerationEventType.RESEARCH_SOURCE_FOUND,
                GenerationEventType.EVIDENCE_EXTRACTED,
            )
        )
    )
    assert {
        operation["node"]["kind"]
        for operation in run.patch.operations
        if operation["op"] == "ADD_NODE"
    } == {
        "source",
        "claim",
    }
    assert any(
        operation["op"] == "ADD_EDGE"
        and operation["edge"]["source_node_id"] == str(strategy.id)
        and operation["edge"]["target_node_id"] == "source_synthetic_benchmark"
        and operation["edge"]["kind"] == "derived_from"
        for operation in run.patch.operations
    )


def test_replay_synthesis_produces_three_critiqued_traceable_opportunities(monkeypatch) -> None:
    canvas, strategy, claims = canonical_synthesis_canvas()
    composition = build_production_composition(
        openai_client=BombClient(),
        live_product_selectable=False,
        fixture_root=fixture_root(),
    )
    monkeypatch.setattr("proofgraph.generation.services.get_composition", lambda: composition)
    created = create_generation_run(
        canvas.id,
        synthesis_request(strategy, claims, "fixture-synthesis"),
    )
    lease = claim_run("fixture-worker")
    assert lease is not None

    process_claimed_run(lease, composition=composition)

    run = GenerationRun.objects.get(pk=created.payload["run_id"])
    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    synthesis = run.stages.get(name="synthesizing").output["output"]
    critique = run.stages.get(name="critiquing").output["output"]
    assert len(synthesis["opportunities"]) == len(critique["critiques"]) == 3
    assert {opportunity["support_status"] for opportunity in synthesis["opportunities"]} == {
        "supported",
        "speculative",
    }
    added_kinds = {
        operation["node"]["kind"]
        for operation in run.patch.operations
        if operation["op"] == "ADD_NODE"
    }
    assert added_kinds >= {"opportunity", "assumption", "risk", "validation_experiment"}
    assert any(
        operation["edge"]["kind"] == "contradicts"
        for operation in run.patch.operations
        if operation["op"] == "ADD_EDGE"
    )
    family_edges = [
        operation["edge"] for operation in run.patch.operations if operation["op"] == "ADD_EDGE"
    ]
    assert any(
        edge["source_node_id"] == "opportunity_answer_workspace"
        and edge["target_node_id"] == "assumption_recurring_volume"
        and edge["kind"] == "derived_from"
        for edge in family_edges
    )
    assert any(
        edge["source_node_id"]
        == str(
            next(
                claim.id
                for claim in claims
                if claim.metadata["fixture_role"] == "claim_workaround_pain"
            )
        )
        and edge["target_node_id"] == "opportunity_answer_workspace"
        and edge["kind"] == "supports"
        for edge in family_edges
    )


def test_replay_regenerates_one_stale_strategy_production_unit(monkeypatch) -> None:
    canvas, target = regeneration_strategy_canvas()
    run = execute_replay(
        monkeypatch,
        canvas,
        replay_regeneration_request(target, "fixture-regen-strategy"),
    )

    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    patch_output = run.stages.get(name="constructing_patch").output["output"]
    assert patch_output["regeneration_target_ids"] == [str(target.id)]
    assert patch_output["permitted_stale_resolution_ids"] == [str(target.id)]
    assert run.stages.filter(status="completed").count() == 2


def test_replay_regenerates_one_stale_claim_with_research_checkpoints(monkeypatch) -> None:
    canvas, target = regeneration_claim_canvas()
    run = execute_replay(
        monkeypatch,
        canvas,
        replay_regeneration_request(target, "fixture-regen-claim"),
    )

    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    patch_output = run.stages.get(name="constructing_patch").output["output"]
    assert patch_output["regeneration_target_ids"] == [str(target.id)]
    assert run.stages.filter(status="completed").count() == 5
    assert run.events.filter(event_type=GenerationEventType.EVIDENCE_EXTRACTED).count() == 3


@pytest.mark.parametrize(
    "kind",
    (
        NodeKind.OPPORTUNITY,
        NodeKind.ASSUMPTION,
        NodeKind.RISK,
        NodeKind.VALIDATION_EXPERIMENT,
    ),
)
def test_replay_regenerates_every_opportunity_family_target(monkeypatch, kind: str) -> None:
    canvas, selected, family = regeneration_opportunity_family_canvas(kind)
    opportunity = next(member for member in family if member.kind == NodeKind.OPPORTUNITY)
    run = execute_replay(
        monkeypatch,
        canvas,
        replay_regeneration_request(selected, f"fixture-regen-{kind}"),
    )

    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    patch_output = run.stages.get(name="constructing_patch").output["output"]
    assert patch_output["regeneration_target_ids"] == [str(opportunity.id)]
    assert patch_output["permitted_stale_resolution_ids"] == sorted(
        str(member.id) for member in family
    )
    assert run.stages.filter(status="completed").count() == 3


def test_replay_regenerates_stale_branch_in_dependency_order(monkeypatch) -> None:
    canvas, targets = regeneration_branch_canvas()
    run = execute_replay(
        monkeypatch,
        canvas,
        replay_regeneration_request(
            targets[0],
            "fixture-regen-branch",
            scope="branch",
        ),
    )

    assert run.status == RunStatus.COMPLETED, json.dumps(run.error, sort_keys=True)
    patch_output = run.stages.get(name="constructing_patch").output["output"]
    target_ids = sorted(str(target.id) for target in targets)
    assert patch_output["regeneration_target_ids"] == target_ids
    assert patch_output["permitted_stale_resolution_ids"] == target_ids
    assert run.patch.regeneration_target_ids == target_ids
    assert run.patch.permitted_stale_resolution_ids == target_ids
    assert run.stages.filter(status="completed").count() == 8
    assert run.events.filter(event_type=GenerationEventType.PATCH_READY).count() == 1
    branch_synthesis = run.stages.get(name="synthesizing").output["output"]
    assert any(
        evidence["claim_id"] == "claim_workaround_pain"
        for evidence in branch_synthesis["opportunities"][0]["evidence"]
    )
    assert any(
        operation["op"] == "ADD_EDGE"
        and operation["edge"]
        == {
            "source_node_id": "claim_workaround_pain",
            "target_node_id": "opportunity_answer_workspace",
            "kind": "supports",
            "metadata": {"generated_by_run_id": str(run.id)},
        }
        for operation in patch_output["operations"]
    )
