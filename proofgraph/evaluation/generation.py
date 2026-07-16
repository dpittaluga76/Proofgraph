from __future__ import annotations

import json
import random
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from proofgraph.evaluation.artifacts import write_json_atomic
from proofgraph.evaluation.scenarios import scenario_set_hash
from proofgraph.evaluation.schemas import (
    EVALUATION_MODELS,
    VARIANTS,
    CritiquedOpportunitySet,
    EvaluationGenerationRun,
    EvaluationModelId,
    EvidenceAnalysis,
    GeneratedVariant,
    OpportunitySet,
    PartialGeneratedVariant,
    ScenarioSet,
    StageRecord,
    StrategyPlan,
    TokenUsageRecord,
    VariantId,
)
from proofgraph.generation.strategies import STRATEGY_CATALOG_VERSION, STRATEGY_TEMPLATES

EVALUATION_MODEL: EvaluationModelId = "gpt-5.6-terra"
EVALUATION_PROMPT_VERSION = "comparative_evaluation_v1"
EVALUATION_MAX_OUTPUT_TOKENS = 4_500
EVALUATION_DEFAULT_WORKERS = 6
EVALUATION_MAX_WORKERS = 8

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


class EvaluationArtifactError(ValueError):
    """An existing private artifact cannot safely resume the requested run."""


class EvaluationProviderError(RuntimeError):
    """A provider call failed after earlier checkpoints remained durable."""

    def __init__(
        self,
        *,
        model: str,
        stage: str,
        status: int | None,
        code: str,
    ) -> None:
        if status == 403 and code == "model_not_found":
            recovery = (
                "Rerun the identical command once; completed outputs and stages will be skipped. "
                "If it repeats, verify that the API key belongs to a project with access to this "
                "model, or start a different allowed model in a new --output path."
            )
        elif status == 429:
            recovery = (
                "Retry the same run configuration later; completed outputs and stages will be "
                "skipped. If rate limiting repeats, reduce --workers."
            )
        elif status == 400 and code == "invalid_json_schema":
            recovery = (
                "The structured-output schema was rejected before inference. Do not retry the "
                "unchanged implementation; validate every object against the supported strict "
                "JSON Schema subset first. Existing checkpoints remain resumable after a "
                "schema-compatible code fix."
            )
        elif status is not None and status >= 500:
            recovery = (
                "Retry the identical command later; completed outputs and stages will be skipped."
            )
        else:
            recovery = (
                "Inspect API project access and retry the identical command; completed outputs "
                "and stages will be skipped."
            )
        status_label = str(status) if status is not None else "unknown"
        super().__init__(
            f"OpenAI evaluation call failed for model {model!r} during {stage!r} "
            f"(HTTP {status_label}, code {code!r}). {recovery}"
        )


def _load_generation_artifact(path: Path) -> EvaluationGenerationRun:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationArtifactError(
            f"Existing generation artifact is unreadable: {path}. "
            "Preserve it and choose a new --output path."
        ) from error
    if not isinstance(raw, dict):
        raise EvaluationArtifactError(
            f"Existing generation artifact is not a JSON object: {path}. "
            "Preserve it and choose a new --output path."
        )
    artifact_model = raw.get("model")
    if artifact_model not in EVALUATION_MODELS:
        outputs = raw.get("outputs")
        output_count = len(outputs) if isinstance(outputs, list) else "unknown"
        if output_count == 0:
            recovery = (
                "It contains zero generated outputs, so no paid work was lost. "
                "Choose a new --output path for the selected model."
            )
        else:
            recovery = (
                f"It contains {output_count} generated outputs that cannot be mixed with a new "
                "model. Preserve it and choose a new --output path."
            )
        raise EvaluationArtifactError(
            f"Existing generation artifact uses unsupported model {artifact_model!r}: {path}. "
            f"{recovery}"
        )
    try:
        return EvaluationGenerationRun.model_validate(raw)
    except ValidationError as error:
        raise EvaluationArtifactError(
            f"Existing generation artifact does not match the current schema: {path}. "
            "Preserve it and choose a new --output path."
        ) from error


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


def _provider_error_code(error: Exception) -> tuple[int | None, str]:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    payload = body.get("error", body) if isinstance(body, dict) else {}
    code = payload.get("code") if isinstance(payload, dict) else None
    return status if isinstance(status, int) else None, str(code or type(error).__name__)


class OpenAIEvaluationGenerator:
    """The cost-bearing, structured-output side of the evaluation harness."""

    def __init__(
        self,
        client: Any,
        *,
        model: EvaluationModelId = EVALUATION_MODEL,
        max_output_tokens: int = EVALUATION_MAX_OUTPUT_TOKENS,
    ) -> None:
        if model not in EVALUATION_MODELS:
            raise ValueError(f"Unsupported evaluation model: {model}")
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens

    def _parse(
        self,
        stage: str,
        payload: dict[str, object],
        response_model: type[_StructuredModel],
    ) -> tuple[_StructuredModel, StageRecord]:
        try:
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
        except Exception as error:
            status, code = _provider_error_code(error)
            raise EvaluationProviderError(
                model=self.model,
                stage=stage,
                status=status,
                code=code,
            ) from error
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

    def generate_variant(
        self,
        scenario: Any,
        variant_id: VariantId,
        *,
        partial: PartialGeneratedVariant | None = None,
        checkpoint: Callable[[PartialGeneratedVariant], None] | None = None,
    ) -> GeneratedVariant:
        core = scenario.core_payload()
        evidence = [item.model_dump(mode="json") for item in scenario.evidence]
        strategy_catalog = [item.model_dump(mode="json") for item in STRATEGY_TEMPLATES]
        allowed_evidence_ids = {item.evidence_id for item in scenario.evidence}
        partial = partial or PartialGeneratedVariant(
            scenario_id=scenario.scenario_id,
            variant_id=variant_id,
        )
        if partial.scenario_id != scenario.scenario_id or partial.variant_id != variant_id:
            raise ValueError("Partial generation checkpoint does not match requested work.")

        def save_checkpoint() -> None:
            if checkpoint is not None:
                checkpoint(partial.model_copy(deep=True))

        if variant_id == "generic":
            if partial.final_opportunity_set is None:
                opportunities, record = self._parse(
                    "generic",
                    {"scenario": core, "requested_opportunity_count": 3},
                    OpportunitySet,
                )
                assert isinstance(opportunities, OpportunitySet)
                self._validate_evidence_ids(opportunities, allowed_ids=set())
                partial.final_opportunity_set = opportunities
                partial.stages.append(record)
                save_checkpoint()
            assert partial.final_opportunity_set is not None
            return GeneratedVariant(
                scenario_id=scenario.scenario_id,
                variant_id=variant_id,
                opportunity_set=partial.final_opportunity_set,
                stages=partial.stages,
            )

        if partial.strategy_plan is None:
            plan, record = self._parse(
                "planning",
                {"scenario": core, "strategy_catalog": strategy_catalog},
                StrategyPlan,
            )
            assert isinstance(plan, StrategyPlan)
            partial.strategy_plan = plan
            partial.stages.append(record)
            save_checkpoint()
        plan = partial.strategy_plan
        if variant_id in {"strategy_plus_evidence", "full_pipeline"}:
            if partial.evidence_analysis is None:
                evidence_analysis, record = self._parse(
                    "evidence",
                    {
                        "scenario": core,
                        "strategy_plan": plan.model_dump(mode="json"),
                        "evidence": evidence,
                    },
                    EvidenceAnalysis,
                )
                assert isinstance(evidence_analysis, EvidenceAnalysis)
                finding_ids = [finding.evidence_id for finding in evidence_analysis.findings]
                if set(finding_ids) != allowed_evidence_ids or len(finding_ids) != len(
                    allowed_evidence_ids
                ):
                    raise ValueError(
                        "Evidence analysis must cover every and only supplied evidence ID."
                    )
                partial.evidence_analysis = evidence_analysis
                partial.stages.append(record)
                save_checkpoint()
            evidence_analysis = partial.evidence_analysis
            finding_ids = [finding.evidence_id for finding in evidence_analysis.findings]
            if set(finding_ids) != allowed_evidence_ids or len(finding_ids) != len(
                allowed_evidence_ids
            ):
                raise ValueError(
                    "Evidence analysis must cover every and only supplied evidence ID."
                )
        else:
            evidence_analysis = None

        opportunity_payload: dict[str, object] = {
            "scenario": core,
            "strategy_plan": plan.model_dump(mode="json"),
            "requested_opportunity_count": 3,
        }
        if evidence_analysis is not None:
            opportunity_payload["evidence"] = evidence
            opportunity_payload["evidence_analysis"] = evidence_analysis.model_dump(mode="json")
        allowed = allowed_evidence_ids if evidence_analysis is not None else set()
        if variant_id == "full_pipeline":
            if partial.draft_opportunity_set is None:
                opportunities, record = self._parse(
                    "opportunities",
                    opportunity_payload,
                    OpportunitySet,
                )
                assert isinstance(opportunities, OpportunitySet)
                self._validate_evidence_ids(opportunities, allowed_ids=allowed)
                partial.draft_opportunity_set = opportunities
                partial.stages.append(record)
                save_checkpoint()
            if partial.final_opportunity_set is None:
                critiqued, record = self._parse(
                    "critique",
                    {
                        **opportunity_payload,
                        "draft_opportunities": partial.draft_opportunity_set.model_dump(
                            mode="json"
                        ),
                    },
                    CritiquedOpportunitySet,
                )
                assert isinstance(critiqued, CritiquedOpportunitySet)
                opportunities = OpportunitySet(opportunities=critiqued.revised_opportunities)
                self._validate_evidence_ids(opportunities, allowed_ids=allowed_evidence_ids)
                partial.final_opportunity_set = opportunities
                partial.stages.append(record)
                save_checkpoint()
        elif partial.final_opportunity_set is None:
            opportunities, record = self._parse(
                "opportunities",
                opportunity_payload,
                OpportunitySet,
            )
            assert isinstance(opportunities, OpportunitySet)
            self._validate_evidence_ids(opportunities, allowed_ids=allowed)
            partial.final_opportunity_set = opportunities
            partial.stages.append(record)
            save_checkpoint()

        assert partial.final_opportunity_set is not None
        return GeneratedVariant(
            scenario_id=scenario.scenario_id,
            variant_id=variant_id,
            opportunity_set=partial.final_opportunity_set,
            stages=partial.stages,
        )


def run_generation(
    scenarios: ScenarioSet,
    generator: OpenAIEvaluationGenerator,
    output_path: Path,
    *,
    seed: int,
    workers: int = EVALUATION_DEFAULT_WORKERS,
    prompt_version: str = EVALUATION_PROMPT_VERSION,
    strategy_version: str = STRATEGY_CATALOG_VERSION,
) -> EvaluationGenerationRun:
    if isinstance(workers, bool) or not 1 <= workers <= EVALUATION_MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {EVALUATION_MAX_WORKERS}, inclusive.")
    scenario_hash = scenario_set_hash(scenarios)
    generation_order = [
        f"{scenario.scenario_id}:{variant}"
        for scenario in scenarios.scenarios
        for variant in VARIANTS
    ]
    random.Random(seed).shuffle(generation_order)

    if output_path.exists():
        run = _load_generation_artifact(output_path)
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
            partials=[],
        )
        write_json_atomic(output_path, run.model_dump(mode="json"))

    completed = {(item.scenario_id, item.variant_id) for item in run.outputs}
    partial_by_key = {(item.scenario_id, item.variant_id): item for item in run.partials}
    scenario_by_id = {item.scenario_id: item for item in scenarios.scenarios}
    order_by_key = {
        tuple(item.rsplit(":", 1)): position for position, item in enumerate(generation_order)
    }
    pending_keys = [
        tuple(item.rsplit(":", 1))
        for item in generation_order
        if tuple(item.rsplit(":", 1)) not in completed
    ]
    if not pending_keys:
        return run

    state_lock = Lock()

    def persist_locked() -> None:
        run.outputs.sort(key=lambda item: order_by_key[(item.scenario_id, item.variant_id)])
        run.partials.sort(key=lambda item: order_by_key[(item.scenario_id, item.variant_id)])
        write_json_atomic(output_path, run.model_dump(mode="json"))

    def generate_one(key: tuple[str, str]) -> GeneratedVariant:
        scenario_id, variant_id = key
        with state_lock:
            saved_partial = partial_by_key.get(key)
            partial = saved_partial.model_copy(deep=True) if saved_partial is not None else None

        def checkpoint(updated: PartialGeneratedVariant) -> None:
            durable = updated.model_copy(deep=True)
            with state_lock:
                run.partials = [
                    item for item in run.partials if (item.scenario_id, item.variant_id) != key
                ]
                run.partials.append(durable)
                partial_by_key[key] = durable
                persist_locked()

        return generator.generate_variant(  # type: ignore[arg-type]
            scenario_by_id[scenario_id],
            variant_id,
            partial=partial,
            checkpoint=checkpoint,
        )

    def commit_generated(key: tuple[str, str], generated: GeneratedVariant) -> None:
        with state_lock:
            run.partials = [
                item for item in run.partials if (item.scenario_id, item.variant_id) != key
            ]
            partial_by_key.pop(key, None)
            run.outputs.append(generated)
            completed.add(key)
            persist_locked()

    pending = iter(pending_keys)
    executor = ThreadPoolExecutor(
        max_workers=min(workers, len(pending_keys)),
        thread_name_prefix="evaluation-generation",
    )
    in_flight: dict[Future[GeneratedVariant], tuple[str, str]] = {}

    def submit_next() -> bool:
        try:
            key = next(pending)
        except StopIteration:
            return False
        in_flight[executor.submit(generate_one, key)] = key
        return True

    for _ in range(min(workers, len(pending_keys))):
        submit_next()

    try:
        while in_flight:
            done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
            first_error: BaseException | None = None
            for future in done:
                key = in_flight.pop(future)
                try:
                    generated = future.result()
                except BaseException as error:
                    first_error = first_error or error
                else:
                    commit_generated(key, generated)

            if first_error is not None:
                for future in in_flight:
                    future.cancel()
                while in_flight:
                    remaining_done, _ = wait(
                        tuple(in_flight),
                        return_when=FIRST_COMPLETED,
                    )
                    for future in remaining_done:
                        key = in_flight.pop(future)
                        if future.cancelled():
                            continue
                        try:
                            generated = future.result()
                        except BaseException:
                            continue
                        commit_generated(key, generated)
                raise first_error

            for _ in done:
                submit_next()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return run


__all__ = [
    "EVALUATION_DEFAULT_WORKERS",
    "EVALUATION_MAX_OUTPUT_TOKENS",
    "EVALUATION_MAX_WORKERS",
    "EVALUATION_MODEL",
    "EVALUATION_MODELS",
    "EVALUATION_PROMPT_VERSION",
    "EvaluationArtifactError",
    "EvaluationProviderError",
    "OpenAIEvaluationGenerator",
    "run_generation",
]
