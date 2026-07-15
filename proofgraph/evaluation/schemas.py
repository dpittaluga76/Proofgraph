from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ScenarioId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{2,79}$")]
VariantId = Literal[
    "generic",
    "strategy_only",
    "strategy_plus_evidence",
    "full_pipeline",
]
DimensionId = Literal[
    "specificity",
    "evidence_relevance",
    "novelty",
    "feasibility",
    "economic_leverage",
    "testability",
    "builder_fit",
]

VARIANTS: tuple[VariantId, ...] = (
    "generic",
    "strategy_only",
    "strategy_plus_evidence",
    "full_pipeline",
)
DIMENSIONS: tuple[DimensionId, ...] = (
    "specificity",
    "evidence_relevance",
    "novelty",
    "feasibility",
    "economic_leverage",
    "testability",
    "builder_fit",
)
REQUIRED_DIMENSIONS: tuple[DimensionId, ...] = (
    "evidence_relevance",
    "specificity",
    "testability",
    "builder_fit",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BenchmarkEvidence(StrictModel):
    evidence_id: ScenarioId
    observation: str = Field(min_length=10, max_length=1_000)
    relevance: str = Field(min_length=10, max_length=500)
    limitation: str = Field(min_length=5, max_length=500)


class BuilderScenario(StrictModel):
    scenario_id: ScenarioId
    title: str = Field(min_length=5, max_length=200)
    builder_profile: str = Field(min_length=20, max_length=1_000)
    goal: str = Field(min_length=10, max_length=500)
    constraints: list[str] = Field(min_length=2, max_length=8)
    advantages: list[str] = Field(min_length=1, max_length=6)
    preferences: list[str] = Field(min_length=1, max_length=6)
    evidence: list[BenchmarkEvidence] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def unique_evidence_ids(self) -> BuilderScenario:
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario evidence IDs must be unique")
        return self

    def core_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"evidence"})


class ScenarioSet(StrictModel):
    schema_version: Literal[1] = 1
    scenario_set_version: str = Field(min_length=1, max_length=100)
    scenarios: list[BuilderScenario] = Field(min_length=20, max_length=25)

    @model_validator(mode="after")
    def unique_scenario_ids(self) -> ScenarioSet:
        ids = [item.scenario_id for item in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario IDs must be unique")
        return self


class StrategyCandidate(StrictModel):
    name: str = Field(min_length=3, max_length=200)
    approach: str = Field(min_length=20, max_length=1_000)
    builder_fit: str = Field(min_length=10, max_length=500)


class StrategyPlan(StrictModel):
    strategies: list[StrategyCandidate] = Field(min_length=3, max_length=3)


class EvidenceFinding(StrictModel):
    evidence_id: ScenarioId
    implication: str = Field(min_length=10, max_length=1_000)
    supports: list[str] = Field(default_factory=list, max_length=5)
    weakens: list[str] = Field(default_factory=list, max_length=5)


class EvidenceAnalysis(StrictModel):
    findings: list[EvidenceFinding] = Field(min_length=3, max_length=6)
    unresolved_gaps: list[str] = Field(min_length=1, max_length=6)


class Opportunity(StrictModel):
    title: str = Field(min_length=3, max_length=200)
    target_user: str = Field(min_length=5, max_length=300)
    problem: str = Field(min_length=20, max_length=1_000)
    product_wedge: str = Field(min_length=20, max_length=1_000)
    why_now: str = Field(min_length=10, max_length=700)
    evidence_ids: list[ScenarioId] = Field(default_factory=list, max_length=6)
    assumptions: list[str] = Field(min_length=1, max_length=6)
    contradiction_or_gap: str = Field(min_length=10, max_length=700)
    validation_test: str = Field(min_length=20, max_length=1_000)
    distribution: str = Field(min_length=10, max_length=700)
    pricing_logic: str = Field(min_length=10, max_length=700)
    builder_fit: str = Field(min_length=10, max_length=700)


class OpportunitySet(StrictModel):
    opportunities: list[Opportunity] = Field(min_length=3, max_length=3)


class CritiqueItem(StrictModel):
    opportunity_title: str = Field(min_length=3, max_length=200)
    weaknesses: list[str] = Field(min_length=1, max_length=6)
    required_revision: str = Field(min_length=10, max_length=700)


class CritiquedOpportunitySet(StrictModel):
    critique: list[CritiqueItem] = Field(min_length=3, max_length=3)
    revised_opportunities: list[Opportunity] = Field(min_length=3, max_length=3)


class TokenUsageRecord(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class StageRecord(StrictModel):
    stage: str = Field(min_length=1, max_length=100)
    response_id: str = Field(min_length=1, max_length=200)
    token_usage: TokenUsageRecord | None = None


class GeneratedVariant(StrictModel):
    scenario_id: ScenarioId
    variant_id: VariantId
    opportunity_set: OpportunitySet
    stages: list[StageRecord] = Field(min_length=1, max_length=5)


class EvaluationGenerationRun(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=200)
    created_at: str
    scenario_set_version: str
    scenario_set_hash: str
    model: str
    reasoning_effort: Literal["medium"] = "medium"
    max_output_tokens: int = Field(ge=1)
    api_storage: Literal[False] = False
    prompt_version: str
    strategy_version: str
    generation_seed: int
    generation_order: list[str]
    outputs: list[GeneratedVariant]

    @model_validator(mode="after")
    def unique_outputs(self) -> EvaluationGenerationRun:
        keys = [(item.scenario_id, item.variant_id) for item in self.outputs]
        if len(keys) != len(set(keys)):
            raise ValueError("generation outputs must contain unique scenario/variant pairs")
        return self


class BlindOutput(StrictModel):
    blind_output_id: str = Field(min_length=8, max_length=100)
    opportunity_set: OpportunitySet


class BlindScenario(StrictModel):
    scenario: BuilderScenario
    outputs: list[BlindOutput] = Field(min_length=4, max_length=4)


class RubricDimension(StrictModel):
    dimension: DimensionId
    one: str
    three: str
    five: str


class BlindPacket(StrictModel):
    schema_version: Literal[1] = 1
    packet_id: str
    scenario_set_version: str
    rubric_version: str
    rubric: list[RubricDimension] = Field(min_length=7, max_length=7)
    scenarios: list[BlindScenario] = Field(min_length=20, max_length=25)

    @model_validator(mode="after")
    def unique_packet_identity(self) -> BlindPacket:
        scenario_ids = [item.scenario.scenario_id for item in self.scenarios]
        output_ids = [
            output.blind_output_id for scenario in self.scenarios for output in scenario.outputs
        ]
        rubric_dimensions = [item.dimension for item in self.rubric]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("blind packet scenario IDs must be unique")
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("blind output IDs must be globally unique")
        if set(rubric_dimensions) != set(DIMENSIONS):
            raise ValueError("blind packet must contain every rubric dimension exactly once")
        return self


class VariantMapping(StrictModel):
    blind_output_id: str
    scenario_id: ScenarioId
    variant_id: VariantId


class PrivateBlindMap(StrictModel):
    schema_version: Literal[1] = 1
    packet_id: str
    randomization_seed: int
    generation_run_id: str
    mappings: list[VariantMapping]


class RatingEntry(StrictModel):
    blind_output_id: str
    scores: dict[DimensionId, int | None]
    notes: str = Field(default="", max_length=2_000)


class RatingArtifact(StrictModel):
    schema_version: Literal[1] = 1
    packet_id: str
    rater_id: str = Field(max_length=100)
    ratings: list[RatingEntry]

    @model_validator(mode="after")
    def unique_rating_outputs(self) -> RatingArtifact:
        ids = [item.blind_output_id for item in self.ratings]
        if len(ids) != len(set(ids)):
            raise ValueError("rating output IDs must be unique")
        return self


class Adjudication(StrictModel):
    blind_output_id: str
    dimension: DimensionId
    adjudicator_id: str = Field(min_length=1, max_length=100)
    resolved_score: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=10, max_length=2_000)


class AdjudicationArtifact(StrictModel):
    schema_version: Literal[1] = 1
    packet_id: str
    adjudications: list[Adjudication]
