from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string
from openai import OpenAI

from proofgraph.generation.clustering import ExactEvidenceClusterer
from proofgraph.generation.context import GraphRunContextFactory
from proofgraph.generation.fixtures import FixtureBundle, StrictFixtureProviders
from proofgraph.generation.pipeline_schemas import PipelineStageOutputValidator
from proofgraph.generation.ports import DurableComposition
from proofgraph.generation.profiles import (
    ExecutionProfileRegistry,
    ProfileStageExecutor,
    ProviderSuite,
    RegisteredExecutionProfileResolver,
    approved_execution_profiles,
)
from proofgraph.generation.research_adapters import (
    BoundedResearchProvider,
    GitHubPublicSearchAdapter,
    OpenAIHostedWebSearchAdapter,
    StackExchangeSearchAdapter,
    UserSourceResearchAdapter,
)
from proofgraph.generation.schemas import StageResultEnvelope
from proofgraph.generation.structured_providers import OpenAIStructuredProviders


class StrictStageOutputValidator:
    """Generic Phase 2 envelope validator retained for the isolated test composition."""

    def validate(
        self,
        stage_name: str,
        result: StageResultEnvelope,
        *,
        stage_input: dict[str, Any],
    ) -> StageResultEnvelope:
        del stage_input
        if result.stage_name != stage_name:
            raise ValueError("stage result identity does not match the active stage")
        return StageResultEnvelope.model_validate(result.model_dump(mode="python"))


class LazyOpenAIClient:
    @cached_property
    def _client(self) -> OpenAI:
        return OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=0, timeout=30.0)

    @property
    def responses(self) -> Any:
        return self._client.responses


def build_production_composition(
    *,
    openai_client: Any | None = None,
    live_product_selectable: bool | None = None,
    fixture_root: Path | None = None,
) -> DurableComposition:
    client = openai_client or LazyOpenAIClient()
    if live_product_selectable is None:
        live_product_selectable = bool(settings.OPENAI_API_KEY)
    root = fixture_root or Path(settings.GENERATION_FIXTURE_ROOT)
    fixture = StrictFixtureProviders(FixtureBundle.load(root))
    structured = OpenAIStructuredProviders(client)
    research = BoundedResearchProvider(
        {
            "openai_web_search": OpenAIHostedWebSearchAdapter(client),
            "github": GitHubPublicSearchAdapter(),
            "stack_exchange": StackExchangeSearchAdapter(),
            "user_source": UserSourceResearchAdapter(),
        }
    )
    live = ProviderSuite(
        planning=structured,
        research=research,
        extraction=structured,
        synthesis=structured,
        critique=structured,
        patch_construction=structured,
    )
    fixture_suite = ProviderSuite(
        planning=fixture,
        research=fixture,
        extraction=fixture,
        synthesis=fixture,
        critique=fixture,
        patch_construction=fixture,
    )
    registry = ExecutionProfileRegistry(
        approved_execution_profiles(
            live=live,
            fixture=fixture_suite,
            live_product_selectable=live_product_selectable,
        ),
        clusterer=ExactEvidenceClusterer(),
    )
    return DurableComposition(
        context_factory=GraphRunContextFactory(),
        profile_resolver=RegisteredExecutionProfileResolver(registry),
        output_validator=PipelineStageOutputValidator(),
        executor=ProfileStageExecutor(registry),
    )


def production_composition() -> DurableComposition:
    return build_production_composition()


def get_composition() -> DurableComposition:
    factory = import_string(settings.GENERATION_COMPOSITION_FACTORY)
    composition = factory()
    if not isinstance(composition, DurableComposition):
        raise TypeError("GENERATION_COMPOSITION_FACTORY must return DurableComposition")
    return composition
