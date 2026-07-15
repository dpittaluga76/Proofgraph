from __future__ import annotations

import hashlib
import random

from proofgraph.evaluation.scenarios import scenario_set_hash
from proofgraph.evaluation.schemas import (
    DIMENSIONS,
    VARIANTS,
    BlindOutput,
    BlindPacket,
    BlindScenario,
    EvaluationGenerationRun,
    PrivateBlindMap,
    RatingArtifact,
    RatingEntry,
    RubricDimension,
    ScenarioSet,
    VariantMapping,
)

RUBRIC_VERSION = "opportunity_rubric_v1"

RUBRIC = (
    RubricDimension(
        dimension="specificity",
        one="Generic language with no clear user, workflow, or wedge.",
        three="Names a user and workflow but leaves important execution details vague.",
        five="Defines a precise user, painful workflow, product wedge, and concrete boundaries.",
    ),
    RubricDimension(
        dimension="evidence_relevance",
        one="Ignores, contradicts, or misuses the supplied evidence.",
        three="Uses some relevant evidence but weakly connects it to the opportunity.",
        five="Correctly uses material evidence and limitations to shape the opportunity.",
    ),
    RubricDimension(
        dimension="novelty",
        one="A commonplace idea without a differentiated mechanism or buyer insight.",
        three="Some differentiation, though close to familiar category conventions.",
        five="A distinctive, credible wedge derived from the scenario rather than novelty alone.",
    ),
    RubricDimension(
        dimension="feasibility",
        one="Conflicts with constraints or depends on unavailable capabilities.",
        three="Plausible with meaningful unresolved execution risk.",
        five="Achievable by this builder with a bounded first product and credible dependencies.",
    ),
    RubricDimension(
        dimension="economic_leverage",
        one="No clear buyer, budget, value driver, or repeatable economics.",
        three="A plausible buyer and value case with uncertain pricing or distribution.",
        five="Links costly recurring pain to a reachable buyer and scalable delivery model.",
    ),
    RubricDimension(
        dimension="testability",
        one="No falsifiable assumptions or concrete near-term test.",
        three="A test is proposed but its signal, audience, or threshold is incomplete.",
        five=(
            "States assumptions and a cheap, time-bounded test with an observable decision signal."
        ),
    ),
    RubricDimension(
        dimension="builder_fit",
        one="Poor match for the builder's skills, access, preferences, or constraints.",
        three="Uses some builder advantages but requires notable capability or access gaps.",
        five="Directly compounds the builder's skills and access while respecting constraints.",
    ),
)


def prepare_blind_packet(
    scenarios: ScenarioSet,
    run: EvaluationGenerationRun,
    *,
    seed: int,
) -> tuple[BlindPacket, PrivateBlindMap, RatingArtifact, RatingArtifact]:
    if (
        run.scenario_set_version != scenarios.scenario_set_version
        or run.scenario_set_hash != scenario_set_hash(scenarios)
    ):
        raise ValueError("Generation artifact does not match the supplied scenario set.")
    expected = {
        (scenario.scenario_id, variant) for scenario in scenarios.scenarios for variant in VARIANTS
    }
    by_key = {(item.scenario_id, item.variant_id): item for item in run.outputs}
    if set(by_key) != expected or len(run.outputs) != len(expected):
        raise ValueError(
            "Generation artifact must contain exactly one output per scenario/variant."
        )

    rng = random.Random(seed)
    packet_digest = hashlib.sha256(
        f"{run.run_id}:{seed}:{run.scenario_set_hash}".encode()
    ).hexdigest()[:20]
    packet_id = f"packet-{packet_digest}"
    blind_scenarios: list[BlindScenario] = []
    mappings: list[VariantMapping] = []
    for scenario in scenarios.scenarios:
        variants = list(VARIANTS)
        rng.shuffle(variants)
        outputs: list[BlindOutput] = []
        for variant in variants:
            opaque_id = f"out-{rng.getrandbits(96):024x}"
            outputs.append(
                BlindOutput(
                    blind_output_id=opaque_id,
                    opportunity_set=by_key[(scenario.scenario_id, variant)].opportunity_set,
                )
            )
            mappings.append(
                VariantMapping(
                    blind_output_id=opaque_id,
                    scenario_id=scenario.scenario_id,
                    variant_id=variant,
                )
            )
        blind_scenarios.append(BlindScenario(scenario=scenario, outputs=outputs))

    packet = BlindPacket(
        packet_id=packet_id,
        scenario_set_version=scenarios.scenario_set_version,
        rubric_version=RUBRIC_VERSION,
        rubric=list(RUBRIC),
        scenarios=blind_scenarios,
    )
    private_map = PrivateBlindMap(
        packet_id=packet_id,
        randomization_seed=seed,
        generation_run_id=run.run_id,
        mappings=mappings,
    )
    rating_entries = [
        RatingEntry(
            blind_output_id=output.blind_output_id,
            scores={dimension: None for dimension in DIMENSIONS},
        )
        for scenario in packet.scenarios
        for output in scenario.outputs
    ]
    rater_a = RatingArtifact(packet_id=packet_id, rater_id="", ratings=rating_entries)
    rater_b = rater_a.model_copy(deep=True)
    return packet, private_map, rater_a, rater_b


__all__ = ["RUBRIC", "RUBRIC_VERSION", "prepare_blind_packet"]
