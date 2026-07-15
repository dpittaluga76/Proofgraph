from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from proofgraph.evaluation.blinding import prepare_blind_packet
from proofgraph.evaluation.generation import OpenAIEvaluationGenerator, run_generation
from proofgraph.evaluation.scenarios import load_scenarios, scenario_set_hash
from proofgraph.evaluation.schemas import (
    DIMENSIONS,
    VARIANTS,
    Adjudication,
    AdjudicationArtifact,
    BlindPacket,
    CritiquedOpportunitySet,
    CritiqueItem,
    EvaluationGenerationRun,
    EvidenceAnalysis,
    EvidenceFinding,
    GeneratedVariant,
    Opportunity,
    OpportunitySet,
    PrivateBlindMap,
    RatingArtifact,
    StageRecord,
    StrategyCandidate,
    StrategyPlan,
)
from proofgraph.evaluation.scoring import analyze_ratings, bootstrap_interval


def _opportunity_set(*, evidence_ids: list[str] | None = None) -> OpportunitySet:
    evidence_ids = evidence_ids or []
    return OpportunitySet(
        opportunities=[
            Opportunity(
                title=f"Opportunity {index}",
                target_user="A specific operations manager",
                problem="A repeated operational problem consumes several staff hours every week.",
                product_wedge=(
                    "A narrow review queue catches exceptions before costly downstream work."
                ),
                why_now="The builder can reach design partners and test the workflow immediately.",
                evidence_ids=evidence_ids,
                assumptions=["The recurring problem has an accountable budget owner."],
                contradiction_or_gap=(
                    "The benchmark does not yet prove willingness to replace a workaround."
                ),
                validation_test=(
                    "Run a two-week concierge pilot and require three teams to use it twice."
                ),
                distribution=(
                    "Recruit initial teams through the builder's trusted professional network."
                ),
                pricing_logic="Charge a monthly fee below the measured labor and failure cost.",
                builder_fit=(
                    "The product uses the builder's domain access and existing software skills."
                ),
            )
            for index in range(1, 4)
        ]
    )


def _generation_run() -> EvaluationGenerationRun:
    scenarios = load_scenarios()
    outputs = [
        GeneratedVariant(
            scenario_id=scenario.scenario_id,
            variant_id=variant,
            opportunity_set=_opportunity_set(),
            stages=[StageRecord(stage="test", response_id=f"response-{scenario.scenario_id}")],
        )
        for scenario in scenarios.scenarios
        for variant in VARIANTS
    ]
    return EvaluationGenerationRun(
        run_id="evaluation-test-run",
        created_at="2026-07-15T00:00:00+00:00",
        scenario_set_version=scenarios.scenario_set_version,
        scenario_set_hash=scenario_set_hash(scenarios),
        model="gpt-5.6",
        max_output_tokens=4_500,
        prompt_version="comparative_evaluation_v1",
        strategy_version="opportunity_strategies_v1",
        generation_seed=27_001,
        generation_order=[f"{item.scenario_id}:{item.variant_id}" for item in outputs],
        outputs=outputs,
    )


class _FakeResponses:
    def __init__(self, evidence_ids: list[str]) -> None:
        self.evidence_ids = evidence_ids
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        response_model = kwargs["text_format"]
        if response_model is StrategyPlan:
            parsed = StrategyPlan(
                strategies=[
                    StrategyCandidate(
                        name=f"Strategy {index}",
                        approach=(
                            "Use a bounded recurring workflow with a measurable buyer outcome."
                        ),
                        builder_fit=(
                            "This approach uses the builder's access and implementation skills."
                        ),
                    )
                    for index in range(1, 4)
                ]
            )
        elif response_model is EvidenceAnalysis:
            parsed = EvidenceAnalysis(
                findings=[
                    EvidenceFinding(
                        evidence_id=evidence_id,
                        implication=(
                            "This evidence supports a concrete workflow and validation hypothesis."
                        ),
                    )
                    for evidence_id in self.evidence_ids
                ],
                unresolved_gaps=["The synthetic evidence does not prove paid demand."],
            )
        elif response_model is CritiquedOpportunitySet:
            opportunities = _opportunity_set(evidence_ids=self.evidence_ids)
            parsed = CritiquedOpportunitySet(
                critique=[
                    CritiqueItem(
                        opportunity_title=opportunity.title,
                        weaknesses=["The initial validation threshold needs more precision."],
                        required_revision=(
                            "State a time-bounded test and an observable buyer signal."
                        ),
                    )
                    for opportunity in opportunities.opportunities
                ],
                revised_opportunities=opportunities.opportunities,
            )
        else:
            payload = json.loads(
                str(kwargs["input"][-1]["content"])
                .removeprefix("UNTRUSTED_BENCHMARK_INPUT_START\n")
                .removesuffix("\nUNTRUSTED_BENCHMARK_INPUT_END")
            )
            parsed = _opportunity_set(
                evidence_ids=self.evidence_ids if "evidence_analysis" in payload else []
            )
        return SimpleNamespace(
            id=f"response-{len(self.calls)}",
            output_parsed=parsed,
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )


class _FakeClient:
    def __init__(self, evidence_ids: list[str]) -> None:
        self.responses = _FakeResponses(evidence_ids)


class _FastGenerator:
    model = "gpt-5.6"
    max_output_tokens = 4_500

    def __init__(self) -> None:
        self.calls = 0

    def generate_variant(self, scenario: object, variant_id: str) -> GeneratedVariant:
        self.calls += 1
        return GeneratedVariant(
            scenario_id=scenario.scenario_id,
            variant_id=variant_id,
            opportunity_set=_opportunity_set(),
            stages=[StageRecord(stage="fake", response_id=f"response-{self.calls}")],
        )


def _completed_rating(template: RatingArtifact, rater_id: str) -> RatingArtifact:
    artifact = template.model_copy(deep=True)
    artifact.rater_id = rater_id
    for rating in artifact.ratings:
        rating.scores = {dimension: 4 for dimension in DIMENSIONS}
    return artifact


def test_versioned_scenario_set_has_required_coverage() -> None:
    scenarios = load_scenarios()

    assert len(scenarios.scenarios) >= 20
    assert len({item.scenario_id for item in scenarios.scenarios}) == len(scenarios.scenarios)
    assert all(len(item.evidence) >= 3 for item in scenarios.scenarios)
    assert len(scenario_set_hash(scenarios)) == 64


def test_openai_generator_freezes_variant_stages_and_privacy() -> None:
    scenario = load_scenarios().scenarios[0]
    client = _FakeClient([item.evidence_id for item in scenario.evidence])
    generator = OpenAIEvaluationGenerator(client)

    generated = [generator.generate_variant(scenario, variant) for variant in VARIANTS]

    assert [len(item.stages) for item in generated] == [1, 2, 3, 4]
    assert [call["text_format"] for call in client.responses.calls] == [
        OpportunitySet,
        StrategyPlan,
        OpportunitySet,
        StrategyPlan,
        EvidenceAnalysis,
        OpportunitySet,
        StrategyPlan,
        EvidenceAnalysis,
        OpportunitySet,
        CritiquedOpportunitySet,
    ]
    assert all(call["model"] == "gpt-5.6" for call in client.responses.calls)
    assert all(call["store"] is False for call in client.responses.calls)
    assert len({call["max_output_tokens"] for call in client.responses.calls}) == 1


def test_generation_artifact_is_deterministic_and_resumable(tmp_path: Path) -> None:
    scenarios = load_scenarios()
    generator = _FastGenerator()
    output = tmp_path / "private-generation.json"

    first = run_generation(scenarios, generator, output, seed=123)
    first_call_count = generator.calls
    second = run_generation(scenarios, generator, output, seed=123)

    assert len(first.outputs) == len(scenarios.scenarios) * len(VARIANTS)
    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    assert generator.calls == first_call_count
    with pytest.raises(ValueError, match="run config"):
        run_generation(scenarios, generator, output, seed=456)


def test_blinding_is_deterministic_and_keeps_variant_map_private() -> None:
    scenarios = load_scenarios()
    run = _generation_run()

    first = prepare_blind_packet(scenarios, run, seed=123)
    repeated = prepare_blind_packet(scenarios, run, seed=123)
    changed = prepare_blind_packet(scenarios, run, seed=456)
    packet, private_map, rater_a, rater_b = first

    assert first[0].model_dump(mode="json") == repeated[0].model_dump(mode="json")
    assert packet.model_dump(mode="json") != changed[0].model_dump(mode="json")
    assert "variant_id" not in packet.model_dump_json()
    assert len(private_map.mappings) == len(scenarios.scenarios) * 4
    assert rater_a.rater_id == rater_b.rater_id == ""
    assert all(set(item.scores) == set(DIMENSIONS) for item in rater_a.ratings)
    assert all(score is None for item in rater_a.ratings for score in item.scores.values())


def test_scoring_requires_exact_adjudication_and_reports_paired_ci() -> None:
    scenarios = load_scenarios()
    generation = _generation_run()
    packet, private_map, rater_a_template, rater_b_template = prepare_blind_packet(
        scenarios,
        generation,
        seed=123,
    )
    rater_a = _completed_rating(rater_a_template, "rater-a")
    rater_b = _completed_rating(rater_b_template, "rater-b")
    mapping = {item.blind_output_id: item.variant_id for item in private_map.mappings}
    for artifact in (rater_a, rater_b):
        for rating in artifact.ratings:
            score = 5 if mapping[rating.blind_output_id] == "full_pipeline" else 3
            rating.scores = {dimension: score for dimension in DIMENSIONS}

    disputed = rater_b.ratings[0]
    disputed.scores["novelty"] = 3 if disputed.scores["novelty"] == 5 else 5
    empty = AdjudicationArtifact(packet_id=packet.packet_id, adjudications=[])
    with pytest.raises(ValueError, match="exactly cover"):
        analyze_ratings(packet, private_map, rater_a, rater_b, empty, generation)

    adjudications = AdjudicationArtifact(
        packet_id=packet.packet_id,
        adjudications=[
            Adjudication(
                blind_output_id=disputed.blind_output_id,
                dimension="novelty",
                adjudicator_id="adjudicator",
                resolved_score=4,
                rationale="The resolved score reflects the rubric anchor and both rater notes.",
            )
        ],
    )
    report = analyze_ratings(
        packet,
        private_map,
        rater_a,
        rater_b,
        adjudications,
        generation,
    )

    assert report["acceptance_passed"] is True
    assert report["dimensions"]["specificity"]["mean_full_minus_generic"] == 2
    assert report["dimensions"]["specificity"]["bootstrap_95_ci"] == [2, 2]
    assert len(report["original_ratings"]) == 2
    assert len(report["adjudications"]["adjudications"]) == 1
    assert bootstrap_interval([1.0] * 20) == (1.0, 1.0)


def test_generation_command_requires_explicit_cost_confirmation(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="confirm-cost"):
        call_command("generate_evaluation_variants", output=tmp_path / "generation.json")


def test_offline_management_commands_create_and_analyze_artifacts(tmp_path: Path) -> None:
    generation = _generation_run()
    generation_path = tmp_path / "private-generation.json"
    generation_path.write_text(generation.model_dump_json(indent=2), encoding="utf-8")
    rating_dir = tmp_path / "rating"

    call_command(
        "prepare_evaluation_packet",
        generation=generation_path,
        output_dir=rating_dir,
        seed=321,
        verbosity=0,
    )

    packet = BlindPacket.model_validate_json(
        (rating_dir / "blind-packet.json").read_text(encoding="utf-8")
    )
    private_map = PrivateBlindMap.model_validate_json(
        (rating_dir / "private-variant-map.json").read_text(encoding="utf-8")
    )
    for filename, rater_id in (
        ("rating-rater-a.json", "rater-a"),
        ("rating-rater-b.json", "rater-b"),
    ):
        path = rating_dir / filename
        rating = RatingArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        completed = _completed_rating(rating, rater_id)
        path.write_text(completed.model_dump_json(indent=2), encoding="utf-8")

    result_json = tmp_path / "result.json"
    result_markdown = tmp_path / "result.md"
    call_command(
        "analyze_evaluation",
        packet=rating_dir / "blind-packet.json",
        private_map=rating_dir / "private-variant-map.json",
        generation=generation_path,
        rater_a=rating_dir / "rating-rater-a.json",
        rater_b=rating_dir / "rating-rater-b.json",
        adjudications=rating_dir / "adjudications.json",
        output_json=result_json,
        output_markdown=result_markdown,
        verbosity=0,
    )

    report = json.loads(result_json.read_text(encoding="utf-8"))
    assert report["packet_id"] == packet.packet_id == private_map.packet_id
    assert report["acceptance_passed"] is False
    assert "Overall required-dimension result: **FAIL**" in result_markdown.read_text(
        encoding="utf-8"
    )
