from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean
from typing import Any, Literal

from proofgraph.evaluation.blinding import blind_packet_hash
from proofgraph.evaluation.schemas import (
    DIMENSIONS,
    REQUIRED_DIMENSIONS,
    VARIANTS,
    BlindPacket,
    DimensionId,
    EvaluationGenerationRun,
    JudgeRatingEntry,
    ModelJudgeRatingArtifact,
    PrivateBlindMap,
)

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 27_031
DISAGREEMENT_THRESHOLD = 2
AcceptanceRuleVersion = Literal["v1", "v2"]
ACCEPTANCE_RULE_VERSIONS: tuple[AcceptanceRuleVersion, ...] = ("v1", "v2")
ACCEPTANCE_RULE_IDS: dict[AcceptanceRuleVersion, str] = {
    "v1": "comparative_acceptance_v1",
    "v2": "comparative_acceptance_v2",
}
V2_BUILDER_FIT_MINIMUM = 4.5

_EXPECTED_PERSONAS = {
    "vera_crosscheck": "vera_crosscheck_v1",
    "marco_launch": "marco_launch_v1",
}


def _score(entry: JudgeRatingEntry, dimension: DimensionId) -> int:
    return getattr(entry.scores, dimension)


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


def _validate_mapping(
    packet: BlindPacket,
    private_map: PrivateBlindMap,
) -> tuple[dict[str, tuple[str, str]], set[str]]:
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
    return mapping, set(packet_outputs)


def _validate_judges(
    packet: BlindPacket,
    packet_output_ids: set[str],
    judges: tuple[ModelJudgeRatingArtifact, ModelJudgeRatingArtifact],
) -> list[dict[str, JudgeRatingEntry]]:
    if any(judge.packet_id != packet.packet_id for judge in judges):
        raise ValueError("Judge packet IDs do not match the blind packet.")
    judge_ids = [judge.provenance.judge_id for judge in judges]
    if set(judge_ids) != set(_EXPECTED_PERSONAS) or len(set(judge_ids)) != 2:
        raise ValueError("Ratings must come from Vera Crosscheck and Marco Launch exactly once.")
    if any(
        judge.provenance.persona_version != _EXPECTED_PERSONAS[judge.provenance.judge_id]
        for judge in judges
    ):
        raise ValueError("Judge rating artifacts do not use the frozen persona versions.")

    run_ids = {judge.provenance.judge_run_id for judge in judges}
    packet_hashes = {judge.provenance.packet_hash for judge in judges}
    prompt_versions = {judge.provenance.prompt_version for judge in judges}
    seeds = {judge.provenance.judge_seed for judge in judges}
    if len(run_ids) != 1 or len(prompt_versions) != 1 or len(seeds) != 1:
        raise ValueError("Both rating artifacts must come from the same frozen judge run.")
    expected_hash = blind_packet_hash(packet)
    if packet_hashes != {expected_hash}:
        raise ValueError("Judge rating artifact packet hashes do not match the blind packet.")

    indexed: list[dict[str, JudgeRatingEntry]] = []
    for judge in judges:
        ratings = {entry.blind_output_id: entry for entry in judge.ratings}
        if len(ratings) != len(judge.ratings):
            raise ValueError("A judge rating artifact contains duplicate output IDs.")
        if set(ratings) != packet_output_ids:
            raise ValueError("Each judge must score every and only blind packet output.")
        indexed.append(ratings)
    return indexed


def _disagreement_metrics(
    packet_output_ids: set[str],
    ratings: list[dict[str, JudgeRatingEntry]],
) -> dict[str, Any]:
    per_dimension: dict[str, Any] = {}
    overall_count = 0
    for dimension in DIMENSIONS:
        count = sum(
            abs(_score(ratings[0][output_id], dimension) - _score(ratings[1][output_id], dimension))
            >= DISAGREEMENT_THRESHOLD
            for output_id in packet_output_ids
        )
        comparisons = len(packet_output_ids)
        overall_count += count
        per_dimension[dimension] = {
            "comparison_count": comparisons,
            "disagreement_count": count,
            "disagreement_rate": count / comparisons,
        }
    overall_comparisons = len(packet_output_ids) * len(DIMENSIONS)
    return {
        "absolute_point_threshold": DISAGREEMENT_THRESHOLD,
        "overall": {
            "comparison_count": overall_comparisons,
            "disagreement_count": overall_count,
            "disagreement_rate": overall_count / overall_comparisons,
        },
        "per_dimension": per_dimension,
    }


def analyze_ratings(
    packet: BlindPacket,
    private_map: PrivateBlindMap,
    judge_a: ModelJudgeRatingArtifact,
    judge_b: ModelJudgeRatingArtifact,
    generation_run: EvaluationGenerationRun,
    *,
    acceptance_rule: AcceptanceRuleVersion = "v1",
) -> dict[str, Any]:
    if acceptance_rule not in ACCEPTANCE_RULE_VERSIONS:
        raise ValueError(f"acceptance_rule must be one of: {', '.join(ACCEPTANCE_RULE_VERSIONS)}.")
    if private_map.generation_run_id != generation_run.run_id:
        raise ValueError("Private map generation run ID does not match the generation artifact.")
    mapping, packet_output_ids = _validate_mapping(packet, private_map)
    judge_artifacts = (judge_a, judge_b)
    ratings = _validate_judges(packet, packet_output_ids, judge_artifacts)

    effective: dict[tuple[str, str, DimensionId], float] = {}
    for output_id, (scenario_id, variant_id) in mapping.items():
        for dimension in DIMENSIONS:
            effective[(scenario_id, variant_id, dimension)] = mean(
                [
                    _score(ratings[0][output_id], dimension),
                    _score(ratings[1][output_id], dimension),
                ]
            )

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
        full_pipeline_mean = mean(
            effective[(scenario_id, "full_pipeline", dimension)] for scenario_id in scenario_ids
        )
        required = dimension in REQUIRED_DIMENSIONS
        acceptance_criteria: dict[str, Any] | None = None
        if required and acceptance_rule == "v2" and dimension == "builder_fit":
            passed = full_pipeline_mean >= V2_BUILDER_FIT_MINIMUM and lower >= 0
            acceptance_criteria = {
                "kind": "absolute_floor_and_nonnegative_relative_ci",
                "minimum_full_pipeline_mean": V2_BUILDER_FIT_MINIMUM,
                "minimum_bootstrap_95_ci_lower_bound": 0.0,
                "ci_lower_bound_inclusive": True,
            }
        elif required:
            passed = improvement >= 0.5 and lower > 0
            acceptance_criteria = {
                "kind": "minimum_relative_lift_and_positive_ci",
                "minimum_mean_full_minus_generic": 0.5,
                "minimum_bootstrap_95_ci_lower_bound": 0.0,
                "ci_lower_bound_inclusive": False,
            }
        else:
            passed = None
        if required and not passed:
            all_required_pass = False
        dimension_report = {
            "required": required,
            "mean_full_minus_generic": improvement,
            "bootstrap_seed": BOOTSTRAP_SEED + index,
            "bootstrap_95_ci": [lower, upper],
            "passes_required_threshold": passed,
            "scenario_differences": dict(zip(scenario_ids, differences, strict=True)),
        }
        if acceptance_rule == "v2":
            dimension_report["full_pipeline_mean"] = full_pipeline_mean
            dimension_report["acceptance_criteria"] = acceptance_criteria
        dimensions[dimension] = dimension_report

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
        "schema_version": 3 if acceptance_rule == "v2" else 2,
        **(
            {"acceptance_rule_version": ACCEPTANCE_RULE_IDS[acceptance_rule]}
            if acceptance_rule == "v2"
            else {}
        ),
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
        "judges": [artifact.provenance.model_dump(mode="json") for artifact in judge_artifacts],
        "scoring": {
            "effective_score_method": "arithmetic mean of both automated judge scores",
            "large_disagreement_policy": "average and report without adjudication",
        },
        "disagreements": _disagreement_metrics(packet_output_ids, ratings),
        "bootstrap": {
            "method": "paired scenario percentile",
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed_base": BOOTSTRAP_SEED,
        },
        "acceptance_passed": all_required_pass,
        "dimensions": dimensions,
        "effective_scores": effective_scores,
        "original_ratings": [
            judge_a.model_dump(mode="json"),
            judge_b.model_dump(mode="json"),
        ],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    status = "PASS" if report["acceptance_passed"] else "FAIL"
    is_v2 = report.get("acceptance_rule_version") == ACCEPTANCE_RULE_IDS["v2"]
    lines = [
        "# Proofgraph automated blinded model evaluation",
        "",
        f"Overall required-dimension result: **{status}**",
        "",
    ]
    if is_v2:
        lines.extend(
            [
                f"Acceptance rule: `{ACCEPTANCE_RULE_IDS['v2']}`.",
                "",
            ]
        )
    lines.extend(
        [
            "Every effective score is the arithmetic mean of the two automated judges. Large "
            "disagreements are reported and are not adjudicated.",
            "",
        ]
    )
    if is_v2:
        lines.extend(
            [
                "| Dimension | Required | Full mean | Mean full - generic | "
                "95% bootstrap CI | Pass |",
                "| --- | --- | ---: | ---: | --- | --- |",
            ]
        )
    else:
        lines.extend(
            [
                "| Dimension | Required | Mean full - generic | 95% bootstrap CI | Pass |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
    for dimension in DIMENSIONS:
        item = report["dimensions"][dimension]
        lower, upper = item["bootstrap_95_ci"]
        passed = item["passes_required_threshold"]
        if is_v2:
            lines.append(
                f"| {dimension} | {'yes' if item['required'] else 'no'} | "
                f"{item['full_pipeline_mean']:.3f} | "
                f"{item['mean_full_minus_generic']:.3f} | [{lower:.3f}, {upper:.3f}] | "
                f"{'yes' if passed else 'no' if passed is False else 'n/a'} |"
            )
        else:
            lines.append(
                f"| {dimension} | {'yes' if item['required'] else 'no'} | "
                f"{item['mean_full_minus_generic']:.3f} | [{lower:.3f}, {upper:.3f}] | "
                f"{'yes' if passed else 'no' if passed is False else 'n/a'} |"
            )

    if is_v2:
        lines.extend(
            [
                "",
                "## Required-dimension rules",
                "",
                "- Evidence relevance, specificity, and testability: mean lift at least "
                "`+0.500` and bootstrap lower bound greater than `0`.",
                "- Builder fit: full-pipeline mean at least `4.500` and paired bootstrap "
                "lower bound at least `0`.",
            ]
        )

    lines.extend(["", "## Automated judges", ""])
    for judge in report["judges"]:
        lines.append(
            f"- **{judge['display_name']}** — `{judge['model']}`, "
            f"persona `{judge['persona_version']}`"
        )
    disagreements = report["disagreements"]
    overall = disagreements["overall"]
    lines.extend(
        [
            "",
            "## Judge disagreement",
            "",
            f"Absolute differences of at least {disagreements['absolute_point_threshold']} "
            f"points: **{overall['disagreement_count']} / {overall['comparison_count']}** "
            f"({overall['disagreement_rate']:.1%}).",
            "",
            "| Dimension | Disagreements | Rate |",
            "| --- | ---: | ---: |",
        ]
    )
    for dimension in DIMENSIONS:
        item = disagreements["per_dimension"][dimension]
        lines.append(
            f"| {dimension} | {item['disagreement_count']} / "
            f"{item['comparison_count']} | {item['disagreement_rate']:.1%} |"
        )

    generation = report["generation"]
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            f"- Generation run: `{generation['run_id']}`",
            f"- Generation model: `{generation['model']}`",
            f"- Generation prompt version: `{generation['prompt_version']}`",
            f"- Strategy version: `{generation['strategy_version']}`",
            f"- Scenario set: `{generation['scenario_set_version']}`",
            f"- Judge run: `{report['judges'][0]['judge_run_id']}`",
            f"- Judge prompt version: `{report['judges'][0]['prompt_version']}`",
            "",
        ]
    )
    if is_v2:
        lines.append(
            "The JSON report retains scenario differences, both original scores and rationales, "
            "effective arithmetic means, and disagreement metrics. A V2 analysis is authoritative "
            "only when it uses fresh post-registration V2 generation and judge artifacts; "
            "reanalyzed V1 ratings remain diagnostic."
        )
    else:
        lines.append(
            "The JSON report is authoritative and retains scenario differences, both original "
            "scores and rationales, effective arithmetic means, and disagreement metrics."
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "ACCEPTANCE_RULE_IDS",
    "ACCEPTANCE_RULE_VERSIONS",
    "V2_BUILDER_FIT_MINIMUM",
    "AcceptanceRuleVersion",
    "analyze_ratings",
    "bootstrap_interval",
    "render_markdown_report",
]
