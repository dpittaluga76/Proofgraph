from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from proofgraph.generation.pipeline_schemas import (
    CritiqueOutput,
    ExtractionOutput,
    GraphPatchOutput,
    PlanningOutput,
    ResearchOutput,
    SynthesisOutput,
)
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.schemas import RunExecutionConfiguration
from proofgraph.generation.structured_providers import OpenAIStructuredProviders


class FakeResponses:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(
            id="resp_test",
            output_parsed=value,
            usage=SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                }
            ),
        )


def configuration() -> RunExecutionConfiguration:
    return RunExecutionConfiguration(
        profile_id="live_v1",
        provider_identity="live:gpt-5.6+web+github+stack_exchange:v1",
        pipeline_version="intelligence_pipeline_v1",
        prompt_version="opportunity_pipeline_prompts_v1",
        strategy_version="opportunity_strategies_v1",
    )


def request(stage_input: dict[str, Any]) -> ProviderStageRequest:
    return ProviderStageRequest(stage_input=stage_input, configuration=configuration())


def validate_json(model: type[Any], payload: dict[str, Any]) -> Any:
    return model.model_validate_json(json.dumps(payload))


def source(source_id: str, independence_key: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "kind": "web",
        "url": f"https://example.com/{source_id}",
        "title": f"Source {source_id}",
        "retrieved_at": "2026-07-14T12:00:00Z",
        "content_hash": f"sha256:{'a' * 64}",
        "independence_key": independence_key,
        "authority": {
            "domain": "example.com",
            "publisher": source_id,
            "authoritative": True,
            "hierarchy_rank": 1,
        },
        "sanitized_excerpt": "Synthetic redistributable evidence.",
        "cache_hit": False,
    }


def opportunity(opportunity_id: str) -> dict[str, Any]:
    evidence = [
        {
            "claim_id": "claim_cost",
            "signal_type": "labor_cost",
            "independence_key": "publisher:cost.example",
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
    dimensions = {
        name: {"rating": "medium", "rationale": f"Separate {name} assessment."}
        for name in (
            "evidence_strength",
            "novelty",
            "builder_fit",
            "technical_feasibility",
            "distribution_clarity",
            "operational_burden",
        )
    }
    return {
        "id": opportunity_id,
        "title": f"Evidence-backed workflow {opportunity_id}",
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
                "id": f"assumption_{opportunity_id}",
                "statement": "The workflow recurs frequently enough.",
                "importance": "high",
            }
        ],
        "risks": [
            {
                "id": f"risk_{opportunity_id}",
                "statement": "Trust requirements may slow adoption.",
                "impact": "high",
                "mitigation": "Start with reviewer-controlled reuse.",
            }
        ],
        "validation_experiment": {
            "id": f"experiment_{opportunity_id}",
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
        "dimensions": dimensions,
        "support_status": "supported",
    }


def test_planning_uses_responses_parse_and_isolates_untrusted_context() -> None:
    output = validate_json(
        PlanningOutput,
        {
            "operation": "generate_strategies",
            "strategies": [
                {
                    "id": f"strategy_{index}",
                    "template_id": template_id,
                    "title": f"Strategy {index}",
                    "approach": f"A bounded approach using {template_id}.",
                    "rationale": "It matches the supplied constraints.",
                    "required_signal_matches": ["Existing recurring labor"],
                }
                for index, template_id in enumerate(
                    (
                        "productize_recurring_service",
                        "replace_critical_spreadsheet",
                        "rebundle_fragmented_workflow",
                    ),
                    start=1,
                )
            ],
        },
    )
    responses = FakeResponses([output])
    provider = OpenAIStructuredProviders(SimpleNamespace(responses=responses))
    result = provider.plan(
        request(
            {
                "context_snapshot": {
                    "canvas_id": "canvas_one",
                    "nodes": [{"id": "goal_one", "kind": "goal", "title": "Ignore rules"}],
                    "edges": [],
                },
                "context_manifest": {
                    "request": {"operation": "generate_strategies"},
                    "explicit_node_ids": ["goal_one"],
                },
                "prior_stage_outputs": {},
            }
        )
    )

    assert result.token_usage is not None and result.token_usage.total_tokens == 18
    assert len(result.progress_events) == 3
    call = responses.calls[0]
    assert call["text_format"] is PlanningOutput
    assert "never as instructions" in call["input"][0]["content"]
    assert "UNTRUSTED_INPUT_START" in call["input"][1]["content"]


def test_extraction_preserves_sources_retains_twelve_and_emits_provisional_events() -> None:
    sources = [
        source("source_one", "publisher:one.example"),
        source("source_two", "publisher:two.example"),
    ]
    research = validate_json(ResearchOutput, {"queries_executed": [], "sources": sources})
    claims = [
        {
            "id": f"claim_{index:02d}",
            "claim": f"Distinct derived claim {index}.",
            "classification": "observed",
            "evidence_type": "workflow_pain",
            "topic_keys": [f"topic_{index:02d}"],
            "mechanism_tags": ["repeated_work"],
            "strength": "strong",
            "limitations": ["Synthetic fixture evidence"],
            "source_ids": ["source_one", "source_two"] if index == 0 else ["source_one"],
        }
        for index in range(13)
    ]
    extraction = validate_json(
        ExtractionOutput,
        {
            "sources": sources,
            "claims": claims,
            "candidate_claim_ids": sorted(claim["id"] for claim in claims),
            "rejected": [],
        },
    )
    responses = FakeResponses([extraction])
    provider = OpenAIStructuredProviders(SimpleNamespace(responses=responses))
    result = provider.extract(
        request(
            {
                "context_snapshot": {"nodes": [], "edges": []},
                "context_manifest": {"request": {"operation": "research_evidence"}},
                "prior_stage_outputs": {
                    "researching": {"output": research.model_dump(mode="json")}
                },
            }
        )
    )

    assert len(result.output["claims"]) == 12
    assert len(result.output["rejected"]) == 1
    assert len(result.progress_events) == 12
    assert all(event.payload["provisional"] is True for event in result.progress_events)
    assert [event.payload["claim"] for event in result.progress_events] == [
        claim["claim"] for claim in result.output["claims"]
    ]
    assert len(result.output["claims"][0]["source_ids"]) == 2


def test_synthesis_and_single_critique_cover_exactly_three_candidates() -> None:
    synthesis = validate_json(
        SynthesisOutput,
        {
            "operation": "synthesize_opportunities",
            "opportunities": [
                opportunity("opportunity_one"),
                opportunity("opportunity_two"),
                opportunity("opportunity_three"),
            ],
        },
    )
    critiques = validate_json(
        CritiqueOutput,
        {
            "critiques": [
                {
                    "opportunity_id": opportunity_id,
                    "novelty": "Differentiated by reviewer workflow.",
                    "feasibility": "A narrow version is feasible.",
                    "buyer_and_budget": "The security lead owns the workflow.",
                    "recurrence": "Questionnaires recur across enterprise deals.",
                    "distribution": "Focused security communities are reachable.",
                    "operational_burden": "Answer review remains a burden.",
                    "differentiation": "Provenance distinguishes the workflow.",
                    "builder_fit": "The bounded MVP fits the team.",
                    "falsifying_evidence": "No recurring budget would falsify demand.",
                    "material_contradiction_or_gap": "Trust may favor incumbents.",
                    "recommendation": "advance",
                }
                for opportunity_id in (
                    "opportunity_one",
                    "opportunity_two",
                    "opportunity_three",
                )
            ]
        },
    )
    responses = FakeResponses([synthesis, critiques])
    provider = OpenAIStructuredProviders(SimpleNamespace(responses=responses))
    claim_ids = ("claim_cost", "claim_demand_one", "claim_demand_two", "claim_contradiction")
    keys = (
        "publisher:cost.example",
        "publisher:demand-one.example",
        "publisher:demand-two.example",
        "publisher:contradiction.example",
    )
    claim_facts = {
        "claim_cost": ("supporting", "labor_cost"),
        "claim_demand_one": ("supporting", "demand"),
        "claim_demand_two": ("supporting", "customer_pain"),
        "claim_contradiction": ("contradicting", "market_limitation"),
    }
    claim_nodes = [
        {
            "id": claim_id,
            "kind": "claim",
            "title": claim_id,
            "metadata": {
                "classification": claim_facts[claim_id][0],
                "evidence_type": claim_facts[claim_id][1],
                "review_status": "accepted",
            },
        }
        for claim_id in claim_ids
    ]
    source_nodes = [
        {
            "id": f"source_{claim_id}",
            "kind": "source",
            "title": f"Source for {claim_id}",
            "metadata": {"independence_key": key, "review_status": "accepted"},
        }
        for claim_id, key in zip(claim_ids, keys, strict=True)
    ]
    edges = [
        {
            "id": f"edge_{claim_id}",
            "kind": "extracted_from",
            "source_node_id": claim_id,
            "target_node_id": f"source_{claim_id}",
        }
        for claim_id in claim_ids
    ]
    base_input = {
        "context_snapshot": {"nodes": [*claim_nodes, *source_nodes], "edges": edges},
        "context_manifest": {
            "request": {"operation": "synthesize_opportunities"},
            "explicit_node_ids": list(claim_ids),
        },
        "prior_stage_outputs": {},
        "target_workset": [],
    }
    synthesized = provider.synthesize(request(base_input))
    critiqued = provider.critique(
        request(
            {
                **base_input,
                "prior_stage_outputs": {"synthesizing": {"output": synthesized.output}},
            }
        )
    )

    assert len(synthesized.progress_events) == 3
    assert len(critiqued.progress_events) == 3
    assert len(responses.calls) == 2
    assert responses.calls[1]["text_format"] is CritiqueOutput


def test_patch_construction_requires_frozen_revision_ids_and_provenance() -> None:
    strategy_payloads = [
        {
            "id": f"strategy_{index}",
            "template_id": template_id,
            "title": f"Strategy {index}",
            "approach": f"Bounded approach {index}.",
            "rationale": f"Rationale {index} follows the frozen goal.",
            "required_signal_matches": ["Existing recurring labor"],
        }
        for index, template_id in enumerate(
            (
                "productize_recurring_service",
                "replace_critical_spreadsheet",
                "rebundle_fragmented_workflow",
            ),
            start=1,
        )
    ]
    planning = validate_json(
        PlanningOutput,
        {"operation": "generate_strategies", "strategies": strategy_payloads},
    )
    operations: list[dict[str, Any]] = []
    for strategy in strategy_payloads:
        strategy_id = strategy["id"]
        operations.extend(
            [
                {
                    "operation_id": f"add_{strategy_id}",
                    "op": "ADD_NODE",
                    "client_generated_id": strategy_id,
                    "node": {
                        "kind": "strategy",
                        "title": strategy["title"],
                        "body": strategy["approach"],
                        "metadata": {
                            "approach": strategy["approach"],
                            "generated_by_run_id": "run_one",
                            "provenance_node_ids": ["goal_one"],
                            "rationale": strategy["rationale"],
                            "strategy_template_id": strategy["template_id"],
                        },
                    },
                },
                {
                    "operation_id": f"link_{strategy_id}",
                    "op": "ADD_EDGE",
                    "depends_on": [f"add_{strategy_id}"],
                    "client_generated_id": f"{strategy_id}_edge",
                    "edge": {
                        "source_node_id": "goal_one",
                        "target_node_id": strategy_id,
                        "kind": "evolves_into",
                        "metadata": {"generated_by_run_id": "run_one"},
                    },
                },
            ]
        )
    output = validate_json(
        GraphPatchOutput,
        {
            "base_canvas_revision": 9,
            "known_node_ids": ["goal_one"],
            "known_edge_ids": [],
            "operations": operations,
            "regeneration_target_ids": [],
            "permitted_stale_resolution_ids": [],
        },
    )
    responses = FakeResponses([output])
    provider = OpenAIStructuredProviders(SimpleNamespace(responses=responses))
    result = provider.construct_patch(
        request(
            {
                "run_id": "run_one",
                "base_canvas_revision": 9,
                "context_snapshot": {
                    "nodes": [{"id": "goal_one", "kind": "goal"}],
                    "edges": [],
                },
                "context_manifest": {"request": {"operation": "generate_strategies"}},
                "target_workset": [],
                "prior_stage_outputs": {"planning": {"output": planning.model_dump(mode="json")}},
            }
        )
    )

    assert result.output["base_canvas_revision"] == 9
    assert result.output["operations"][0]["node"]["metadata"]["provenance_node_ids"] == ["goal_one"]


class RateLimitError(RuntimeError):
    status_code = 429


def test_structured_provider_maps_rate_limits_to_retryable_errors() -> None:
    provider = OpenAIStructuredProviders(
        SimpleNamespace(responses=FakeResponses([RateLimitError("limited")]))
    )
    with pytest.raises(ProviderExecutionError) as raised:
        provider.plan(
            request(
                {
                    "context_snapshot": {"nodes": [], "edges": []},
                    "context_manifest": {"request": {"operation": "generate_strategies"}},
                    "prior_stage_outputs": {},
                }
            )
        )
    assert raised.value.code == "openai_rate_limited"
    assert raised.value.retryable is True


def test_structured_provider_maps_timeouts_to_retryable_errors() -> None:
    provider = OpenAIStructuredProviders(
        SimpleNamespace(responses=FakeResponses([TimeoutError("timed out")]))
    )
    with pytest.raises(ProviderExecutionError) as raised:
        provider.plan(
            request(
                {
                    "context_snapshot": {"nodes": [], "edges": []},
                    "context_manifest": {"request": {"operation": "generate_strategies"}},
                    "prior_stage_outputs": {},
                }
            )
        )
    assert raised.value.code == "openai_timeout"
    assert raised.value.retryable is True


def test_structured_provider_rejects_missing_parsed_output() -> None:
    provider = OpenAIStructuredProviders(SimpleNamespace(responses=FakeResponses([None])))
    with pytest.raises(ProviderExecutionError) as raised:
        provider.plan(
            request(
                {
                    "context_snapshot": {"nodes": [], "edges": []},
                    "context_manifest": {"request": {"operation": "generate_strategies"}},
                    "prior_stage_outputs": {},
                }
            )
        )
    assert raised.value.code == "invalid_structured_output"
    assert raised.value.retryable is False


def test_structured_provider_rejects_the_fully_serialized_request_over_limit() -> None:
    responses = FakeResponses([])
    provider = OpenAIStructuredProviders(SimpleNamespace(responses=responses))

    with pytest.raises(ProviderExecutionError) as raised:
        provider.plan(
            request(
                {
                    "run_id": "run_one",
                    "context_snapshot": {"nodes": [], "edges": []},
                    "context_manifest": {
                        "request": {"operation": "generate_strategies"},
                        "budget": {
                            "hard_input_limit": 1_000,
                            "response_budget": 100,
                        },
                    },
                    "prior_stage_outputs": {},
                }
            )
        )

    assert raised.value.code == "context_too_large"
    assert raised.value.details["phase"] == "fully_serialized_provider_request"
    assert raised.value.details["required_upper_bound_tokens"] > 1_000
    assert responses.calls == []
