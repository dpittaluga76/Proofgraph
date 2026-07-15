from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from proofgraph.generation.ports import (
    CritiqueProvider,
    DeterministicClusterer,
    ExecutionProfileResolver,
    ExtractionProvider,
    PatchConstructionProvider,
    PlanningProvider,
    ProviderStageRequest,
    ResearchProvider,
    StageExecutor,
    SynthesisProvider,
)
from proofgraph.generation.schemas import (
    ProgressEventEnvelope,
    RunExecutionConfiguration,
    StageResultEnvelope,
)
from proofgraph.generation.strategies import STRATEGY_CATALOG_VERSION
from proofgraph.graph.exceptions import GraphAPIError

PIPELINE_VERSION: Final = "intelligence_pipeline_v1"
PROMPT_VERSION: Final = "opportunity_pipeline_prompts_v1"
FIXTURE_BUNDLE_ID: Final = "security_questionnaires_v1"
FIXTURE_VERSION: Final = "1"


@dataclass(frozen=True)
class ProviderSuite:
    planning: PlanningProvider
    research: ResearchProvider
    extraction: ExtractionProvider
    synthesis: SynthesisProvider
    critique: CritiqueProvider
    patch_construction: PatchConstructionProvider


@dataclass(frozen=True)
class ExecutionProfile:
    configuration: RunExecutionConfiguration
    providers: ProviderSuite
    product_selectable: bool = True


class ExecutionProfileRegistry:
    def __init__(
        self,
        profiles: tuple[ExecutionProfile, ...],
        *,
        clusterer: DeterministicClusterer,
    ) -> None:
        self._profiles = {profile.configuration.profile_id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("execution profile IDs must be unique")
        self.clusterer = clusterer

    def resolve_profile(self, profile_id: str, *, product_request: bool) -> ExecutionProfile:
        profile = self._profiles.get(profile_id)
        if profile is None or (product_request and not profile.product_selectable):
            raise GraphAPIError(
                status=422,
                code="execution_profile_unavailable",
                message="The requested execution profile is unavailable.",
                details={"execution_profile_id": profile_id},
            )
        return profile

    def profile_for_execution(
        self,
        configuration: RunExecutionConfiguration,
    ) -> ExecutionProfile:
        profile = self.resolve_profile(configuration.profile_id, product_request=False)
        if profile.configuration != configuration:
            raise ValueError("the frozen execution configuration does not match its profile")
        return profile


class RegisteredExecutionProfileResolver(ExecutionProfileResolver):
    def __init__(self, registry: ExecutionProfileRegistry) -> None:
        self.registry = registry

    def resolve(self, profile_id: str, *, product_request: bool) -> RunExecutionConfiguration:
        return self.registry.resolve_profile(
            profile_id,
            product_request=product_request,
        ).configuration


class ProfileStageExecutor(StageExecutor):
    def __init__(self, registry: ExecutionProfileRegistry) -> None:
        self.registry = registry

    def execute(
        self,
        *,
        stage_name: str,
        stage_input: dict[str, Any],
        configuration: RunExecutionConfiguration,
        progress_callback: Callable[[ProgressEventEnvelope], None] | None = None,
    ) -> StageResultEnvelope:
        profile = self.registry.profile_for_execution(configuration)
        request = ProviderStageRequest(
            stage_input=stage_input,
            configuration=configuration,
            progress_callback=progress_callback,
        )
        if stage_name == "planning":
            return profile.providers.planning.plan(request)
        if stage_name == "researching":
            return profile.providers.research.research(request)
        if stage_name == "extracting":
            return profile.providers.extraction.extract(request)
        if stage_name == "clustering":
            return self.registry.clusterer.cluster(request)
        if stage_name == "synthesizing":
            return profile.providers.synthesis.synthesize(request)
        if stage_name == "critiquing":
            return profile.providers.critique.critique(request)
        if stage_name == "constructing_patch":
            return profile.providers.patch_construction.construct_patch(request)
        raise ValueError(f"unsupported provider-backed stage: {stage_name}")


def _configuration(
    profile_id: str,
    provider_identity: str,
    *,
    fixture: bool,
) -> RunExecutionConfiguration:
    return RunExecutionConfiguration(
        profile_id=profile_id,
        provider_identity=provider_identity,
        pipeline_version=PIPELINE_VERSION,
        prompt_version=PROMPT_VERSION,
        strategy_version=STRATEGY_CATALOG_VERSION,
        fixture_bundle_id=FIXTURE_BUNDLE_ID if fixture else None,
        fixture_version=FIXTURE_VERSION if fixture else None,
    )


def approved_execution_profiles(
    *,
    live: ProviderSuite,
    fixture: ProviderSuite,
    live_product_selectable: bool = True,
) -> tuple[ExecutionProfile, ...]:
    return (
        ExecutionProfile(
            configuration=_configuration(
                "live_v1",
                "live:gpt-5.6+web+github+stack_exchange:v1",
                fixture=False,
            ),
            providers=live,
            product_selectable=live_product_selectable,
        ),
        ExecutionProfile(
            configuration=_configuration(
                "demo_hybrid_v1",
                "hybrid:security_questionnaires_v1+gpt-5.6:v1",
                fixture=True,
            ),
            providers=ProviderSuite(
                planning=fixture.planning,
                research=fixture.research,
                extraction=fixture.extraction,
                synthesis=live.synthesis,
                critique=live.critique,
                patch_construction=live.patch_construction,
            ),
            product_selectable=live_product_selectable,
        ),
        ExecutionProfile(
            configuration=_configuration(
                "replay_v1",
                "fixture:security_questionnaires_v1:v1",
                fixture=True,
            ),
            providers=fixture,
        ),
    )


__all__ = [
    "ExecutionProfile",
    "ExecutionProfileRegistry",
    "ProfileStageExecutor",
    "ProviderSuite",
    "RegisteredExecutionProfileResolver",
    "approved_execution_profiles",
]
