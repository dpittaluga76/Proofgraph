from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from proofgraph.generation.schemas import (
    GenerationRunRequest,
    ProgressEventEnvelope,
    RunContextEnvelope,
    RunExecutionConfiguration,
    StageResultEnvelope,
)
from proofgraph.graph.models import Canvas, Node


class RunContextFactory(Protocol):
    def build(
        self,
        *,
        canvas: Canvas,
        request: GenerationRunRequest,
        selected_nodes: list[Node],
    ) -> RunContextEnvelope: ...


class ExecutionProfileResolver(Protocol):
    def resolve(self, profile_id: str, *, product_request: bool) -> RunExecutionConfiguration: ...


class StageOutputValidator(Protocol):
    def validate(
        self,
        stage_name: str,
        result: StageResultEnvelope,
        *,
        stage_input: dict[str, Any],
    ) -> StageResultEnvelope: ...


class StageExecutor(Protocol):
    def execute(
        self,
        *,
        stage_name: str,
        stage_input: dict[str, Any],
        configuration: RunExecutionConfiguration,
        progress_callback: Callable[[ProgressEventEnvelope], None] | None = None,
    ) -> StageResultEnvelope: ...


@dataclass(frozen=True)
class ProviderStageRequest:
    stage_input: dict[str, Any]
    configuration: RunExecutionConfiguration
    progress_callback: Callable[[ProgressEventEnvelope], None] | None = None

    def deliver_progress(
        self,
        events: tuple[ProgressEventEnvelope, ...],
    ) -> tuple[ProgressEventEnvelope, ...]:
        if self.progress_callback is None:
            return events
        for event in events:
            self.progress_callback(event)
        return ()


class PlanningProvider(Protocol):
    def plan(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


class ResearchProvider(Protocol):
    def research(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


class ExtractionProvider(Protocol):
    def extract(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


class SynthesisProvider(Protocol):
    def synthesize(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


class CritiqueProvider(Protocol):
    def critique(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


class PatchConstructionProvider(Protocol):
    def construct_patch(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


class DeterministicClusterer(Protocol):
    version: str

    def cluster(self, request: ProviderStageRequest) -> StageResultEnvelope: ...


@dataclass(frozen=True)
class DurableComposition:
    context_factory: RunContextFactory
    profile_resolver: ExecutionProfileResolver
    output_validator: StageOutputValidator
    executor: StageExecutor
