from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean
from typing import Any

from proofgraph.evaluation.schemas import (
    DIMENSIONS,
    REQUIRED_DIMENSIONS,
    VARIANTS,
    AdjudicationArtifact,
    BlindPacket,
    DimensionId,
    EvaluationGenerationRun,
    PrivateBlindMap,
    RatingArtifact,
)

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 27_031


def _percentile(sorted_values: list[float], proportion: float) -> float:
    position = (len(sorted_values) - 1) * proportion
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def bootstrap_interval(
    differences: list[float],
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[float, float]:
    if not differences:
        raise ValueError("At least one paired difference is required.")
    if resamples < 1:
        raise ValueError("At least one bootstrap resample is required.")
    rng = random.Random(seed)
    sample_size = len(differences)
    estimates = sorted(
        mean(differences[rng.randrange(sample_size)] for _ in range(sample_size))
        for _ in range(resamples)
    )
    return _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def _validate_inputs(
    packet: BlindPacket,
    private_map: PrivateBlindMap,
    raters: tuple[RatingArtifact, RatingArtifact],
    adjudications: AdjudicationArtifact,
) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, DimensionId], int]]:
    if private_map.packet_id != packet.packet_id:
        raise ValueError("Private map packet ID does not match the blind packet.")
    packet_outputs = {
        output.blind_output_id: scenario.scenario.scenario_id
        for scenario in packet.scenarios
        for output in scenario.outputs
    }
    if len(packet_outputs) != sum(len(scenario.outputs) for scenario in packet.scenarios):
        raise ValueError("Blind output IDs must be unique.")
    mapping: dict[str, tuple[str, str]] = {}
    for item in private_map.mappings:
        if item.blind_output_id in mapping:
            raise ValueError("Private map contains duplicate blind output IDs.")
        mapping[item.blind_output_id] = (item.scenario_id, item.variant_id)
    if set(mapping) != set(packet_outputs):
        raise ValueError("Private map must cover exactly the blind packet outputs.")
    if any(mapping[item][0] != scenario_id for item, scenario_id in packet_outputs.items()):
        raise ValueError("Private map scenario IDs do not match the blind packet.")
    scenario_variants: dict[str, set[str]] = defaultdict(set)
    for scenario_id, variant_id in mapping.values():
        scenario_variants[scenario_id].add(variant_id)
    if any(variants != set(VARIANTS) for variants in scenario_variants.values()):
        raise ValueError("Every scenario must map to all four variants exactly once.")

    if any(rater.packet_id != packet.packet_id for rater in raters):
        raise ValueError("Rater packet IDs do not match the blind packet.")
    rater_ids = [rater.rater_id.strip() for rater in raters]
    if not all(rater_ids) or len(set(rater_ids)) != 2:
        raise ValueError("Two distinct, non-empty rater IDs are required.")
    expected_dimensions = set(DIMENSIONS)
    indexed_ratings: list[dict[str, dict[DimensionId, int]]] = []
    for rater in raters:
        index: dict[str, dict[DimensionId, int]] = {}
        for rating in rater.ratings:
            if rating.blind_output_id in index:
                raise ValueError("A rating artifact contains duplicate output IDs.")
            if set(rating.scores) != expected_dimensions:
                raise ValueError("Every rating must contain exactly the seven rubric dimensions.")
            scores: dict[DimensionId, int] = {}
            for dimension, score in rating.scores.items():
                if score is None or isinstance(score, bool) or not 1 <= score <= 5:
                    raise ValueError("Every rating score must be an integer from 1 through 5.")
                scores[dimension] = score
            index[rating.blind_output_id] = scores
        if set(index) != set(packet_outputs):
            raise ValueError("Each rater must score every and only blind packet output.")
        indexed_ratings.append(index)

    disputes = {
        (output_id, dimension)
        for output_id in packet_outputs
        for dimension in DIMENSIONS
        if abs(indexed_ratings[0][output_id][dimension] - indexed_ratings[1][output_id][dimension])
        >= 2
    }
    if adjudications.packet_id != packet.packet_id:
        raise ValueError("Adjudication packet ID does not match the blind packet.")
    adjudication_scores: dict[tuple[str, DimensionId], int] = {}
    for item in adjudications.adjudications:
        key = (item.blind_output_id, item.dimension)
        if key in adjudication_scores:
            raise ValueError("Adjudication artifact contains duplicate decisions.")
        adjudication_scores[key] = item.resolved_score
    if set(adjudication_scores) != disputes:
        missing = sorted(disputes - set(adjudication_scores))
        extras = sorted(set(adjudication_scores) - disputes)
        message = (
            "Adjudications must exactly cover >=2 disagreements; "
            f"missing={missing}, extras={extras}."
        )
        raise ValueError(message)
    return mapping, adjudication_scores


def analyze_ratings(
    packet: BlindPacket,
    private_map: PrivateBlindMap,
    rater_a: RatingArtifact,
    rater_b: RatingArtifact,
    adjudications: AdjudicationArtifact,
    generation_run: EvaluationGenerationRun,
) -> dict[str, Any]:
    if private_map.generation_run_id != generation_run.run_id:
        raise ValueError("Private map generation run ID does not match the generation artifact.")
    mapping, adjudication_scores = _validate_inputs(
        packet,
        private_map,
        (rater_a, rater_b),
        adjudications,
    )
    ratings = [
        {entry.blind_output_id: entry for entry in artifact.ratings}
        for artifact in (rater_a, rater_b)
    ]
    effective: dict[tuple[str, str, DimensionId], float] = {}
    for output_id, (scenario_id, variant_id) in mapping.items():
        for dimension in DIMENSIONS:
            key = (output_id, dimension)
            score = (
                float(adjudication_scores[key])
                if key in adjudication_scores
                else mean(
                    [
                        ratings[0][output_id].scores[dimension],
                        ratings[1][output_id].scores[dimension],
                    ]
                )
            )
            effective[(scenario_id, variant_id, dimension)] = score

    scenario_ids = [scenario.scenario.scenario_id for scenario in packet.scenarios]
    dimensions: dict[str, Any] = {}
    all_required_pass = True
    for index, dimension in enumerate(DIMENSIONS):
        differences = [
            effective[(scenario_id, "full_pipeline", dimension)]
            - effective[(scenario_id, "generic", dimension)]
            for scenario_id in scenario_ids
        ]
        lower, upper = bootstrap_interval(differences, seed=BOOTSTRAP_SEED + index)
        improvement = mean(differences)
        required = dimension in REQUIRED_DIMENSIONS
        passed = improvement >= 0.5 and lower > 0 if required else None
        if required and not passed:
            all_required_pass = False
        dimensions[dimension] = {
            "required": required,
            "mean_full_minus_generic": improvement,
            "bootstrap_seed": BOOTSTRAP_SEED + index,
            "bootstrap_95_ci": [lower, upper],
            "passes_required_threshold": passed,
            "scenario_differences": dict(zip(scenario_ids, differences, strict=True)),
        }

    effective_scores = {
        scenario_id: {
            variant: {
                dimension: effective[(scenario_id, variant, dimension)] for dimension in DIMENSIONS
            }
            for variant in VARIANTS
        }
        for scenario_id in scenario_ids
    }
    return {
        "schema_version": 1,
        "packet_id": packet.packet_id,
        "generation": {
            "run_id": generation_run.run_id,
            "model": generation_run.model,
            "reasoning_effort": generation_run.reasoning_effort,
            "max_output_tokens": generation_run.max_output_tokens,
            "api_storage": generation_run.api_storage,
            "generation_seed": generation_run.generation_seed,
            "prompt_version": generation_run.prompt_version,
            "strategy_version": generation_run.strategy_version,
            "scenario_set_version": generation_run.scenario_set_version,
            "scenario_set_hash": generation_run.scenario_set_hash,
        },
        "rubric_version": packet.rubric_version,
        "rater_ids": [rater_a.rater_id, rater_b.rater_id],
        "bootstrap": {
            "method": "paired scenario percentile",
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed_base": BOOTSTRAP_SEED,
        },
        "acceptance_passed": all_required_pass,
        "dimensions": dimensions,
        "effective_scores": effective_scores,
        "original_ratings": [
            rater_a.model_dump(mode="json"),
            rater_b.model_dump(mode="json"),
        ],
        "adjudications": adjudications.model_dump(mode="json"),
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    status = "PASS" if report["acceptance_passed"] else "FAIL"
    lines = [
        "# Proofgraph comparative evaluation",
        "",
        f"Overall required-dimension result: **{status}**",
        "",
        "| Dimension | Required | Mean full - generic | 95% bootstrap CI | Pass |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for dimension in DIMENSIONS:
        item = report["dimensions"][dimension]
        lower, upper = item["bootstrap_95_ci"]
        passed = item["passes_required_threshold"]
        lines.append(
            f"| {dimension} | {'yes' if item['required'] else 'no'} | "
            f"{item['mean_full_minus_generic']:.3f} | [{lower:.3f}, {upper:.3f}] | "
            f"{'yes' if passed else 'no' if passed is False else 'n/a'} |"
        )
    generation = report["generation"]
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            f"- Generation run: `{generation['run_id']}`",
            f"- Model: `{generation['model']}`",
            f"- Prompt version: `{generation['prompt_version']}`",
            f"- Strategy version: `{generation['strategy_version']}`",
            f"- Scenario set: `{generation['scenario_set_version']}`",
            f"- Raters: `{', '.join(report['rater_ids'])}`",
            f"- Adjudications: `{len(report['adjudications']['adjudications'])}`",
            "",
            "The JSON report is authoritative and retains scenario differences, original scores, "
            "and adjudications.",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = ["analyze_ratings", "bootstrap_interval", "render_markdown_report"]
