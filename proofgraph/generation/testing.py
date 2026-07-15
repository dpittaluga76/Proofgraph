from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from proofgraph.generation.composition import StrictStageOutputValidator
from proofgraph.generation.context import GraphRunContextFactory
from proofgraph.generation.ports import DurableComposition
from proofgraph.generation.schemas import (
    ProgressEventEnvelope,
    RunExecutionConfiguration,
    StageResultEnvelope,
)
from proofgraph.graph.exceptions import GraphAPIError


class Phase2TestProfileResolver:
    def resolve(self, profile_id: str, *, product_request: bool) -> RunExecutionConfiguration:
        if profile_id != "phase2_test_v1":
            raise GraphAPIError(
                status=422,
                code="execution_profile_unavailable",
                message="The requested test execution profile is unavailable.",
            )
        return RunExecutionConfiguration(
            profile_id="phase2_test_v1",
            provider_identity="deterministic_phase2_adapter",
            pipeline_version="phase2_pipeline_v1",
            prompt_version="synthetic_prompt_v1",
            strategy_version="synthetic_strategy_v1",
            fixture_bundle_id="phase2_synthetic_bundle",
            fixture_version="1",
        )


class DeterministicPhase2Executor:
    @staticmethod
    def _add_node_operation(
        *,
        context_hash: str,
        index: int,
        kind: str,
        title: str,
        configuration: RunExecutionConfiguration,
    ) -> dict[str, Any]:
        return {
            "op": "ADD_NODE",
            "client_generated_id": f"phase2-{context_hash[:10]}-{index}",
            "node": {
                "kind": kind,
                "title": title,
                "body": "Synthetic fixture output for durable-job verification.",
                "metadata": {
                    "generated_by_profile": configuration.profile_id,
                    "fixture_version": configuration.fixture_version,
                },
                "position": {"x": index * 40, "y": index * 30},
            },
        }

    def _patch_operations(
        self,
        *,
        context_hash: str,
        stage_input: dict[str, Any],
        configuration: RunExecutionConfiguration,
    ) -> list[dict[str, Any]]:
        manifest = stage_input["context_manifest"]
        operation = manifest["request"]["operation"]
        regeneration = manifest.get("regeneration") or {}
        targets = regeneration.get("targets") or []
        if operation == "regenerate_stale":
            return [
                self._add_node_operation(
                    context_hash=context_hash,
                    index=index,
                    kind=target["kind"],
                    title=f"Regenerated {target['kind'].replace('_', ' ')}",
                    configuration=configuration,
                )
                for index, target in enumerate(targets)
            ]
        if operation == "generate_strategies":
            return [
                self._add_node_operation(
                    context_hash=context_hash,
                    index=index,
                    kind="strategy",
                    title=f"Deterministic Phase 2 strategy {index + 1}",
                    configuration=configuration,
                )
                for index in range(3)
            ]
        if operation == "synthesize_opportunities":
            return [
                self._add_node_operation(
                    context_hash=context_hash,
                    index=index,
                    kind="opportunity",
                    title=f"Deterministic Phase 2 opportunity {index + 1}",
                    configuration=configuration,
                )
                for index in range(3)
            ]
        return [
            self._add_node_operation(
                context_hash=context_hash,
                index=0,
                kind="claim",
                title="Deterministic Phase 2 evidence candidate",
                configuration=configuration,
            )
        ]

    def execute(
        self,
        *,
        stage_name: str,
        stage_input: dict[str, Any],
        configuration: RunExecutionConfiguration,
        progress_callback: Callable[[ProgressEventEnvelope], None] | None = None,
    ) -> StageResultEnvelope:
        context_hash = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{stage_name}:{stage_input['context_snapshot']['canvas_id']}",
        ).hex
        output: dict[str, Any] = {"stage": stage_name, "deterministic_id": context_hash}
        events: tuple[ProgressEventEnvelope, ...] = ()
        if stage_name == "researching":
            events = (
                ProgressEventEnvelope(
                    event_type="research.query_created",
                    payload={"query_hash": context_hash},
                ),
                ProgressEventEnvelope(
                    event_type="research.source_found",
                    payload={
                        "provisional": True,
                        "canonical_url": "https://fixtures.invalid/source",
                        "content_hash": context_hash,
                        "sanitized_excerpt": "Synthetic redistributable fixture excerpt.",
                    },
                ),
            )
        elif stage_name == "extracting":
            events = (
                ProgressEventEnvelope(
                    event_type="evidence.extracted",
                    payload={
                        "provisional": True,
                        "claim_hash": context_hash,
                        "sanitized_excerpt": "Synthetic derived evidence.",
                    },
                ),
            )
        elif stage_name in {"planning", "synthesizing"}:
            events = (
                ProgressEventEnvelope(
                    event_type="candidate.generated",
                    payload={"candidate_hash": context_hash, "provisional": True},
                ),
            )
        elif stage_name == "critiquing":
            events = (
                ProgressEventEnvelope(
                    event_type="candidate.critiqued",
                    payload={"candidate_hash": context_hash, "provisional": True},
                ),
            )
        elif stage_name == "constructing_patch":
            output["base_canvas_revision"] = stage_input["base_canvas_revision"]
            output["operations"] = self._patch_operations(
                context_hash=context_hash,
                stage_input=stage_input,
                configuration=configuration,
            )
        if progress_callback is not None:
            for event in events:
                progress_callback(event)
            events = ()
        return StageResultEnvelope(
            stage_name=stage_name,
            output=output,
            provider_identity=configuration.provider_identity,
            progress_events=events,
        )


def phase2_test_composition() -> DurableComposition:
    return DurableComposition(
        context_factory=GraphRunContextFactory(),
        profile_resolver=Phase2TestProfileResolver(),
        output_validator=StrictStageOutputValidator(),
        executor=DeterministicPhase2Executor(),
    )
