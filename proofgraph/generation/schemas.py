from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from proofgraph.generation.models import RunOperation
from proofgraph.generation.retention import validate_progress_payload, validate_retained_payload


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GenerationRunRequest(StrictModel):
    operation: Literal[
        "generate_strategies",
        "research_evidence",
        "synthesize_opportunities",
        "regenerate_stale",
    ]
    selected_node_ids: list[uuid.UUID] = Field(min_length=1)
    expected_node_versions: dict[uuid.UUID, int]
    instruction: str | None = Field(default=None, max_length=4_000)
    execution_profile_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    regeneration_scope: Literal["node", "branch"] | None = None

    @model_validator(mode="after")
    def validate_selection_envelope(self) -> GenerationRunRequest:
        if len(set(self.selected_node_ids)) != len(self.selected_node_ids):
            raise ValueError("selected_node_ids must not contain duplicates")
        if set(self.expected_node_versions) != set(self.selected_node_ids):
            raise ValueError("expected_node_versions must contain exactly the selected node IDs")
        if any(version < 1 for version in self.expected_node_versions.values()):
            raise ValueError("expected node versions must be positive")
        if self.operation == RunOperation.REGENERATE_STALE:
            if self.regeneration_scope is None:
                raise ValueError("regeneration_scope is required for regenerate_stale")
        elif self.regeneration_scope is not None:
            raise ValueError("regeneration_scope is valid only for regenerate_stale")
        if self.instruction is not None:
            stripped = self.instruction.strip()
            self.instruction = stripped or None
        return self


class PatchRegenerationRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def normalize_fields(self) -> PatchRegenerationRequest:
        self.instruction = self.instruction.strip()
        self.idempotency_key = self.idempotency_key.strip()
        if not self.instruction:
            raise ValueError("instruction must not be blank")
        if not self.idempotency_key:
            raise ValueError("idempotency_key must not be blank")
        return self


class PatchApplyRequest(StrictModel):
    selected_operation_ids: list[str] | None = None
    apply_nonconflicting_only: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> PatchApplyRequest:
        if self.selected_operation_ids is None:
            return self
        normalized = [value.strip() for value in self.selected_operation_ids]
        if not normalized or any(not value or len(value) > 200 for value in normalized):
            raise ValueError("selected_operation_ids must contain non-empty operation IDs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("selected_operation_ids must not contain duplicates")
        self.selected_operation_ids = normalized
        return self


class SourceIngestionEnvelope(StrictModel):
    operation_key: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=1, max_length=2_048)
    text: str | None = Field(default=None, min_length=1, max_length=102_400)
    title: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_source_input(self) -> SourceIngestionEnvelope:
        if (self.url is None) == (self.text is None):
            raise ValueError("exactly one of url or text is required")
        if self.text is not None and len(self.text.encode("utf-8")) > 100 * 1024:
            raise ValueError("text exceeds 100 KiB UTF-8")
        if self.operation_key != self.operation_key.strip():
            raise ValueError("operation_key may not contain surrounding whitespace")
        if self.title is not None:
            stripped = self.title.strip()
            if not stripped:
                raise ValueError("title must not be blank")
            self.title = stripped
        return self


class RunExecutionConfiguration(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    profile_id: str
    provider_identity: str
    pipeline_version: str
    prompt_version: str
    strategy_version: str
    fixture_bundle_id: str | None = None
    fixture_version: str | None = None


class RunContextEnvelope(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    snapshot: dict[str, Any]
    manifest: dict[str, Any]
    context_hash: str
    included_node_ids: tuple[uuid.UUID, ...]
    node_versions: dict[str, int]

    @model_validator(mode="after")
    def validate_retention(self) -> RunContextEnvelope:
        validate_retained_payload(self.snapshot)
        validate_retained_payload(self.manifest)
        return self


class ProgressEventEnvelope(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event_type: str
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_retention(self) -> ProgressEventEnvelope:
        validate_progress_payload(self.event_type, self.payload)
        return self


class TokenUsage(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class StageResultEnvelope(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    stage_name: str
    output: dict[str, Any]
    provider_identity: str
    model_response_id: str | None = None
    token_usage: TokenUsage | None = None
    progress_events: tuple[ProgressEventEnvelope, ...] = ()

    @model_validator(mode="after")
    def validate_retention(self) -> StageResultEnvelope:
        validate_retained_payload(self.output)
        return self


class RunErrorEnvelope(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    code: str
    message: str
    retryable: bool
    stage: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
