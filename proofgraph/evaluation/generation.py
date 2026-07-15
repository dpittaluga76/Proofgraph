from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from proofgraph.evaluation.artifacts import write_json_atomic
from proofgraph.evaluation.scenarios import scenario_set_hash
from proofgraph.evaluation.schemas import (
    VARIANTS,
    CritiquedOpportunitySet,
    EvaluationGenerationRun,
    EvidenceAnalysis,
    GeneratedVariant,
    OpportunitySet,
    ScenarioSet,
    StageRecord,
    StrategyPlan,
    TokenUsageRecord,
    VariantId,
)
from proofgraph.generation.strategies import STRATEGY_CATALOG_VERSION, STRATEGY_TEMPLATES

EVALUATION_MODEL = "gpt-5.6"
EVALUATION_PROMPT_VERSION = "comparative_evaluation_v1"
EVALUATION_MAX_OUTPUT_TOKENS = 4_500

_StructuredModel = TypeVar("_StructuredModel", bound=BaseModel)

_SYSTEM_PROMPTS = {
    "generic": (
        "Generate exactly three commercially plausible software opportunities for the supplied "
        "builder. Work directly from the builder scenario; do not use a named strategy catalog "
        "or evidence-analysis method. Keep evidence_ids empty. Return only the requested schema."
    ),
    "planning": (
        "Select exactly three materially different opportunity strategies for this builder using "
        "the supplied strategy catalog. Explain builder fit. Return only the requested schema."
    ),
    "evidence": (
        "Analyze every supplied synthetic benchmark evidence item. Preserve evidence IDs exactly, "
        "state implications, and identify unresolved gaps. Never invent evidence. Return only the "
        "requested schema."
    ),
    "opportunities": (
        "Generate exactly three commercially plausible software opportunities from the supplied "
        "builder scenario and strategy plan. Use the evidence analysis only when it is supplied. "
        "Cite only supplied evidence IDs, make assumptions explicit, include one contradiction or "
        "gap per opportunity, and propose a concrete validation test. Return only the schema."
    ),
    "critique": (
        "Perform exactly one rigorous critique and revision pass over all three draft "
        "opportunities. Improve specificity, evidence relevance, feasibility, testability, "
        "economic leverage, novelty, and builder fit without inventing evidence. Preserve only "
        "supplied evidence IDs. Return only the requested schema."
    ),
}


def _object_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _token_usage(response: Any) -> TokenUsageRecord | None:
    usage = _object_payload(getattr(response, "usage", None))
    if not usage:
        usage = _object_payload(response).get("usage") or {}
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    if total_tokens == 0:
        return None
    return TokenUsageRecord(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _untrusted_message(payload: dict[str, object]) -> str:
    return (
        "UNTRUSTED_BENCHMARK_INPUT_START\n"
        f"{json.dumps(payload, sort_keys=True, ensure_ascii=False)}\n"
        "UNTRUSTED_BENCHMARK_INPUT_END"
    )


class OpenAIEvaluationGenerator:
    """The cost-bearing, structured-output side of the evaluation harness."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = EVALUATION_MODEL,
        max_output_tokens: int = EVALUATION_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens

    def _parse(
        self,
        stage: str,
        payload: dict[str, object],
        response_model: type[_StructuredModel],
    ) -> tuple[_StructuredModel, StageRecord]:
        response = self.client.responses.parse(
            model=self.model,
            reasoning={"effort": "medium"},
            input=[
                {"role": "system", "content": _SYSTEM_PROMPTS[stage]},
                {"role": "user", "content": _untrusted_message(payload)},
            ],
            max_output_tokens=self.max_output_tokens,
            text_format=response_model,
            store=False,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RuntimeError(f"The {stage} evaluation call returned no parsed output.")
        if not isinstance(parsed, response_model):
            raw = parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else parsed
            parsed = response_model.model_validate(raw)
        response_id = getattr(response, "id", None) or _object_payload(response).get("id")
        if not response_id:
            raise RuntimeError(f"The {stage} evaluation call returned no response ID.")
        return parsed, StageRecord(
            stage=stage,
            response_id=str(response_id),
            token_usage=_token_usage(response),
        )

    @staticmethod
    def _validate_evidence_ids(
        opportunities: OpportunitySet,
        *,
        allowed_ids: set[str],
    ) -> None:
        cited = {
            evidence_id
            for opportunity in opportunities.opportunities
            for evidence_id in opportunity.evidence_ids
        }
        unknown = cited - allowed_ids
        if unknown:
            raise ValueError(
                f"Generated opportunities cited unknown evidence IDs: {sorted(unknown)}"
            )

    def generate_variant(self, scenario: Any, variant_id: VariantId) -> GeneratedVariant:
        core = scenario.core_payload()
        evidence = [item.model_dump(mode="json") for item in scenario.evidence]
        strategy_catalog = [item.model_dump(mode="json") for item in STRATEGY_TEMPLATES]
        allowed_evidence_ids = {item.evidence_id for item in scenario.evidence}
        stages: list[StageRecord] = []

        if variant_id == "generic":
            opportunities, record = self._parse(
                "generic",
                {"scenario": core, "requested_opportunity_count": 3},
                OpportunitySet,
            )
            stages.append(record)
            assert isinstance(opportunities, OpportunitySet)
            self._validate_evidence_ids(opportunities, allowed_ids=set())
            return GeneratedVariant(
                scenario_id=scenario.scenario_id,
                variant_id=variant_id,
                opportunity_set=opportunities,
                stages=stages,
            )

        plan, record = self._parse(
            "planning",
            {"scenario": core, "strategy_catalog": strategy_catalog},
            StrategyPlan,
        )
        stages.append(record)
        assert isinstance(plan, StrategyPlan)
        evidence_analysis: EvidenceAnalysis | None = None
        if variant_id in {"strategy_plus_evidence", "full_pipeline"}:
            evidence_analysis, record = self._parse(
                "evidence",
                {
                    "scenario": core,
                    "strategy_plan": plan.model_dump(mode="json"),
                    "evidence": evidence,
                },
                EvidenceAnalysis,
            )
            stages.append(record)
            assert isinstance(evidence_analysis, EvidenceAnalysis)
            finding_ids = [finding.evidence_id for finding in evidence_analysis.findings]
            if set(finding_ids) != allowed_evidence_ids or len(finding_ids) != len(
                allowed_evidence_ids
            ):
                raise ValueError(
                    "Evidence analysis must cover every and only supplied evidence ID."
                )

        opportunity_payload: dict[str, object] = {
            "scenario": core,
            "strategy_plan": plan.model_dump(mode="json"),
            "requested_opportunity_count": 3,
        }
        if evidence_analysis is not None:
            opportunity_payload["evidence"] = evidence
            opportunity_payload["evidence_analysis"] = evidence_analysis.model_dump(mode="json")
        opportunities, record = self._parse(
            "opportunities",
            opportunity_payload,
            OpportunitySet,
        )
        stages.append(record)
        assert isinstance(opportunities, OpportunitySet)
        allowed = allowed_evidence_ids if evidence_analysis is not None else set()
        self._validate_evidence_ids(opportunities, allowed_ids=allowed)

        if variant_id == "full_pipeline":
            critiqued, record = self._parse(
                "critique",
                {
                    **opportunity_payload,
                    "draft_opportunities": opportunities.model_dump(mode="json"),
                },
                CritiquedOpportunitySet,
            )
            stages.append(record)
            assert isinstance(critiqued, CritiquedOpportunitySet)
            opportunities = OpportunitySet(opportunities=critiqued.revised_opportunities)
            self._validate_evidence_ids(opportunities, allowed_ids=allowed_evidence_ids)

        return GeneratedVariant(
            scenario_id=scenario.scenario_id,
            variant_id=variant_id,
            opportunity_set=opportunities,
            stages=stages,
        )


def run_generation(
    scenarios: ScenarioSet,
    generator: OpenAIEvaluationGenerator,
    output_path: Path,
    *,
    seed: int,
    prompt_version: str = EVALUATION_PROMPT_VERSION,
    strategy_version: str = STRATEGY_CATALOG_VERSION,
) -> EvaluationGenerationRun:
    scenario_hash = scenario_set_hash(scenarios)
    generation_order = [
        f"{scenario.scenario_id}:{variant}"
        for scenario in scenarios.scenarios
        for variant in VARIANTS
    ]
    random.Random(seed).shuffle(generation_order)

    if output_path.exists():
        run = EvaluationGenerationRun.model_validate_json(output_path.read_text(encoding="utf-8"))
        expected = (
            scenarios.scenario_set_version,
            scenario_hash,
            generator.model,
            generator.max_output_tokens,
            prompt_version,
            strategy_version,
            seed,
            generation_order,
        )
        actual = (
            run.scenario_set_version,
            run.scenario_set_hash,
            run.model,
            run.max_output_tokens,
            run.prompt_version,
            run.strategy_version,
            run.generation_seed,
            run.generation_order,
        )
        if actual != expected:
            raise ValueError(
                "Existing generation artifact does not match the requested run config."
            )
    else:
        run = EvaluationGenerationRun(
            run_id=f"eval-{uuid.uuid4()}",
            created_at=datetime.now(UTC).isoformat(),
            scenario_set_version=scenarios.scenario_set_version,
            scenario_set_hash=scenario_hash,
            model=generator.model,
            max_output_tokens=generator.max_output_tokens,
            prompt_version=prompt_version,
            strategy_version=strategy_version,
            generation_seed=seed,
            generation_order=generation_order,
            outputs=[],
        )
        write_json_atomic(output_path, run.model_dump(mode="json"))

    completed = {(item.scenario_id, item.variant_id) for item in run.outputs}
    scenario_by_id = {item.scenario_id: item for item in scenarios.scenarios}
    for item in generation_order:
        scenario_id, variant_id = item.rsplit(":", 1)
        key = (scenario_id, variant_id)
        if key in completed:
            continue
        generated = generator.generate_variant(scenario_by_id[scenario_id], variant_id)  # type: ignore[arg-type]
        run.outputs.append(generated)
        completed.add(key)
        write_json_atomic(output_path, run.model_dump(mode="json"))
    return run


__all__ = [
    "EVALUATION_MAX_OUTPUT_TOKENS",
    "EVALUATION_MODEL",
    "EVALUATION_PROMPT_VERSION",
    "OpenAIEvaluationGenerator",
    "run_generation",
]
