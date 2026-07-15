from __future__ import annotations

import inspect
from typing import Any

import pytest
from django.test import override_settings
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from proofgraph.generation.composition import build_production_composition
from proofgraph.generation.execution import _stage_input_hash
from proofgraph.generation.pipeline_schemas import (
    CritiqueOutput,
    ExtractionOutput,
    GraphPatchOutput,
    PlanningOutput,
    SynthesisOutput,
)
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.profiles import (
    ExecutionProfileRegistry,
    ProfileStageExecutor,
    ProviderSuite,
    RegisteredExecutionProfileResolver,
    approved_execution_profiles,
)
from proofgraph.generation.schemas import StageResultEnvelope
from proofgraph.graph.exceptions import GraphAPIError


class RecordingProviders:
    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.calls: list[str] = []

    def result(self, method: str, request: ProviderStageRequest) -> StageResultEnvelope:
        self.calls.append(method)
        stage_name = {
            "plan": "planning",
            "research": "researching",
            "extract": "extracting",
            "synthesize": "synthesizing",
            "critique": "critiquing",
            "construct_patch": "constructing_patch",
        }[method]
        return StageResultEnvelope(
            stage_name=stage_name,
            output={"adapter": self.identity, "method": method},
            provider_identity=request.configuration.provider_identity,
        )

    def plan(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self.result("plan", request)

    def research(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self.result("research", request)

    def extract(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self.result("extract", request)

    def synthesize(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self.result("synthesize", request)

    def critique(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self.result("critique", request)

    def construct_patch(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self.result("construct_patch", request)


class RecordingClusterer:
    version = "deterministic_clusterer_v1"

    def __init__(self) -> None:
        self.calls = 0

    def cluster(self, request: ProviderStageRequest) -> StageResultEnvelope:
        self.calls += 1
        return StageResultEnvelope(
            stage_name="clustering",
            output={"adapter": "application", "method": "cluster"},
            provider_identity=request.configuration.provider_identity,
        )


def provider_suite(providers: RecordingProviders) -> ProviderSuite:
    return ProviderSuite(
        planning=providers,
        research=providers,
        extraction=providers,
        synthesis=providers,
        critique=providers,
        patch_construction=providers,
    )


def make_registry() -> tuple[
    ExecutionProfileRegistry,
    RecordingProviders,
    RecordingProviders,
    RecordingClusterer,
]:
    live = RecordingProviders("live")
    fixture = RecordingProviders("fixture")
    clusterer = RecordingClusterer()
    profiles = approved_execution_profiles(
        live=provider_suite(live),
        fixture=provider_suite(fixture),
    )
    return ExecutionProfileRegistry(profiles, clusterer=clusterer), live, fixture, clusterer


@pytest.mark.parametrize(
    ("profile_id", "stage_name", "expected_adapter"),
    [
        ("live_v1", "planning", "live"),
        ("live_v1", "researching", "live"),
        ("demo_hybrid_v1", "planning", "fixture"),
        ("demo_hybrid_v1", "extracting", "fixture"),
        ("demo_hybrid_v1", "synthesizing", "live"),
        ("demo_hybrid_v1", "critiquing", "live"),
        ("replay_v1", "constructing_patch", "fixture"),
    ],
)
def test_approved_profiles_route_typed_stage_ports(
    profile_id: str,
    stage_name: str,
    expected_adapter: str,
) -> None:
    registry, _live, _fixture, _clusterer = make_registry()
    configuration = RegisteredExecutionProfileResolver(registry).resolve(
        profile_id,
        product_request=True,
    )

    result = ProfileStageExecutor(registry).execute(
        stage_name=stage_name,
        stage_input={"semantic": "input"},
        configuration=configuration,
    )

    assert result.output["adapter"] == expected_adapter
    assert result.provider_identity == configuration.provider_identity
    assert "is_demo_mode" not in inspect.getsource(ProfileStageExecutor)


def test_clustering_is_one_shared_application_stage_for_every_profile() -> None:
    registry, live, fixture, clusterer = make_registry()
    resolver = RegisteredExecutionProfileResolver(registry)
    executor = ProfileStageExecutor(registry)

    for profile_id in ("live_v1", "demo_hybrid_v1", "replay_v1"):
        result = executor.execute(
            stage_name="clustering",
            stage_input={"claims": []},
            configuration=resolver.resolve(profile_id, product_request=True),
        )
        assert result.output["adapter"] == "application"

    assert clusterer.calls == 3
    assert not live.calls
    assert not fixture.calls


def test_product_resolver_rejects_unregistered_and_phase_two_profiles() -> None:
    registry, _live, _fixture, _clusterer = make_registry()
    resolver = RegisteredExecutionProfileResolver(registry)

    for profile_id in ("missing_v1", "phase2_test_v1"):
        with pytest.raises(GraphAPIError) as captured:
            resolver.resolve(profile_id, product_request=True)
        assert captured.value.status == 422
        assert captured.value.code == "execution_profile_unavailable"


def test_profiles_freeze_all_versions_and_provider_identity_affects_hash() -> None:
    registry, _live, _fixture, _clusterer = make_registry()
    resolver = RegisteredExecutionProfileResolver(registry)
    live = resolver.resolve("live_v1", product_request=True)
    replay = resolver.resolve("replay_v1", product_request=True)
    semantic_input: dict[str, Any] = {"context": {"selected": ["node_one"]}}

    assert live.pipeline_version == replay.pipeline_version == "intelligence_pipeline_v1"
    assert live.prompt_version == replay.prompt_version == "opportunity_pipeline_prompts_v1"
    assert live.strategy_version == replay.strategy_version == "opportunity_strategies_v1"
    assert replay.fixture_bundle_id == "security_questionnaires_v1"
    assert replay.fixture_version == "1"
    assert _stage_input_hash("planning", semantic_input, live) != _stage_input_hash(
        "planning", semantic_input, replay
    )

    with pytest.raises(ValidationError, match="frozen"):
        replay.profile_id = "changed"  # type: ignore[misc]


@override_settings(OPENAI_API_KEY=None)
def test_production_composition_enables_replay_but_fences_live_without_credentials() -> None:
    composition = build_production_composition()

    replay = composition.profile_resolver.resolve("replay_v1", product_request=True)
    assert replay.fixture_bundle_id == "security_questionnaires_v1"
    for profile_id in ("live_v1", "demo_hybrid_v1"):
        with pytest.raises(GraphAPIError) as raised:
            composition.profile_resolver.resolve(profile_id, product_request=True)
        assert raised.value.code == "execution_profile_unavailable"


def test_integrated_composition_enables_exactly_the_three_approved_profiles() -> None:
    composition = build_production_composition(
        openai_client=object(),
        live_product_selectable=True,
    )

    configurations = {
        profile_id: composition.profile_resolver.resolve(profile_id, product_request=True)
        for profile_id in ("live_v1", "demo_hybrid_v1", "replay_v1")
    }
    assert set(configurations) == {"live_v1", "demo_hybrid_v1", "replay_v1"}
    with pytest.raises(GraphAPIError):
        composition.profile_resolver.resolve("phase2_test_v1", product_request=True)


def test_live_semantic_models_compile_to_openai_strict_json_schemas() -> None:
    for model in (
        PlanningOutput,
        ExtractionOutput,
        SynthesisOutput,
        CritiqueOutput,
        GraphPatchOutput,
    ):
        schema = to_strict_json_schema(model)
        assert schema["additionalProperties"] is False
