from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from proofgraph.generation.fixtures import (
    _build_fixture_patch,
    _fixture_prior_output,
    fixture_semantic_hash,
)
from proofgraph.generation.pipeline_schemas import (
    ExtractionOutput,
    GraphPatchOutput,
    PipelineStageOutputValidator,
    PlanningOutput,
    SynthesisOutput,
)
from proofgraph.generation.schemas import StageResultEnvelope
from proofgraph.generation.strategies import (
    MECHANISM_TAG_VOCAB_VERSION,
    MECHANISM_TAG_VOCABULARY,
    STRATEGY_BY_ID,
    STRATEGY_CATALOG_VERSION,
    STRATEGY_TEMPLATES,
)


def validate_json(model: type, value: dict[str, object]) -> object:
    return model.model_validate_json(json.dumps(value))


def test_fixture_prior_hash_omits_only_redundant_claim_candidate_audit_field() -> None:
    output = {
        "sources": [],
        "claims": [],
        "candidate_claim_ids": ["claim_rejected"],
        "rejected": [{"subject_kind": "claim", "source_or_claim_id": "claim_rejected"}],
    }

    normalized = _fixture_prior_output(output)

    assert "candidate_claim_ids" not in normalized
    assert normalized["rejected"] == output["rejected"]
    assert fixture_semantic_hash(normalized) != fixture_semantic_hash(output)


def source(source_id: str, independence_key: str) -> dict[str, object]:
    return {
        "id": source_id,
        "kind": "web",
        "url": f"https://example.com/{source_id}/evidence",
        "title": f"Evidence {source_id}",
        "retrieved_at": "2026-07-14T12:00:00Z",
        "content_hash": "sha256:" + ("a" * 64),
        "independence_key": independence_key,
        "authority": {
            "domain": "example.com",
            "publisher": source_id,
            "authoritative": True,
            "hierarchy_rank": 1,
        },
        "sanitized_excerpt": "A bounded derived excerpt.",
        "cache_hit": False,
    }


def opportunity(
    *,
    suffix: str = "one",
    support_status: str = "supported",
) -> dict[str, object]:
    evidence = [
        {
            "claim_id": "claim_cost",
            "signal_type": "labor_cost",
            "independence_key": "publisher:official.example",
            "accepted": True,
        },
        {
            "claim_id": "claim_demand_one",
            "signal_type": "demand",
            "independence_key": "publisher:demand-one.example",
            "accepted": True,
        },
        {
            "claim_id": "claim_demand_two",
            "signal_type": "pain",
            "independence_key": "publisher:demand-two.example",
            "accepted": True,
        },
        {
            "claim_id": "claim_contradiction",
            "signal_type": "contradiction",
            "independence_key": "publisher:contradiction.example",
            "accepted": True,
        },
    ]
    return {
        "id": f"opportunity_{suffix}",
        "title": "Evidence-backed workflow",
        "buyer": "Security operations lead",
        "problem": "Repeated questionnaires delay enterprise deals.",
        "current_spend_or_workaround": "Teams spend labor on spreadsheets and documents.",
        "mechanism": "Reuse approved answers with provenance and reviewer controls.",
        "business_model": "Subscription per workspace",
        "why_now": "Security reviews are expanding across smaller vendors.",
        "evidence": evidence,
        "contradiction": {
            "summary": "Some teams may prefer consulting services.",
            "claim_id": "claim_contradiction",
        },
        "assumptions": [
            {
                "id": f"assumption_{suffix}",
                "statement": "The workflow recurs frequently enough.",
                "importance": "high",
            }
        ],
        "risks": [
            {
                "id": f"risk_{suffix}",
                "statement": "Trust requirements may slow adoption.",
                "impact": "high",
                "mitigation": "Start with reviewer-controlled reuse.",
            }
        ],
        "validation_experiment": {
            "id": f"experiment_{suffix}",
            "hypothesis": "Teams will pay to reduce review labor.",
            "method": "Run a concierge workflow with five design partners.",
            "metric": "Paid commitments",
            "success_criteria": "Three paid commitments",
            "timebox": "Two weeks",
        },
        "builder_fit": "A small technical team can build the bounded workflow.",
        "distribution_channel": "Security-lead communities",
        "distribution_rationale": "The buyer already gathers in focused communities.",
        "defensibility": "Approved-answer history and integrations deepen over time.",
        "technical_feasibility": "The first version uses document ingestion and workflow state.",
        "operational_burden": "Human review remains necessary for exceptions.",
        "dimensions": {
            name: {"rating": "medium", "rationale": f"Separate {name} assessment."}
            for name in (
                "evidence_strength",
                "novelty",
                "builder_fit",
                "technical_feasibility",
                "distribution_clarity",
                "operational_burden",
            )
        },
        "support_status": support_status,
    }


def synthesis_stage_input() -> dict[str, object]:
    claim_keys = {
        "claim_cost": ("publisher:official.example", "observed", "labor_cost"),
        "claim_demand_one": ("publisher:demand-one.example", "observed", "demand"),
        "claim_demand_two": ("publisher:demand-two.example", "observed", "customer_pain"),
        "claim_contradiction": (
            "publisher:contradiction.example",
            "contradicting",
            "adoption_risk",
        ),
    }
    return {
        "context_snapshot": {
            "nodes": [
                *[
                    {
                        "id": claim_id,
                        "kind": "claim",
                        "metadata": {
                            "classification": classification,
                            "evidence_type": evidence_type,
                        },
                    }
                    for claim_id, (_, classification, evidence_type) in claim_keys.items()
                ],
                *[
                    {
                        "id": f"source_{claim_id}",
                        "kind": "source",
                        "metadata": {"independence_key": independence_key},
                    }
                    for claim_id, (independence_key, _, _) in claim_keys.items()
                ],
            ],
            "edges": [
                {
                    "id": f"edge_{claim_id}",
                    "kind": "extracted_from",
                    "source_node_id": claim_id,
                    "target_node_id": f"source_{claim_id}",
                }
                for claim_id in claim_keys
            ],
        },
        "context_manifest": {
            "request": {"operation": "synthesize_opportunities"},
            "explicit_node_ids": list(claim_keys),
        },
        "prior_stage_outputs": {},
        "target_workset": [],
    }


def test_strategy_catalog_is_versioned_complete_and_unique() -> None:
    assert STRATEGY_CATALOG_VERSION == "opportunity_strategies_v1"
    assert len(STRATEGY_TEMPLATES) == len(STRATEGY_BY_ID) == 14
    assert tuple(template.id for template in STRATEGY_TEMPLATES) == tuple(STRATEGY_BY_ID.keys())
    assert all(template.required_signals for template in STRATEGY_TEMPLATES)
    assert all(template.failure_conditions for template in STRATEGY_TEMPLATES)
    assert all(template.default_research_queries for template in STRATEGY_TEMPLATES)
    assert MECHANISM_TAG_VOCAB_VERSION == "opportunity_mechanisms_v1"
    assert set(STRATEGY_BY_ID) <= MECHANISM_TAG_VOCABULARY


def test_extraction_requires_canonical_claim_keys_and_known_sources() -> None:
    payload = {
        "sources": [source("source_one", "publisher:one.example")],
        "claims": [
            {
                "id": "claim_one",
                "claim": "Teams repeat this workflow.",
                "classification": "observed",
                "evidence_type": "customer_pain",
                "topic_keys": ["security_review", "vendor_questionnaire"],
                "mechanism_tags": ["automate_mandatory_work"],
                "strength": "medium",
                "limitations": ["One public report"],
                "source_ids": ["source_one"],
            }
        ],
        "candidate_claim_ids": ["claim_one"],
        "rejected": [],
    }
    validate_json(ExtractionOutput, payload)

    noncanonical = copy.deepcopy(payload)
    noncanonical["claims"][0]["topic_keys"] = ["vendor_questionnaire", "security_review"]
    with pytest.raises(ValidationError, match="sorted and deduplicated"):
        validate_json(ExtractionOutput, noncanonical)

    unknown_source = copy.deepcopy(payload)
    unknown_source["claims"][0]["source_ids"] = ["source_missing"]
    with pytest.raises(ValidationError, match="unknown sources"):
        validate_json(ExtractionOutput, unknown_source)

    unknown_mechanism = copy.deepcopy(payload)
    unknown_mechanism["claims"][0]["mechanism_tags"] = ["unversioned_mechanism"]
    with pytest.raises(ValidationError, match="unknown versioned mechanism tags"):
        validate_json(ExtractionOutput, unknown_mechanism)

    unknown_rejected = copy.deepcopy(payload)
    unknown_rejected["rejected"] = [
        {
            "subject_kind": "claim",
            "source_or_claim_id": "claim_never_seen",
            "reason": "invalid",
            "details": "This identity was not among the extracted candidates.",
        }
    ]
    with pytest.raises(ValidationError, match="partition candidate_claim_ids"):
        validate_json(ExtractionOutput, unknown_rejected)


def test_contextual_extraction_accounts_for_every_researched_source() -> None:
    validator = PipelineStageOutputValidator()
    source_one = source("source_one", "publisher:one.example")
    source_two = source("source_two", "publisher:two.example")
    output = {
        "sources": [source_one],
        "claims": [],
        "candidate_claim_ids": [],
        "rejected": [],
    }
    stage_input = {
        "context_snapshot": {"nodes": [], "edges": []},
        "context_manifest": {"request": {"operation": "research_evidence"}},
        "prior_stage_outputs": {
            "researching": {
                "output": {
                    "queries_executed": [],
                    "sources": [source_one, source_two],
                }
            }
        },
    }
    with pytest.raises(ValueError, match="retain or explicitly reject every researched source"):
        validator.validate(
            "extracting",
            StageResultEnvelope(
                stage_name="extracting",
                output=output,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )

    unknown_rejection = copy.deepcopy(output)
    unknown_rejection["rejected"] = [
        {
            "subject_kind": "source",
            "source_or_claim_id": "source_never_researched",
            "reason": "irrelevant",
            "details": "The source was not part of the frozen research output.",
        }
    ]
    with pytest.raises(ValueError, match="unknown researched sources"):
        validator.validate(
            "extracting",
            StageResultEnvelope(
                stage_name="extracting",
                output=unknown_rejection,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )


def test_supported_opportunity_threshold_uses_structured_evidence() -> None:
    payload = {
        "operation": "synthesize_opportunities",
        "opportunities": [
            opportunity(suffix="one"),
            opportunity(suffix="two"),
            opportunity(suffix="three"),
        ],
    }
    validate_json(SynthesisOutput, payload)

    insufficient = copy.deepcopy(payload)
    insufficient["opportunities"][0]["evidence"] = insufficient["opportunities"][0]["evidence"][:2]
    with pytest.raises(ValidationError, match="structured evidence threshold"):
        validate_json(SynthesisOutput, insufficient)

    speculative = copy.deepcopy(insufficient)
    speculative["opportunities"][0]["support_status"] = "speculative"
    validate_json(SynthesisOutput, speculative)


def test_regeneration_research_budget_is_global_across_targets() -> None:
    plans = []
    for target_index in range(2):
        plans.append(
            {
                "target_node_id": f"target_{target_index}",
                "selected_strategy_id": f"strategy_{target_index}",
                "research_questions": [
                    {
                        "id": f"question_{target_index}",
                        "question": "What evidence validates this target?",
                        "evidence_type": "customer_pain",
                    }
                ],
                "query_plan": [
                    {
                        "id": f"query_{target_index}_{query_index}",
                        "query": f"bounded evidence query {target_index} {query_index}",
                        "provider": "openai_web_search",
                        "evidence_type": "customer_pain",
                    }
                    for query_index in range(3)
                ],
                "required_evidence_types": [
                    {
                        "evidence_type": "customer_pain",
                        "reason": "The target needs independently observable pain.",
                        "minimum_independent_sources": 1,
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="one and five research queries total"):
        validate_json(
            PlanningOutput,
            {
                "operation": "regenerate_stale",
                "strategies": [],
                "research_plans": plans,
            },
        )


def test_planning_requires_material_strategies_and_applies_ip_policy() -> None:
    strategies = [
        {
            "id": f"strategy_{index}",
            "template_id": template_id,
            "title": f"Strategy {index}",
            "approach": f"Distinct workflow approach {index}.",
            "rationale": f"Distinct evidence rationale {index}.",
            "required_signal_matches": ["The workflow repeats."],
            "failure_condition_risks": ["Demand may be too weak."],
        }
        for index, template_id in enumerate(
            (
                "automate_mandatory_work",
                "replace_critical_spreadsheet",
                "remove_scarce_expert_bottleneck",
            ),
            start=1,
        )
    ]
    duplicate = copy.deepcopy(strategies)
    duplicate[1]["approach"] = duplicate[0]["approach"]
    with pytest.raises(ValidationError, match="materially different"):
        validate_json(
            PlanningOutput,
            {
                "operation": "generate_strategies",
                "strategies": duplicate,
                "research_plans": [],
            },
        )

    prohibited = copy.deepcopy(strategies)
    prohibited[0]["approach"] = "Clone the proprietary source implementation for customers."
    with pytest.raises(ValidationError, match="intellectual-property"):
        validate_json(
            PlanningOutput,
            {
                "operation": "generate_strategies",
                "strategies": prohibited,
                "research_plans": [],
            },
        )


def test_contextual_research_planning_is_bound_to_selected_and_linked_strategy() -> None:
    validator = PipelineStageOutputValidator()
    plan = {
        "operation": "research_evidence",
        "strategies": [],
        "research_plans": [
            {
                "target_node_id": "strategy_selected",
                "selected_strategy_id": "strategy_other",
                "research_questions": [
                    {
                        "id": "question_one",
                        "question": "What evidence confirms recurring work?",
                        "evidence_type": "workflow_recurrence",
                    }
                ],
                "query_plan": [
                    {
                        "id": "query_one",
                        "query": "recurring compliance workflow evidence",
                        "provider": "openai_web_search",
                        "evidence_type": "workflow_recurrence",
                    }
                ],
                "required_evidence_types": [
                    {
                        "evidence_type": "workflow_recurrence",
                        "reason": "Recurrence must be observable.",
                        "minimum_independent_sources": 1,
                    }
                ],
            }
        ],
    }
    research_input = {
        "context_snapshot": {
            "nodes": [
                {"id": "strategy_selected", "kind": "strategy"},
                {"id": "strategy_other", "kind": "strategy"},
            ],
            "edges": [],
        },
        "context_manifest": {
            "request": {"operation": "research_evidence"},
            "explicit_node_ids": ["strategy_selected"],
        },
        "target_workset": [],
        "prior_stage_outputs": {},
    }
    with pytest.raises(ValueError, match="explicitly selected strategy"):
        validator.validate(
            "planning",
            StageResultEnvelope(
                stage_name="planning",
                output=plan,
                provider_identity="fixture:test",
            ),
            stage_input=research_input,
        )

    wrong_target = copy.deepcopy(plan)
    wrong_target["research_plans"][0]["selected_strategy_id"] = "strategy_selected"
    wrong_target["research_plans"][0]["target_node_id"] = None
    with pytest.raises(ValueError, match="target must be the selected strategy"):
        validator.validate(
            "planning",
            StageResultEnvelope(
                stage_name="planning",
                output=wrong_target,
                provider_identity="fixture:test",
            ),
            stage_input=research_input,
        )

    regeneration_plan = copy.deepcopy(plan)
    regeneration_plan["operation"] = "regenerate_stale"
    regeneration_plan["research_plans"][0]["target_node_id"] = "claim_target"
    regeneration_input = {
        "context_snapshot": {
            "nodes": [
                {"id": "strategy_selected", "kind": "strategy"},
                {"id": "strategy_other", "kind": "strategy"},
                {"id": "claim_target", "kind": "claim"},
            ],
            "edges": [
                {
                    "id": "edge_strategy_claim",
                    "kind": "derived_from",
                    "source_node_id": "strategy_selected",
                    "target_node_id": "claim_target",
                }
            ],
        },
        "context_manifest": {"request": {"operation": "regenerate_stale"}},
        "target_workset": [{"node_id": "claim_target", "kind": "claim"}],
        "prior_stage_outputs": {},
    }
    with pytest.raises(ValueError, match="target's linked strategy"):
        validator.validate(
            "planning",
            StageResultEnvelope(
                stage_name="planning",
                output=regeneration_plan,
                provider_identity="fixture:test",
            ),
            stage_input=regeneration_input,
        )


def test_ip_policy_rejects_paraphrased_proprietary_copying() -> None:
    payload = {
        "operation": "synthesize_opportunities",
        "opportunities": [
            opportunity(suffix="one"),
            opportunity(suffix="two"),
            opportunity(suffix="three"),
        ],
    }
    payload["opportunities"][0]["mechanism"] = (
        "Replicate the proprietary implementation and expose it as a hosted workflow."
    )

    with pytest.raises(ValidationError, match="intellectual-property"):
        validate_json(SynthesisOutput, payload)


@pytest.mark.parametrize(
    "prohibited_mechanism",
    (
        "Reimplement the internals of a closed-source competitor.",
        "Reuse private customer records to train the recommendation model.",
        "Circumvent the vendor conditions of use to gather the dataset.",
        "Create a confusingly similar trademark for the launch.",
        "Bypass authentication to scrape the protected workspace.",
        "Build our own version by studying the internal behavior of a closed commercial rival.",
        "Use a competitor's confidential customer records to train the recommendation model.",
        "Ignore the service agreement while scraping the platform.",
    ),
)
def test_ip_policy_rejects_prohibited_intent_paraphrases(
    prohibited_mechanism: str,
) -> None:
    payload = {
        "operation": "synthesize_opportunities",
        "opportunities": [
            opportunity(suffix="one"),
            opportunity(suffix="two"),
            opportunity(suffix="three"),
        ],
    }
    payload["opportunities"][0]["mechanism"] = prohibited_mechanism

    with pytest.raises(ValidationError, match="intellectual-property"):
        validate_json(SynthesisOutput, payload)


@pytest.mark.parametrize(
    "allowed_text",
    (
        "Do not copy proprietary code; integrate through the documented API.",
        "Competitors may copy proprietary code; prevent that with access controls.",
        "Copying proprietary implementation details is prohibited.",
        "Our controls ensure users cannot reuse private datasets.",
        "The product blocks customers from copying proprietary code.",
        "We detect when suppliers steal private records and stop the transfer.",
        "Copying protected interface assets remains a compliance risk.",
    ),
)
def test_ip_policy_allows_guardrails_and_descriptive_risks(allowed_text: str) -> None:
    payload = {
        "operation": "synthesize_opportunities",
        "opportunities": [
            opportunity(suffix="one"),
            opportunity(suffix="two"),
            opportunity(suffix="three"),
        ],
    }
    payload["opportunities"][0]["mechanism"] = allowed_text

    validate_json(SynthesisOutput, payload)


def test_patch_schema_rejects_unresolved_references_and_dependency_cycles() -> None:
    unresolved = {
        "base_canvas_revision": 4,
        "known_node_ids": ["known_goal"],
        "operations": [
            {
                "operation_id": "edge_operation",
                "op": "ADD_EDGE",
                "client_generated_id": "new_edge",
                "edge": {
                    "source_node_id": "known_goal",
                    "target_node_id": "missing_node",
                    "kind": "evolves_into",
                },
            }
        ],
    }
    with pytest.raises(ValidationError, match="unresolved"):
        validate_json(GraphPatchOutput, unresolved)

    cycle = {
        "base_canvas_revision": 4,
        "operations": [
            {
                "operation_id": "first",
                "op": "ADD_NODE",
                "depends_on": ["second"],
                "client_generated_id": "first_node",
                "node": {"kind": "strategy", "title": "First"},
            },
            {
                "operation_id": "second",
                "op": "ADD_NODE",
                "depends_on": ["first"],
                "client_generated_id": "second_node",
                "node": {"kind": "strategy", "title": "Second"},
            },
        ],
    }
    with pytest.raises(ValidationError, match="acyclic"):
        validate_json(GraphPatchOutput, cycle)


def test_delete_node_requires_operation_dependencies_for_every_prerequisite() -> None:
    patch = {
        "base_canvas_revision": 4,
        "known_node_ids": ["branch_constraint", "target_node"],
        "known_edge_ids": ["incident_edge"],
        "operations": [
            {
                "operation_id": "delete_edge",
                "op": "DELETE_EDGE",
                "edge_id": "incident_edge",
                "expected_version": 1,
            },
            {
                "operation_id": "detach_constraint",
                "op": "UPDATE_NODE",
                "node_id": "branch_constraint",
                "expected_version": 1,
                "changes": {"branch_root_node_id": None},
            },
            {
                "operation_id": "delete_node",
                "op": "DELETE_NODE",
                "depends_on": ["delete_edge"],
                "node_id": "target_node",
                "expected_version": 1,
                "required_incident_edge_ids": ["incident_edge"],
                "required_branch_constraint_ids": ["branch_constraint"],
            },
        ],
    }

    with pytest.raises(ValidationError, match="depend on every declared prerequisite"):
        validate_json(GraphPatchOutput, patch)

    patch["operations"][2]["depends_on"] = ["delete_edge", "detach_constraint"]
    patch["operations"][2]["depends_on"].sort()
    validate_json(GraphPatchOutput, patch)


def test_contextual_synthesis_rejects_unselected_claims_and_misbound_source_keys() -> None:
    validator = PipelineStageOutputValidator()
    stage_input = synthesis_stage_input()
    valid_output = {
        "operation": "synthesize_opportunities",
        "opportunities": [
            opportunity(suffix="one"),
            opportunity(suffix="two"),
            opportunity(suffix="three"),
        ],
    }

    unselected_input = copy.deepcopy(stage_input)
    unselected_input["context_snapshot"]["nodes"].extend(
        [
            {"id": "claim_unselected", "kind": "claim", "metadata": {}},
            {
                "id": "source_unselected",
                "kind": "source",
                "metadata": {"independence_key": "publisher:official.example"},
            },
        ]
    )
    unselected_input["context_snapshot"]["edges"].append(
        {
            "id": "edge_unselected",
            "kind": "extracted_from",
            "source_node_id": "claim_unselected",
            "target_node_id": "source_unselected",
        }
    )
    unselected_output = copy.deepcopy(valid_output)
    unselected_output["opportunities"][0]["evidence"][0]["claim_id"] = "claim_unselected"
    with pytest.raises(ValueError, match="provisional or unselected claims"):
        validator.validate(
            "synthesizing",
            StageResultEnvelope(
                stage_name="synthesizing",
                output=unselected_output,
                provider_identity="fixture:test",
            ),
            stage_input=unselected_input,
        )

    misbound_output = copy.deepcopy(valid_output)
    misbound_output["opportunities"][0]["evidence"][0]["independence_key"] = (
        "publisher:demand-one.example"
    )
    with pytest.raises(ValueError, match="accepted source relations"):
        validator.validate(
            "synthesizing",
            StageResultEnvelope(
                stage_name="synthesizing",
                output=misbound_output,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )

    spoofed_signal_input = copy.deepcopy(stage_input)
    cost_claim = next(
        node
        for node in spoofed_signal_input["context_snapshot"]["nodes"]
        if node["id"] == "claim_cost"
    )
    cost_claim["metadata"]["evidence_type"] = "customer_pain"
    with pytest.raises(ValueError, match="authoritative claim metadata"):
        validator.validate(
            "synthesizing",
            StageResultEnvelope(
                stage_name="synthesizing",
                output=valid_output,
                provider_identity="fixture:test",
            ),
            stage_input=spoofed_signal_input,
        )


def test_contextual_patch_enforces_operation_kinds_metadata_and_directed_lineage() -> None:
    validator = PipelineStageOutputValidator()
    planned_strategies = [
        {
            "id": f"strategy_{suffix}",
            "template_id": template_id,
            "title": f"Localized strategy {suffix}",
            "approach": f"Apply the {template_id} strategy to the selected goal.",
            "rationale": f"The {template_id} lens fits the frozen goal evidence.",
            "required_signal_matches": ["A relevant frozen goal exists."],
            "failure_condition_risks": ["The strategy may not fit the buyer."],
        }
        for suffix, template_id in (
            ("one", "automate_mandatory_work"),
            ("two", "replace_critical_spreadsheet"),
            ("three", "sell_outcome_not_tool"),
        )
    ]
    stage_input = {
        "run_id": "run_one",
        "base_canvas_revision": 3,
        "context_snapshot": {
            "nodes": [{"id": "goal_one", "kind": "goal", "version": 1}],
            "edges": [],
        },
        "context_manifest": {"request": {"operation": "generate_strategies"}},
        "target_workset": [],
        "prior_stage_outputs": {
            "planning": {
                "output": {
                    "operation": "generate_strategies",
                    "strategies": planned_strategies,
                    "research_plans": [],
                }
            }
        },
    }
    operations: list[dict[str, object]] = []
    for index, strategy in enumerate(planned_strategies, start=1):
        strategy_id = str(strategy["id"])
        operations.extend(
            [
                {
                    "operation_id": f"add_strategy_{index}",
                    "op": "ADD_NODE",
                    "client_generated_id": strategy_id,
                    "node": {
                        "kind": "strategy",
                        "title": strategy["title"],
                        "body": strategy["approach"],
                        "metadata": {
                            "generated_by_run_id": "run_one",
                            "provenance_node_ids": ["goal_one"],
                            "approach": strategy["approach"],
                            "rationale": strategy["rationale"],
                            "strategy_template_id": strategy["template_id"],
                        },
                    },
                },
                {
                    "operation_id": f"link_strategy_{index}",
                    "op": "ADD_EDGE",
                    "depends_on": [f"add_strategy_{index}"],
                    "client_generated_id": f"edge_{index}",
                    "edge": {
                        "source_node_id": "goal_one",
                        "target_node_id": strategy_id,
                        "kind": "evolves_into",
                        "metadata": {"generated_by_run_id": "run_one"},
                    },
                },
            ]
        )
    valid_output = {
        "base_canvas_revision": 3,
        "known_node_ids": ["goal_one"],
        "known_edge_ids": [],
        "operations": operations,
    }

    validator.validate(
        "constructing_patch",
        StageResultEnvelope(
            stage_name="constructing_patch",
            output=valid_output,
            provider_identity="fixture:test",
        ),
        stage_input=stage_input,
    )

    empty_patch = {**valid_output, "operations": []}
    with pytest.raises(ValueError, match="missing a validated strategy candidate"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=empty_patch,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )

    wrong_direction = copy.deepcopy(valid_output)
    wrong_direction["operations"][1]["edge"].update(
        {"source_node_id": "strategy_one", "target_node_id": "goal_one"}
    )
    with pytest.raises(ValueError, match="exactly match validated semantic lineage"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=wrong_direction,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )

    extra_edge = copy.deepcopy(valid_output)
    extra_edge["operations"].append(
        {
            "operation_id": "extra_unbound_edge",
            "op": "ADD_EDGE",
            "depends_on": ["add_strategy_1"],
            "client_generated_id": "extra_edge",
            "edge": {
                "source_node_id": "goal_one",
                "target_node_id": "strategy_one",
                "kind": "supports",
                "metadata": {"generated_by_run_id": "run_one"},
            },
        }
    )
    with pytest.raises(ValueError, match="exactly match validated semantic lineage"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=extra_edge,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )

    wrong_kind = copy.deepcopy(valid_output)
    wrong_kind["operations"][0]["node"]["kind"] = "risk"
    with pytest.raises(ValueError, match="diverges from planning output"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=wrong_kind,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )

    wrong_metadata = copy.deepcopy(valid_output)
    wrong_metadata["operations"][0]["node"]["metadata"]["importance"] = "high"
    with pytest.raises(ValueError, match="wrong-kind fields"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=wrong_metadata,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )


def test_regeneration_patch_must_resolve_the_complete_frozen_stale_set() -> None:
    validator = PipelineStageOutputValidator()
    stage_input = {
        "run_id": "run_regeneration",
        "base_canvas_revision": 4,
        "context_snapshot": {
            "nodes": [
                {"id": "opportunity_target", "kind": "opportunity", "version": 2},
                {"id": "risk_member", "kind": "risk", "version": 2},
            ],
            "edges": [],
        },
        "context_manifest": {"request": {"operation": "regenerate_stale"}},
        "target_workset": [
            {
                "node_id": "opportunity_target",
                "kind": "opportunity",
                "stale_node_ids": ["opportunity_target", "risk_member"],
                "member_node_ids": ["opportunity_target", "risk_member"],
            }
        ],
        "prior_stage_outputs": {},
    }
    incomplete = {
        "base_canvas_revision": 4,
        "known_node_ids": ["opportunity_target", "risk_member"],
        "known_edge_ids": [],
        "operations": [],
        "regeneration_target_ids": ["opportunity_target"],
        "permitted_stale_resolution_ids": ["opportunity_target"],
    }

    with pytest.raises(ValueError, match="exact stale members"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=incomplete,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )


def test_opportunity_patch_edges_follow_each_evidence_signal_exactly() -> None:
    validator = PipelineStageOutputValidator()
    stage_input = synthesis_stage_input()
    stage_input.update({"run_id": "run_signal_edges", "base_canvas_revision": 0})
    opportunities = [
        opportunity(suffix="one"),
        opportunity(suffix="two"),
        opportunity(suffix="three"),
    ]
    critiques = [
        {
            "opportunity_id": candidate["id"],
            "novelty": "The workflow is differentiated by reviewer control.",
            "feasibility": "A bounded first version is feasible.",
            "buyer_and_budget": "Security leads own the recurring labor budget.",
            "recurrence": "Questionnaires recur across sales cycles.",
            "distribution": "Focused security communities are reachable.",
            "operational_burden": "Reviewer exceptions remain manageable.",
            "differentiation": "Provenance distinguishes the workflow.",
            "builder_fit": "A small technical team can build it.",
            "falsifying_evidence": "No paid pilot would falsify demand.",
            "material_contradiction_or_gap": "Some buyers prefer consulting.",
            "recommendation": "advance",
        }
        for candidate in opportunities
    ]
    normalized_synthesis = SynthesisOutput.model_validate_json(
        json.dumps(
            {
                "operation": "synthesize_opportunities",
                "opportunities": opportunities,
            }
        )
    ).model_dump(mode="json")
    stage_input["prior_stage_outputs"] = {
        "synthesizing": {"output": normalized_synthesis},
        "critiquing": {"output": {"critiques": critiques}},
    }
    valid_patch = _build_fixture_patch(stage_input)
    validator.validate(
        "constructing_patch",
        StageResultEnvelope(
            stage_name="constructing_patch",
            output=valid_patch,
            provider_identity="fixture:test",
        ),
        stage_input=stage_input,
    )

    wrong_signal_edge = copy.deepcopy(valid_patch)
    edge = next(
        operation["edge"]
        for operation in wrong_signal_edge["operations"]
        if operation["op"] == "ADD_EDGE"
        and operation["edge"]["source_node_id"] == "claim_cost"
        and operation["edge"]["target_node_id"] == "opportunity_one"
    )
    edge["kind"] = "contradicts"
    with pytest.raises(ValueError, match="exactly match validated semantic lineage"):
        validator.validate(
            "constructing_patch",
            StageResultEnvelope(
                stage_name="constructing_patch",
                output=wrong_signal_edge,
                provider_identity="fixture:test",
            ),
            stage_input=stage_input,
        )


def test_production_stage_validator_rejects_incomplete_or_extra_output() -> None:
    validator = PipelineStageOutputValidator()
    valid = StageResultEnvelope(
        stage_name="synthesizing",
        output={
            "operation": "synthesize_opportunities",
            "opportunities": [
                opportunity(suffix="one"),
                opportunity(suffix="two"),
                opportunity(suffix="three"),
            ],
        },
        provider_identity="fixture:test",
    )
    stage_input = synthesis_stage_input()

    validated = validator.validate("synthesizing", valid, stage_input=stage_input)
    assert validated.output["opportunities"][0]["support_status"] == "supported"

    invalid = valid.model_copy(update={"output": {**valid.output, "unexpected": True}})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validator.validate("synthesizing", invalid, stage_input=stage_input)
