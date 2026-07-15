from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from proofgraph.generation.schemas import StageResultEnvelope
from proofgraph.generation.strategies import MECHANISM_TAG_VOCABULARY, STRATEGY_BY_ID

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]
EntityId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
IndependenceKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]*:[a-z0-9][a-z0-9_.:-]*$"),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _canonical_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    canonical = tuple(sorted(set(values)))
    if values != canonical:
        raise ValueError(f"{field_name} must be sorted and deduplicated")
    return values


class StrategyCandidate(PipelineModel):
    id: EntityId
    target_node_id: EntityId | None = None
    template_id: Slug
    title: ShortText
    approach: LongText
    rationale: LongText
    required_signal_matches: tuple[ShortText, ...] = Field(min_length=1)
    failure_condition_risks: tuple[ShortText, ...] = ()


class ResearchQuestion(PipelineModel):
    id: EntityId
    question: ShortText
    evidence_type: Slug


class QueryPlanItem(PipelineModel):
    id: EntityId
    query: ShortText
    provider: Literal["openai_web_search", "github", "stack_exchange", "user_source"]
    evidence_type: Slug


class RequiredEvidenceType(PipelineModel):
    evidence_type: Slug
    reason: ShortText
    minimum_independent_sources: int = Field(ge=1, le=5)


class ResearchPlan(PipelineModel):
    target_node_id: EntityId | None = None
    selected_strategy_id: EntityId
    research_questions: tuple[ResearchQuestion, ...] = Field(min_length=1, max_length=10)
    query_plan: tuple[QueryPlanItem, ...] = Field(max_length=5)
    required_evidence_types: tuple[RequiredEvidenceType, ...] = Field(min_length=1)


class PlanningOutput(PipelineModel):
    operation: Literal[
        "generate_strategies",
        "research_evidence",
        "synthesize_opportunities",
        "regenerate_stale",
    ]
    strategies: tuple[StrategyCandidate, ...] = ()
    research_plans: tuple[ResearchPlan, ...] = ()

    @model_validator(mode="after")
    def validate_operation_shape(self) -> PlanningOutput:
        strategy_ids = [strategy.id for strategy in self.strategies]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("strategy candidate IDs must be unique")
        normalized_titles = {
            re.sub(r"\s+", " ", strategy.title).strip().casefold() for strategy in self.strategies
        }
        normalized_approaches = {
            re.sub(r"\s+", " ", strategy.approach).strip().casefold()
            for strategy in self.strategies
        }
        if self.operation == "generate_strategies" and (
            len(normalized_titles) != len(self.strategies)
            or len(normalized_approaches) != len(self.strategies)
        ):
            raise ValueError("strategy candidates must be materially different")
        question_ids = [
            question.id for plan in self.research_plans for question in plan.research_questions
        ]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("research question IDs must be unique across the complete plan")
        queries = [query for plan in self.research_plans for query in plan.query_plan]
        if self.research_plans and not 1 <= len(queries) <= 5:
            raise ValueError("planning must allocate between one and five research queries total")
        query_ids = [query.id for query in queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("research query IDs must be unique across the complete plan")
        if self.operation == "generate_strategies":
            if (
                len(self.strategies) != 3
                or self.research_plans
                or any(strategy.target_node_id is not None for strategy in self.strategies)
            ):
                raise ValueError(
                    "generate_strategies planning must return exactly three strategies"
                )
        elif self.operation == "research_evidence":
            if len(self.research_plans) != 1 or self.strategies:
                raise ValueError("research_evidence planning must return one research plan")
        elif self.operation == "regenerate_stale":
            if bool(self.strategies) == bool(self.research_plans):
                raise ValueError("regeneration planning must return target-localized output")
            target_ids = [
                candidate.target_node_id for candidate in (*self.strategies, *self.research_plans)
            ]
            if any(target_id is None for target_id in target_ids) or len(set(target_ids)) != len(
                target_ids
            ):
                raise ValueError("regeneration planning requires one unique target per output")
        elif self.strategies or self.research_plans:
            raise ValueError("synthesis operations do not accept planning candidates")
        _validate_ip_boundaries(self.model_dump(mode="json"))
        return self


class SourceAuthority(PipelineModel):
    domain: str | None = None
    publisher: str | None = None
    authoritative: bool
    hierarchy_rank: int = Field(ge=1, le=6)


class SourceRecord(PipelineModel):
    id: EntityId
    kind: Literal["web", "github", "stack_exchange", "user_url", "user_text"]
    url: HttpUrl | None = None
    title: ShortText
    retrieved_at: datetime
    content_hash: ContentHash
    independence_key: IndependenceKey
    authority: SourceAuthority
    sanitized_excerpt: str = Field(min_length=1, max_length=500)
    cache_hit: bool = False

    @model_validator(mode="after")
    def require_url_for_remote_source(self) -> SourceRecord:
        if self.kind != "user_text" and self.url is None:
            raise ValueError("remote sources require a URL")
        return self


class ResearchOutput(PipelineModel):
    queries_executed: tuple[QueryPlanItem, ...] = Field(max_length=5)
    sources: tuple[SourceRecord, ...] = Field(max_length=10)
    no_results_reason: ShortText | None = None

    @model_validator(mode="after")
    def explain_empty_results(self) -> ResearchOutput:
        if not self.sources and self.no_results_reason is None:
            raise ValueError("empty research output requires a no-results reason")
        return self


class ClaimRecord(PipelineModel):
    id: EntityId
    claim: LongText
    classification: Literal["observed", "derived", "inferred", "contradicting"]
    evidence_type: Slug
    topic_keys: tuple[Slug, ...] = Field(min_length=1)
    mechanism_tags: tuple[Slug, ...]
    contradiction_target_key: Slug | None = None
    strength: Literal["weak", "medium", "strong"]
    limitations: tuple[ShortText, ...]
    source_ids: tuple[EntityId, ...] = Field(min_length=1)

    @field_validator("topic_keys", "mechanism_tags", "source_ids")
    @classmethod
    def validate_canonical_lists(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        canonical = _canonical_tuple(value, info.field_name)
        if info.field_name == "mechanism_tags":
            unknown = set(canonical) - MECHANISM_TAG_VOCABULARY
            if unknown:
                raise ValueError(f"unknown versioned mechanism tags: {sorted(unknown)}")
        return canonical

    @model_validator(mode="after")
    def validate_contradiction_target(self) -> ClaimRecord:
        if self.classification == "contradicting" and self.contradiction_target_key is None:
            raise ValueError("contradicting claims require contradiction_target_key")
        if self.classification != "contradicting" and self.contradiction_target_key is not None:
            raise ValueError("only contradicting claims may set contradiction_target_key")
        return self


class RejectedEvidence(PipelineModel):
    subject_kind: Literal["source", "claim"]
    source_or_claim_id: EntityId
    reason: Literal["duplicate", "irrelevant", "unsupported", "invalid", "rejected"]
    details: ShortText


class ExtractionOutput(PipelineModel):
    sources: tuple[SourceRecord, ...] = Field(max_length=10)
    claims: tuple[ClaimRecord, ...] = Field(max_length=100)
    candidate_claim_ids: tuple[EntityId, ...] = Field(max_length=100)
    rejected: tuple[RejectedEvidence, ...] = Field(default=(), max_length=110)

    @model_validator(mode="after")
    def validate_claim_source_relations(self) -> ExtractionOutput:
        source_ids = {source.id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("source IDs must be unique")
        claim_ids = {claim.id for claim in self.claims}
        if len(claim_ids) != len(self.claims):
            raise ValueError("claim IDs must be unique")
        _canonical_tuple(self.candidate_claim_ids, "candidate_claim_ids")
        if source_ids & set(self.candidate_claim_ids):
            raise ValueError("source and claim candidate IDs must be disjoint")
        rejected_keys = [
            (rejected.subject_kind, rejected.source_or_claim_id) for rejected in self.rejected
        ]
        if len(rejected_keys) != len(set(rejected_keys)):
            raise ValueError("rejected evidence identities must be unique")
        rejected_source_ids = {
            rejected.source_or_claim_id
            for rejected in self.rejected
            if rejected.subject_kind == "source"
        }
        rejected_claim_ids = {
            rejected.source_or_claim_id
            for rejected in self.rejected
            if rejected.subject_kind == "claim"
        }
        if source_ids & rejected_source_ids:
            raise ValueError("a source cannot be both retained and rejected")
        if claim_ids & rejected_claim_ids:
            raise ValueError("a claim cannot be both retained and rejected")
        if claim_ids | rejected_claim_ids != set(self.candidate_claim_ids):
            raise ValueError(
                "retained and rejected claims must exactly partition candidate_claim_ids"
            )
        for claim in self.claims:
            unknown = set(claim.source_ids) - source_ids
            if unknown:
                raise ValueError(f"claim {claim.id} references unknown sources: {sorted(unknown)}")
        return self


class EvidenceCluster(PipelineModel):
    id: EntityId
    evidence_type: Slug
    topic_keys: tuple[Slug, ...] = Field(min_length=1)
    mechanism_tags: tuple[Slug, ...]
    contradiction_target_key: Slug | None = None
    claim_ids: tuple[EntityId, ...] = Field(min_length=1)
    source_ids: tuple[EntityId, ...] = Field(min_length=1)
    independence_keys: tuple[IndependenceKey, ...] = Field(min_length=1)
    independent_support_count: int = Field(ge=1)

    @field_validator(
        "topic_keys",
        "mechanism_tags",
        "claim_ids",
        "source_ids",
        "independence_keys",
    )
    @classmethod
    def validate_canonical_cluster_lists(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _canonical_tuple(value, info.field_name)

    @model_validator(mode="after")
    def validate_support_count(self) -> EvidenceCluster:
        if self.independent_support_count != len(self.independence_keys):
            raise ValueError("independent_support_count must match distinct independence keys")
        return self


class ClusteringOutput(PipelineModel):
    clusters: tuple[EvidenceCluster, ...]


class DimensionAssessment(PipelineModel):
    rating: Literal["low", "medium", "high"]
    rationale: ShortText


class OpportunityDimensions(PipelineModel):
    evidence_strength: DimensionAssessment
    novelty: DimensionAssessment
    builder_fit: DimensionAssessment
    technical_feasibility: DimensionAssessment
    distribution_clarity: DimensionAssessment
    operational_burden: DimensionAssessment


class OpportunityEvidence(PipelineModel):
    claim_id: EntityId
    signal_type: Literal["spending", "revenue", "labor_cost", "demand", "pain", "contradiction"]
    independence_key: IndependenceKey
    accepted: Literal[True] = True


class AssumptionOutput(PipelineModel):
    id: EntityId
    statement: ShortText
    importance: Literal["low", "medium", "high"]


class RiskOutput(PipelineModel):
    id: EntityId
    statement: ShortText
    impact: Literal["low", "medium", "high"]
    mitigation: ShortText


class ValidationExperimentOutput(PipelineModel):
    id: EntityId
    hypothesis: ShortText
    method: LongText
    metric: ShortText
    success_criteria: ShortText
    timebox: ShortText


class ContradictionOutput(PipelineModel):
    summary: ShortText
    claim_id: EntityId | None = None
    evidence_gap: ShortText | None = None

    @model_validator(mode="after")
    def require_claim_or_gap(self) -> ContradictionOutput:
        if self.claim_id is None and self.evidence_gap is None:
            raise ValueError("a contradiction requires a claim or explicit evidence gap")
        return self


class OpportunityOutput(PipelineModel):
    id: EntityId
    target_node_id: EntityId | None = None
    title: ShortText
    buyer: ShortText
    problem: LongText
    current_spend_or_workaround: LongText
    mechanism: LongText
    business_model: ShortText
    why_now: LongText
    evidence: tuple[OpportunityEvidence, ...] = Field(min_length=1)
    contradiction: ContradictionOutput
    assumptions: tuple[AssumptionOutput, ...] = Field(min_length=1)
    risks: tuple[RiskOutput, ...] = Field(min_length=1)
    validation_experiment: ValidationExperimentOutput
    builder_fit: LongText
    distribution_channel: ShortText
    distribution_rationale: LongText
    defensibility: LongText
    technical_feasibility: LongText
    operational_burden: LongText
    dimensions: OpportunityDimensions
    support_status: Literal["supported", "speculative"]

    @model_validator(mode="after")
    def enforce_supported_threshold(self) -> OpportunityOutput:
        if self.support_status != "supported":
            return self
        cost_signals = {
            evidence.signal_type
            for evidence in self.evidence
            if evidence.signal_type in {"spending", "revenue", "labor_cost"}
        }
        demand_keys = {
            evidence.independence_key
            for evidence in self.evidence
            if evidence.signal_type in {"demand", "pain"}
        }
        demand_claims = {
            evidence.claim_id
            for evidence in self.evidence
            if evidence.signal_type in {"demand", "pain"}
        }
        has_contradiction = (
            any(evidence.signal_type == "contradiction" for evidence in self.evidence)
            or self.contradiction.claim_id is not None
        )
        if (
            not cost_signals
            or len(demand_keys) < 2
            or len(demand_claims) < 2
            or not has_contradiction
        ):
            raise ValueError(
                "supported opportunities do not meet the structured evidence threshold"
            )
        return self


class SynthesisOutput(PipelineModel):
    operation: Literal["synthesize_opportunities", "regenerate_stale"]
    opportunities: tuple[OpportunityOutput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cardinality(self) -> SynthesisOutput:
        opportunity_ids = [opportunity.id for opportunity in self.opportunities]
        if len(opportunity_ids) != len(set(opportunity_ids)):
            raise ValueError("opportunity IDs must be unique")
        if self.operation == "synthesize_opportunities" and len(self.opportunities) != 3:
            raise ValueError("synthesize_opportunities must return exactly three candidates")
        if self.operation == "regenerate_stale":
            target_ids = [opportunity.target_node_id for opportunity in self.opportunities]
            if any(target_id is None for target_id in target_ids) or len(set(target_ids)) != len(
                target_ids
            ):
                raise ValueError(
                    "regeneration synthesis requires one unique target per opportunity"
                )
        family_ids = [
            family_id
            for opportunity in self.opportunities
            for family_id in (
                opportunity.id,
                *(assumption.id for assumption in opportunity.assumptions),
                *(risk.id for risk in opportunity.risks),
                opportunity.validation_experiment.id,
            )
        ]
        if len(family_ids) != len(set(family_ids)):
            raise ValueError("opportunity-family IDs must be unique across the synthesis output")
        _validate_ip_boundaries(self.model_dump(mode="json"))
        return self


class CritiqueRecord(PipelineModel):
    opportunity_id: EntityId
    novelty: LongText
    feasibility: LongText
    buyer_and_budget: LongText
    recurrence: LongText
    distribution: LongText
    operational_burden: LongText
    differentiation: LongText
    builder_fit: LongText
    falsifying_evidence: LongText
    material_contradiction_or_gap: LongText
    recommendation: Literal["advance", "revise", "reject"]


class CritiqueOutput(PipelineModel):
    critiques: tuple[CritiqueRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ip_boundaries(self) -> CritiqueOutput:
        opportunity_ids = [critique.opportunity_id for critique in self.critiques]
        if len(opportunity_ids) != len(set(opportunity_ids)):
            raise ValueError("critique may cover each opportunity only once")
        _validate_ip_boundaries(self.model_dump(mode="json"))
        return self


class PatchPosition(PipelineModel):
    x: int | FiniteFloat
    y: int | FiniteFloat


class PatchNode(PipelineModel):
    kind: Literal[
        "goal",
        "constraint",
        "strategy",
        "source",
        "claim",
        "opportunity",
        "assumption",
        "risk",
        "validation_experiment",
        "generation_placeholder",
    ]
    title: ShortText
    body: str | None = Field(default=None, max_length=4_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    branch_root_node_id: EntityId | None = None
    position: PatchPosition | None = None

    @model_validator(mode="after")
    def validate_retained_node_payload(self) -> PatchNode:
        from proofgraph.generation.retention import validate_retained_payload

        validate_retained_payload(self.model_dump(mode="json"))
        provenance = self.metadata.get("provenance_node_ids")
        if provenance is not None and (
            not isinstance(provenance, list)
            or not all(isinstance(value, str) for value in provenance)
            or provenance != sorted(set(provenance))
        ):
            raise ValueError("provenance_node_ids must be sorted and deduplicated strings")
        scope = self.metadata.get("context_scope")
        if self.kind != "constraint" and self.branch_root_node_id is not None:
            raise ValueError("only constraint nodes may declare branch_root_node_id")
        if self.kind == "constraint":
            if scope == "branch" and self.branch_root_node_id is None:
                raise ValueError("branch constraints require branch_root_node_id")
            if scope == "global" and self.branch_root_node_id is not None:
                raise ValueError("global constraints may not declare branch_root_node_id")
        return self


class PatchEdge(PipelineModel):
    source_node_id: EntityId
    target_node_id: EntityId
    kind: Literal[
        "supports",
        "contradicts",
        "derived_from",
        "constrained_by",
        "evolves_into",
        "requires_validation",
        "extracted_from",
    ]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_retained_edge_payload(self) -> PatchEdge:
        from proofgraph.generation.retention import validate_retained_payload

        validate_retained_payload(self.model_dump(mode="json"))
        return self


class PatchOperationCandidate(PipelineModel):
    operation_id: EntityId
    op: Literal[
        "ADD_NODE",
        "UPDATE_NODE",
        "DELETE_NODE",
        "ADD_EDGE",
        "UPDATE_EDGE",
        "DELETE_EDGE",
        "PATCH_NODE_METADATA",
        "MOVE_NODE",
    ]
    depends_on: tuple[EntityId, ...] = ()
    client_generated_id: EntityId | None = None
    node: PatchNode | None = None
    edge: PatchEdge | None = None
    node_id: EntityId | None = None
    edge_id: EntityId | None = None
    expected_version: int | None = Field(default=None, ge=1)
    expected_position_version: int | None = Field(default=None, ge=1)
    changes: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    position: PatchPosition | None = None
    required_incident_edge_ids: tuple[EntityId, ...] = ()
    required_branch_constraint_ids: tuple[EntityId, ...] = ()

    @model_validator(mode="after")
    def validate_operation_shape(self) -> PatchOperationCandidate:
        _canonical_tuple(self.depends_on, "depends_on")
        _canonical_tuple(self.required_incident_edge_ids, "required_incident_edge_ids")
        _canonical_tuple(self.required_branch_constraint_ids, "required_branch_constraint_ids")

        active = {
            "client_generated_id": self.client_generated_id is not None,
            "node": self.node is not None,
            "edge": self.edge is not None,
            "node_id": self.node_id is not None,
            "edge_id": self.edge_id is not None,
            "expected_version": self.expected_version is not None,
            "expected_position_version": self.expected_position_version is not None,
            "changes": self.changes is not None,
            "metadata": self.metadata is not None,
            "position": self.position is not None,
            "required_incident_edge_ids": bool(self.required_incident_edge_ids),
            "required_branch_constraint_ids": bool(self.required_branch_constraint_ids),
        }
        required: dict[str, set[str]] = {
            "ADD_NODE": {"client_generated_id", "node"},
            "UPDATE_NODE": {"node_id", "expected_version", "changes"},
            "DELETE_NODE": {"node_id", "expected_version"},
            "ADD_EDGE": {"client_generated_id", "edge"},
            "UPDATE_EDGE": {"edge_id", "expected_version", "changes"},
            "DELETE_EDGE": {"edge_id", "expected_version"},
            "PATCH_NODE_METADATA": {"node_id", "expected_version", "metadata"},
            "MOVE_NODE": {"node_id", "expected_position_version", "position"},
        }
        allowed = set(required[self.op])
        if self.op == "DELETE_NODE":
            allowed.update({"required_incident_edge_ids", "required_branch_constraint_ids"})
        missing = sorted(field for field in required[self.op] if not active[field])
        incompatible = sorted(
            field for field, is_active in active.items() if is_active and field not in allowed
        )
        if missing:
            raise ValueError(f"{self.op} is missing required fields: {missing}")
        if incompatible:
            raise ValueError(f"{self.op} contains incompatible fields: {incompatible}")
        if self.changes is not None and not self.changes:
            raise ValueError(f"{self.op} changes must not be empty")
        if self.op == "UPDATE_NODE" and self.changes is not None:
            unknown = set(self.changes) - {
                "title",
                "body",
                "metadata",
                "branch_root_node_id",
            }
            if unknown:
                raise ValueError(f"UPDATE_NODE contains unsupported changes: {sorted(unknown)}")
        if self.op == "UPDATE_EDGE" and self.changes is not None:
            unknown = set(self.changes) - {
                "source_node_id",
                "target_node_id",
                "kind",
                "metadata",
            }
            if unknown:
                raise ValueError(f"UPDATE_EDGE contains unsupported changes: {sorted(unknown)}")
        if self.metadata is not None and not self.metadata:
            raise ValueError("PATCH_NODE_METADATA metadata must not be empty")
        from proofgraph.generation.retention import validate_retained_payload

        validate_retained_payload(self.model_dump(mode="json"))
        return self


class GraphPatchOutput(PipelineModel):
    base_canvas_revision: int = Field(ge=0)
    known_node_ids: tuple[EntityId, ...] = ()
    known_edge_ids: tuple[EntityId, ...] = ()
    operations: tuple[PatchOperationCandidate, ...]
    regeneration_target_ids: tuple[EntityId, ...] = ()
    permitted_stale_resolution_ids: tuple[EntityId, ...] = ()

    @model_validator(mode="after")
    def validate_dependency_graph(self) -> GraphPatchOutput:
        _canonical_tuple(self.known_node_ids, "known_node_ids")
        _canonical_tuple(self.known_edge_ids, "known_edge_ids")
        _canonical_tuple(self.regeneration_target_ids, "regeneration_target_ids")
        _canonical_tuple(
            self.permitted_stale_resolution_ids,
            "permitted_stale_resolution_ids",
        )
        operation_by_id = {operation.operation_id: operation for operation in self.operations}
        if len(operation_by_id) != len(self.operations):
            raise ValueError("patch operation IDs must be unique")
        local_creators = {
            operation.client_generated_id: operation.operation_id
            for operation in self.operations
            if operation.client_generated_id is not None
        }
        local_node_creators = {
            operation.client_generated_id: operation.operation_id
            for operation in self.operations
            if operation.op == "ADD_NODE" and operation.client_generated_id is not None
        }
        if len(local_creators) != sum(
            operation.client_generated_id is not None for operation in self.operations
        ):
            raise ValueError("client_generated_id values must be unique within the patch")
        for operation in self.operations:
            unknown_dependencies = set(operation.depends_on) - operation_by_id.keys()
            if unknown_dependencies:
                raise ValueError(
                    f"unresolved operation dependencies: {sorted(unknown_dependencies)}"
                )
            if operation.node_id is not None and operation.node_id not in self.known_node_ids:
                raise ValueError(
                    f"node operation references unknown server node: {operation.node_id}"
                )
            if operation.edge_id is not None and operation.edge_id not in self.known_edge_ids:
                raise ValueError(
                    f"edge operation references unknown server edge: {operation.edge_id}"
                )
            references: set[str] = set()
            if operation.edge is not None:
                references.update({operation.edge.source_node_id, operation.edge.target_node_id})
            if operation.node is not None:
                provenance = operation.node.metadata.get("provenance_node_ids")
                if isinstance(provenance, list):
                    references.update(item for item in provenance if isinstance(item, str))
                if operation.node.branch_root_node_id is not None:
                    references.add(operation.node.branch_root_node_id)
            if operation.changes is not None:
                for key in ("source_node_id", "target_node_id", "branch_root_node_id"):
                    value = operation.changes.get(key)
                    if isinstance(value, str):
                        references.add(value)
            for reference in references:
                if reference in self.known_node_ids:
                    continue
                creator = local_node_creators.get(reference)
                if creator is None or creator not in operation.depends_on:
                    raise ValueError(f"unresolved or dependency-free local reference: {reference}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(operation_id: str) -> None:
            if operation_id in visiting:
                raise ValueError("patch operation dependencies must be acyclic")
            if operation_id in visited:
                return
            visiting.add(operation_id)
            for dependency in operation_by_id[operation_id].depends_on:
                visit(dependency)
            visiting.remove(operation_id)
            visited.add(operation_id)

        for operation_id in operation_by_id:
            visit(operation_id)
        delete_edge_operations = {
            operation.edge_id: operation.operation_id
            for operation in self.operations
            if operation.op == "DELETE_EDGE" and operation.edge_id is not None
        }
        resolved_constraint_operations = {
            operation.node_id: operation.operation_id
            for operation in self.operations
            if operation.node_id is not None
            and (
                operation.op == "DELETE_NODE"
                or (
                    operation.op == "UPDATE_NODE"
                    and operation.changes is not None
                    and "branch_root_node_id" in operation.changes
                )
            )
        }
        for operation in self.operations:
            if operation.op != "DELETE_NODE":
                continue
            if set(operation.required_incident_edge_ids) - delete_edge_operations.keys():
                raise ValueError("DELETE_NODE is missing incident-edge prerequisites")
            if (
                set(operation.required_branch_constraint_ids)
                - resolved_constraint_operations.keys()
            ):
                raise ValueError("DELETE_NODE is missing branch-constraint prerequisites")
            prerequisite_operations = {
                delete_edge_operations[edge_id] for edge_id in operation.required_incident_edge_ids
            } | {
                resolved_constraint_operations[node_id]
                for node_id in operation.required_branch_constraint_ids
            }
            if prerequisite_operations - set(operation.depends_on):
                raise ValueError("DELETE_NODE must depend on every declared prerequisite operation")
            if set(operation.required_incident_edge_ids) - set(self.known_edge_ids):
                raise ValueError("DELETE_NODE references an unknown incident edge")
            if set(operation.required_branch_constraint_ids) - set(self.known_node_ids):
                raise ValueError("DELETE_NODE references an unknown branch constraint")
        return self


_PROTECTED_ACTION = (
    r"(?P<action>copy(?:ing)?|clone|duplicat(?:e|ing)|mirror(?:ing)?|reproduc(?:e|ing)|"
    r"replicat(?:e|ing)|steal(?:ing)?|lift(?:ing)?|reverse[- ]engineer(?:ing)?|"
    r"reimplement(?:ing)?|recreat(?:e|ing)|port(?:ing)?|appropriat(?:e|ing))"
)
_PROTECTED_MATERIAL = (
    r"(?:code|implementation|internals?|source|assets?|ui|interface|datasets?|data|"
    r"records?|models?|weights|corpus|corpora|training examples?|customer information|"
    r"internal behavior|product expression|look and feel)"
)
_PROTECTED_MARKER = (
    r"(?:proprietary|closed[- ]source|protected|private|non[- ]public|confidential|"
    r"trade[- ]secret|internal[- ]only)"
)
_PROHIBITED_INTENT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"\b{_PROTECTED_ACTION}\b.{{0,120}}\b{_PROTECTED_MARKER}\b.{{0,120}}\b{_PROTECTED_MATERIAL}\b",
        rf"\b{_PROTECTED_ACTION}\b.{{0,120}}\b{_PROTECTED_MATERIAL}\b.{{0,120}}\b{_PROTECTED_MARKER}\b",
        (
            r"\b(?P<action>impersonat(?:e|ing)|mimic(?:king)?|masquerad(?:e|ing) as|"
            r"pass(?:ing)? off as)\b.{0,100}\b(?:brand|trademark|mark)\b"
        ),
        (
            r"\b(?P<action>creat(?:e|ing)|use|adopt)\b.{0,80}\bconfusingly[- ]similar\b"
            r".{0,80}\b(?:brand|name|mark|trademark)\b"
        ),
        (
            r"\b(?P<action>reuse|copy|extract|train on|scrape|use|import|ingest|buy|acquire)"
            r"\b.{0,120}\b(?:private|non[- ]public|proprietary|confidential|"
            r"trade[- ]secret|internal[- ]only)\b.{0,100}"
            r"\b(?:datasets?|data|records?|corpus|corpora|training examples?|"
            r"customer information)\b"
        ),
        (
            r"\b(?P<action>violate|bypass|evade|ignore|circumvent|disregard)\w*\b"
            r".{0,120}\b(?:third[- ]party |vendor |platform )?"
            r"(?:(?:api )?terms|conditions of use|usage policy|terms of service|"
            r"service agreement|developer agreement|acceptable use policy)\b"
        ),
        (
            r"\b(?P<action>bypass|evade|circumvent|scrape behind|defeat)\b.{0,120}"
            r"\b(?:login|paywall|access controls?|rate limits?|authentication)\b"
        ),
        (
            r"\b(?P<action>study|studying|analy[sz]e|analy[sz]ing|inspect|inspecting|"
            r"observe|observing|trace|tracing)\b.{0,120}\b(?:internal behavior|internals?|"
            r"implementation details?)\b.{0,120}\b(?:closed|proprietary|commercial)"
            r"(?:[- ]source)?\b.{0,80}\b(?:competitor|rival|product|service)\b"
        ),
    )
)
_SAFE_PREFIX = re.compile(
    r"(?:\b(?:do|does|did|should|must|will|would|can|could)\s+not|"
    r"\b(?:cannot|can't|don't|doesn't|didn't|shouldn't|mustn't|won't|wouldn't|"
    r"couldn't)\b|\bnot|\bnever|\bavoid(?:s|ed|ing)?|\brefus(?:e|es|ed|ing)|"
    r"\bprohibit(?:s|ed|ing)?|\bprevent(?:s|ed|ing)?|\bblock(?:s|ed|ing)?|"
    r"\bdisallow(?:s|ed|ing)?|\bforbid(?:s|den|ding)?|"
    r"\bwarn(?:s|ed|ing)?\s+against)"
    r"(?:\s+[a-z0-9'-]+){0,6}\s*$"
)
_MONITORING_PREFIX = re.compile(
    r"\b(?:detect|detects|detected|detecting|monitor|monitors|monitored|monitoring)\s+"
    r"(?:when|whether|attempts?\s+to)(?:\s+[a-z0-9'-]+){0,5}\s*$"
)
_DESCRIPTIVE_THIRD_PARTY_PREFIX = re.compile(
    r"\b(?:attackers?|competitors?|incumbents?|suppliers?|third parties|users?|vendors?)\b"
    r".{0,60}\b(?:may|might|could|can|would)\s*$"
)
_NEGATIVE_ASSESSMENT_SUFFIX = re.compile(
    r"^.{0,100}\b(?:a risk|compliance risk|security risk|dangerous|illegal|not allowed|"
    r"prohibited|unacceptable|unsafe|a violation|must be prevented|must be blocked)\b"
)


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _text_values(child)]
    if isinstance(value, (list, tuple)):
        return [text for child in value for text in _text_values(child)]
    return []


def _is_guardrail_or_description(clause: str, match: re.Match[str]) -> bool:
    action_start = match.start("action")
    prefix = clause[max(0, action_start - 100) : action_start]
    if _SAFE_PREFIX.search(prefix):
        return True
    if _MONITORING_PREFIX.search(prefix):
        return True
    if _DESCRIPTIVE_THIRD_PARTY_PREFIX.search(prefix):
        return True
    return bool(_NEGATIVE_ASSESSMENT_SUFFIX.search(clause[match.end() :]))


def _validate_ip_boundaries(value: Any) -> None:
    for text in _text_values(value):
        normalized = re.sub(r"\s+", " ", text.casefold()).strip()
        clauses = re.split(r"(?<=[.!?;])\s+|\b(?:but|however)\b", normalized)
        for clause in clauses:
            for pattern in _PROHIBITED_INTENT_PATTERNS:
                match = pattern.search(clause)
                if match is not None and not _is_guardrail_or_description(clause, match):
                    raise ValueError(
                        "output violates intellectual-property or third-party-terms boundaries"
                    )


STAGE_OUTPUT_MODELS: dict[str, type[PipelineModel]] = {
    "planning": PlanningOutput,
    "researching": ResearchOutput,
    "extracting": ExtractionOutput,
    "clustering": ClusteringOutput,
    "synthesizing": SynthesisOutput,
    "critiquing": CritiqueOutput,
    "constructing_patch": GraphPatchOutput,
}


_OPPORTUNITY_FAMILY_KINDS = {
    "opportunity",
    "assumption",
    "risk",
    "validation_experiment",
}
_GENERATED_METADATA_BASE = {
    "description",
    "generated_by_run_id",
    "lineage_mode",
    "notes",
    "provenance_node_ids",
    "regenerated_from_node_id",
    "regeneration_scope",
    "review_status",
    "summary",
    "tags",
}
_CONSTRAINT_CLONE_METADATA_FIELDS = {
    "category",
    "context_scope",
    "description",
    "generated_by_run_id",
    "notes",
    "pinned",
    "provenance_node_ids",
    "review_status",
    "summary",
    "tags",
}
_GENERATED_METADATA_ALLOWED = {
    "constraint": _CONSTRAINT_CLONE_METADATA_FIELDS,
    "strategy": _GENERATED_METADATA_BASE
    | {"approach", "rationale", "strategy_template_id", "target_segment"},
    "source": _GENERATED_METADATA_BASE
    | {
        "authority",
        "canonical_url",
        "content_hash",
        "content_type",
        "independence_key",
        "retrieved_at",
        "sanitized_excerpt",
        "source_kind",
        "untrusted_source",
        "url",
    },
    "claim": _GENERATED_METADATA_BASE
    | {
        "classification",
        "contradiction_target_key",
        "evidence_type",
        "independence_keys",
        "limitations",
        "mechanism_tags",
        "source_ids",
        "strength",
        "topic_keys",
    },
    "opportunity": _GENERATED_METADATA_BASE
    | {
        "assumptions",
        "builder_fit",
        "business_model",
        "buyer",
        "contradiction",
        "current_spend_or_workaround",
        "defensibility",
        "dimensions",
        "distribution_channel",
        "distribution_rationale",
        "evidence",
        "mechanism",
        "operational_burden",
        "problem",
        "risks",
        "support_status",
        "technical_feasibility",
        "validation_experiment",
        "why_now",
    },
    "assumption": _GENERATED_METADATA_BASE | {"category", "importance"},
    "risk": _GENERATED_METADATA_BASE | {"category", "impact", "likelihood", "mitigation"},
    "validation_experiment": _GENERATED_METADATA_BASE
    | {"hypothesis", "method", "metric", "success_criteria", "timebox"},
}
_GENERATED_METADATA_REQUIRED = {
    "constraint": {
        "context_scope",
        "generated_by_run_id",
        "pinned",
        "provenance_node_ids",
        "review_status",
    },
    "strategy": {
        "approach",
        "generated_by_run_id",
        "provenance_node_ids",
        "rationale",
        "strategy_template_id",
    },
    "source": {
        "authority",
        "content_hash",
        "generated_by_run_id",
        "independence_key",
        "provenance_node_ids",
        "retrieved_at",
        "sanitized_excerpt",
        "source_kind",
    },
    "claim": {
        "classification",
        "evidence_type",
        "generated_by_run_id",
        "limitations",
        "mechanism_tags",
        "provenance_node_ids",
        "source_ids",
        "strength",
        "topic_keys",
    },
    "opportunity": {
        "assumptions",
        "builder_fit",
        "business_model",
        "buyer",
        "contradiction",
        "current_spend_or_workaround",
        "defensibility",
        "dimensions",
        "distribution_channel",
        "distribution_rationale",
        "evidence",
        "generated_by_run_id",
        "mechanism",
        "operational_burden",
        "problem",
        "provenance_node_ids",
        "risks",
        "support_status",
        "technical_feasibility",
        "validation_experiment",
        "why_now",
    },
    "assumption": {"generated_by_run_id", "importance", "provenance_node_ids"},
    "risk": {"generated_by_run_id", "impact", "mitigation", "provenance_node_ids"},
    "validation_experiment": {
        "generated_by_run_id",
        "hypothesis",
        "method",
        "metric",
        "provenance_node_ids",
        "success_criteria",
        "timebox",
    },
}


def _stage_operation(stage_input: dict[str, Any]) -> str | None:
    manifest = stage_input.get("context_manifest")
    if not isinstance(manifest, dict):
        return None
    request = manifest.get("request")
    return request.get("operation") if isinstance(request, dict) else None


def _semantic_snapshot(stage_input: dict[str, Any]) -> dict[str, Any]:
    snapshot = stage_input.get("context_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("the stage requires a valid semantic graph snapshot")
    if not isinstance(snapshot.get("nodes"), list) or not isinstance(snapshot.get("edges"), list):
        raise ValueError("the semantic graph snapshot is malformed")
    return snapshot


def parallel_constraint_clone_specs(
    stage_input: dict[str, Any],
    successor_by_target: dict[str, str],
) -> list[dict[str, Any]]:
    """Resolve each applicable branch constraint to its closest successor root(s)."""

    if not successor_by_target:
        return []
    snapshot = _semantic_snapshot(stage_input)
    nodes = {
        str(node["id"]): node
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id")
    }
    manifest = stage_input.get("context_manifest") or {}
    anchors = manifest.get("branch_constraint_anchors") if isinstance(manifest, dict) else {}
    if not isinstance(anchors, dict):
        raise ValueError("branch_constraint_anchors must be an object")

    adjacency: dict[str, set[str]] = {}
    for edge in snapshot["edges"]:
        if not isinstance(edge, dict):
            continue
        source_id = edge.get("source_node_id")
        target_id = edge.get("target_node_id")
        kind = edge.get("kind")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            continue
        ancestor_id, descendant_id = (
            (target_id, source_id)
            if kind in {"constrained_by", "extracted_from"}
            else (source_id, target_id)
        )
        adjacency.setdefault(ancestor_id, set()).add(descendant_id)

    specs: list[dict[str, Any]] = []
    target_ids = set(successor_by_target)
    for constraint_id, anchor_id in sorted(anchors.items()):
        if not isinstance(constraint_id, str) or not isinstance(anchor_id, str):
            raise ValueError("branch constraint anchors must use string node IDs")
        constraint = nodes.get(constraint_id)
        if constraint is None or constraint.get("kind") != "constraint":
            raise ValueError("branch constraint anchor references an unavailable constraint")
        distances = {anchor_id: 0}
        queue = [anchor_id]
        for current in queue:
            for descendant_id in sorted(adjacency.get(current, ())):
                if descendant_id in distances:
                    continue
                distances[descendant_id] = distances[current] + 1
                queue.append(descendant_id)
        reachable = {
            target_id: distances[target_id] for target_id in target_ids if target_id in distances
        }
        if not reachable:
            raise ValueError("an applicable branch constraint has no regenerated successor root")
        minimum_distance = min(reachable.values())
        for target_id in sorted(
            target_id for target_id, distance in reachable.items() if distance == minimum_distance
        ):
            specs.append(
                {
                    "constraint_id": constraint_id,
                    "constraint": constraint,
                    "target_id": target_id,
                    "successor_id": successor_by_target[target_id],
                }
            )
    return specs


def _prior_stage_output(stage_input: dict[str, Any], stage_name: str) -> dict[str, Any]:
    matches = _prior_stage_outputs(stage_input, stage_name)
    return matches[-1]


def _prior_stage_outputs(stage_input: dict[str, Any], stage_name: str) -> list[dict[str, Any]]:
    prior = stage_input.get("prior_stage_outputs")
    if not isinstance(prior, dict):
        raise ValueError(f"{stage_name} requires a completed prior checkpoint")
    matches = [
        value for key, value in prior.items() if key == stage_name or key.endswith(f":{stage_name}")
    ]
    if not matches or not all(isinstance(match, dict) for match in matches):
        raise ValueError(f"{stage_name} requires a completed prior checkpoint")
    outputs = [match.get("output") for match in matches]
    if not all(isinstance(output, dict) for output in outputs):
        raise ValueError(f"a {stage_name} checkpoint is malformed")
    return outputs


def _target_ids(stage_input: dict[str, Any]) -> set[str]:
    return {
        str(target.get("node_id"))
        for target in stage_input.get("target_workset") or []
        if isinstance(target, dict) and target.get("node_id")
    }


def _stale_target_ids(stage_input: dict[str, Any]) -> set[str]:
    stale_ids: set[str] = set()
    for target in stage_input.get("target_workset") or []:
        if not isinstance(target, dict):
            continue
        values = target.get("stale_node_ids")
        if isinstance(values, list):
            stale_ids.update(str(value) for value in values)
        elif target.get("node_id"):
            stale_ids.add(str(target["node_id"]))
    return stale_ids


def _member_target_ids(stage_input: dict[str, Any]) -> set[str]:
    member_ids: set[str] = set()
    for target in stage_input.get("target_workset") or []:
        if not isinstance(target, dict):
            continue
        values = target.get("member_node_ids")
        if isinstance(values, list):
            member_ids.update(str(value) for value in values)
        elif target.get("node_id"):
            member_ids.add(str(target["node_id"]))
    return member_ids


def _semantic_ancestor_ids(snapshot: dict[str, Any], node_id: str) -> set[str]:
    """Return cycle-safe semantic ancestors using the frozen dependency directions."""
    parents: dict[str, set[str]] = {}
    reverse_direction = {"constrained_by", "extracted_from"}
    for edge in snapshot["edges"]:
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source_node_id") or "")
        target_id = str(edge.get("target_node_id") or "")
        kind = str(edge.get("kind") or "")
        if not source_id or not target_id:
            continue
        if kind in reverse_direction:
            parents.setdefault(source_id, set()).add(target_id)
        else:
            parents.setdefault(target_id, set()).add(source_id)
    visited: set[str] = set()
    pending = sorted(parents.get(node_id, set()))
    while pending:
        current = pending.pop(0)
        if current == node_id or current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(parents.get(current, set()) - visited))
    return visited


def _prior_strategy_replacements(stage_input: dict[str, Any]) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for value in _prior_models(stage_input, "planning", PlanningOutput):
        assert isinstance(value, PlanningOutput)
        for candidate in value.strategies:
            if candidate.target_node_id is not None:
                replacements[candidate.target_node_id] = candidate.id
    return replacements


def _validate_planning_output(output: PlanningOutput, stage_input: dict[str, Any]) -> None:
    operation = _stage_operation(stage_input)
    if output.operation != operation:
        raise ValueError("planning output operation does not match the run operation")
    template_ids = [strategy.template_id for strategy in output.strategies]
    if any(template_id not in STRATEGY_BY_ID for template_id in template_ids):
        raise ValueError("planning must select strategies from the frozen catalog")
    if operation == "generate_strategies" and len(template_ids) != len(set(template_ids)):
        raise ValueError("new strategy generation must select unique strategy templates")
    manifest = stage_input.get("context_manifest") or {}
    selected = set(manifest.get("explicit_node_ids") or [])
    allowed_targets = selected if operation == "research_evidence" else _target_ids(stage_input)
    snapshot = _semantic_snapshot(stage_input)
    node_kinds = {
        str(node.get("id")): str(node.get("kind"))
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id")
    }
    known_node_ids = set(node_kinds)
    prior_replacements = _prior_strategy_replacements(stage_input)
    for plan in output.research_plans:
        if plan.target_node_id is not None and plan.target_node_id not in allowed_targets:
            raise ValueError("research planning references an unselected target")
        if operation == "research_evidence":
            selected_strategies = {
                node_id for node_id in selected if node_kinds.get(node_id) == "strategy"
            }
            if selected_strategies != {plan.selected_strategy_id}:
                raise ValueError("research planning must use the explicitly selected strategy")
            if plan.target_node_id != plan.selected_strategy_id:
                raise ValueError("research planning target must be the selected strategy")
        else:
            assert plan.target_node_id is not None
            ancestor_strategies = {
                node_id
                for node_id in _semantic_ancestor_ids(snapshot, plan.target_node_id)
                if node_kinds.get(node_id) == "strategy"
            }
            allowed_strategies = {
                prior_replacements.get(strategy_id, strategy_id)
                for strategy_id in ancestor_strategies
            }
            if plan.selected_strategy_id not in allowed_strategies:
                raise ValueError(
                    "regeneration research planning must use the target's linked strategy"
                )
        if (
            plan.selected_strategy_id not in known_node_ids
            and plan.selected_strategy_id not in prior_replacements.values()
        ):
            raise ValueError("research planning references an unknown strategy")
    if operation == "regenerate_stale":
        localized = {
            candidate.target_node_id for candidate in (*output.strategies, *output.research_plans)
        }
        if localized != allowed_targets:
            raise ValueError("regeneration planning does not match its frozen production units")


def _validate_research_output(output: ResearchOutput, stage_input: dict[str, Any]) -> None:
    if not output.sources:
        raise ValueError(output.no_results_reason or "No useful search results were found.")
    planning = PlanningOutput.model_validate_json(
        json.dumps(_prior_stage_output(stage_input, "planning"))
    )
    planned_queries = {
        query.id: query for plan in planning.research_plans for query in plan.query_plan
    }
    if {query.id for query in output.queries_executed} != set(planned_queries):
        raise ValueError("research must execute the complete frozen query plan")
    for query in output.queries_executed:
        if query != planned_queries[query.id]:
            raise ValueError("research must preserve every planned query exactly")


def _validate_extraction_output(output: ExtractionOutput, stage_input: dict[str, Any]) -> None:
    research = ResearchOutput.model_validate_json(
        json.dumps(_prior_stage_output(stage_input, "researching"))
    )
    known_sources = {source.id: source for source in research.sources}
    retained_source_ids = {source.id for source in output.sources}
    for source in output.sources:
        if source.id not in known_sources or source != known_sources[source.id]:
            raise ValueError("extraction must preserve exact researched source identities")
    rejected_source_ids = {
        rejected.source_or_claim_id
        for rejected in output.rejected
        if rejected.subject_kind == "source"
    }
    unknown_rejections = rejected_source_ids - set(known_sources)
    if unknown_rejections:
        raise ValueError(
            f"extraction rejects unknown researched sources: {sorted(unknown_rejections)}"
        )
    if retained_source_ids | rejected_source_ids != set(known_sources):
        raise ValueError("extraction must retain or explicitly reject every researched source")
    if len(output.claims) > 12:
        raise ValueError("extraction output must already contain the deterministic retained set")


_SIGNAL_EVIDENCE_TYPES = {
    "spending": {"spending", "budget", "current_spend", "pricing"},
    "revenue": {"revenue", "revenue_signal"},
    "labor_cost": {"labor_cost", "time_cost", "deal_delay"},
    "demand": {"demand", "buyer_demand", "workflow_recurrence", "willingness_to_pay"},
    "pain": {"pain", "customer_pain", "workflow_pain", "workaround"},
}


def _claim_facts(stage_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshot = _semantic_snapshot(stage_input)
    nodes = {
        str(node.get("id")): node
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id")
    }
    facts: dict[str, dict[str, Any]] = {}
    for node_id, node in nodes.items():
        if node.get("kind") != "claim":
            continue
        metadata = node.get("metadata")
        facts[node_id] = {
            "classification": metadata.get("classification")
            if isinstance(metadata, dict)
            else None,
            "evidence_type": metadata.get("evidence_type") if isinstance(metadata, dict) else None,
            "independence_keys": set(),
            "source": "snapshot",
        }
    for edge in snapshot["edges"]:
        if not isinstance(edge, dict) or edge.get("kind") != "extracted_from":
            continue
        claim_id = str(edge.get("source_node_id") or "")
        source_id = str(edge.get("target_node_id") or "")
        claim = nodes.get(claim_id)
        source = nodes.get(source_id)
        if (
            not claim
            or not source
            or claim.get("kind") != "claim"
            or source.get("kind") != "source"
        ):
            continue
        metadata = source.get("metadata")
        independence_key = metadata.get("independence_key") if isinstance(metadata, dict) else None
        if isinstance(independence_key, str):
            facts[claim_id]["independence_keys"].add(independence_key)

    if _stage_operation(stage_input) == "regenerate_stale":
        prior = stage_input.get("prior_stage_outputs")
        if isinstance(prior, dict):
            for key, value in prior.items():
                if not (key == "extracting" or key.endswith(":extracting")):
                    continue
                if not isinstance(value, dict) or not isinstance(value.get("output"), dict):
                    raise ValueError("an extracting checkpoint is malformed")
                extraction = ExtractionOutput.model_validate_json(json.dumps(value["output"]))
                source_by_id = {source.id: source for source in extraction.sources}
                for claim in extraction.claims:
                    facts[claim.id] = {
                        "classification": claim.classification,
                        "evidence_type": claim.evidence_type,
                        "independence_keys": {
                            source_by_id[source_id].independence_key
                            for source_id in claim.source_ids
                        },
                        "source": "prior_extraction",
                    }
    return facts


def _signal_matches_claim(signal_type: str, fact: dict[str, Any]) -> bool:
    if signal_type == "contradiction":
        return fact.get("classification") == "contradicting"
    return fact.get("evidence_type") in _SIGNAL_EVIDENCE_TYPES.get(signal_type, set())


def _validate_synthesis_output(output: SynthesisOutput, stage_input: dict[str, Any]) -> None:
    operation = _stage_operation(stage_input)
    if output.operation != operation:
        raise ValueError("synthesis output operation does not match the run operation")
    manifest = stage_input.get("context_manifest") or {}
    explicit_ids = set(manifest.get("explicit_node_ids") or [])
    snapshot = _semantic_snapshot(stage_input)
    selected_claim_ids = {
        str(node.get("id"))
        for node in snapshot["nodes"]
        if isinstance(node, dict)
        and node.get("kind") == "claim"
        and (operation == "regenerate_stale" or str(node.get("id")) in explicit_ids)
    }
    claim_facts = _claim_facts(stage_input)
    if operation == "regenerate_stale":
        selected_claim_ids.update(
            claim_id
            for claim_id, fact in claim_facts.items()
            if fact.get("source") == "prior_extraction"
        )
    for opportunity in output.opportunities:
        evidence_claim_ids = {evidence.claim_id for evidence in opportunity.evidence}
        referenced_claim_ids = set(evidence_claim_ids)
        if opportunity.contradiction.claim_id:
            referenced_claim_ids.add(opportunity.contradiction.claim_id)
        if referenced_claim_ids - selected_claim_ids:
            raise ValueError("synthesis references provisional or unselected claims")
        for evidence in opportunity.evidence:
            fact = claim_facts[evidence.claim_id]
            if evidence.independence_key not in fact["independence_keys"]:
                raise ValueError(
                    "synthesis evidence independence keys must follow the selected claim's "
                    "accepted source relations"
                )
            if not _signal_matches_claim(evidence.signal_type, fact):
                raise ValueError(
                    "synthesis evidence signal types must match authoritative claim metadata"
                )
        contradiction_id = opportunity.contradiction.claim_id
        if (
            contradiction_id is not None
            and claim_facts[contradiction_id].get("classification") != "contradicting"
        ):
            raise ValueError("synthesis contradiction references a non-contradicting claim")
    if operation == "regenerate_stale" and {
        opportunity.target_node_id for opportunity in output.opportunities
    } != _target_ids(stage_input):
        raise ValueError("regeneration synthesis does not match its frozen production units")


def _validate_critique_output(output: CritiqueOutput, stage_input: dict[str, Any]) -> None:
    synthesis = SynthesisOutput.model_validate_json(
        json.dumps(_prior_stage_output(stage_input, "synthesizing"))
    )
    if {critique.opportunity_id for critique in output.critiques} != {
        opportunity.id for opportunity in synthesis.opportunities
    }:
        raise ValueError("critique must cover every synthesized opportunity exactly once")


def _allowed_patch_node_kinds(operation: str | None, stage_input: dict[str, Any]) -> set[str]:
    if operation == "generate_strategies":
        return {"strategy"}
    if operation == "research_evidence":
        return {"source", "claim"}
    if operation == "synthesize_opportunities":
        return set(_OPPORTUNITY_FAMILY_KINDS)
    if operation != "regenerate_stale":
        return set()
    target_kinds = {
        str(target.get("kind"))
        for target in stage_input.get("target_workset") or []
        if isinstance(target, dict) and target.get("kind")
    }
    allowed: set[str] = set()
    if "strategy" in target_kinds:
        allowed.add("strategy")
    if "claim" in target_kinds:
        allowed.update({"source", "claim"})
    if target_kinds & _OPPORTUNITY_FAMILY_KINDS:
        allowed.update(_OPPORTUNITY_FAMILY_KINDS)
    if target_kinds:
        allowed.add("constraint")
    return allowed


def _has_lineage_edge(
    *,
    node_kind: str,
    local_id: str,
    provenance_id: str,
    edges: tuple[PatchEdge, ...],
) -> bool:
    patterns = {
        "strategy": {(provenance_id, local_id, "evolves_into")},
        "source": {(provenance_id, local_id, "derived_from")},
        "claim": {(local_id, provenance_id, "extracted_from")},
        "opportunity": {
            (provenance_id, local_id, "supports"),
            (provenance_id, local_id, "contradicts"),
        },
        "assumption": {(provenance_id, local_id, "derived_from")},
        "risk": {(provenance_id, local_id, "derived_from")},
        "validation_experiment": {(provenance_id, local_id, "requires_validation")},
    }
    expected = patterns.get(node_kind, set())
    return any((edge.source_node_id, edge.target_node_id, edge.kind) in expected for edge in edges)


def _validate_generated_metadata(node: PatchNode, *, run_id: str) -> None:
    metadata = node.metadata
    allowed = _GENERATED_METADATA_ALLOWED.get(node.kind)
    required = _GENERATED_METADATA_REQUIRED.get(node.kind)
    if allowed is None or required is None:
        raise ValueError(f"generation patches may not add {node.kind} nodes")
    unknown = set(metadata) - allowed
    if unknown:
        raise ValueError(
            f"generated {node.kind} metadata contains wrong-kind fields: {sorted(unknown)}"
        )
    missing = required - set(metadata)
    if missing:
        raise ValueError(f"generated {node.kind} metadata is missing fields: {sorted(missing)}")
    if metadata.get("generated_by_run_id") != run_id:
        raise ValueError("generated nodes require the originating run ID")
    if metadata.get("review_status") not in {None, "provisional"}:
        raise ValueError("candidate generated nodes must remain provisional until patch acceptance")


def _parallel_successors(
    output: GraphPatchOutput,
    stage_input: dict[str, Any],
) -> dict[str, str]:
    target_kinds = {
        str(target.get("node_id")): str(target.get("kind"))
        for target in stage_input.get("target_workset") or []
        if isinstance(target, dict) and target.get("node_id") and target.get("kind")
    }
    scope = ((stage_input.get("context_manifest") or {}).get("request") or {}).get(
        "regeneration_scope"
    )
    if scope not in {"node", "branch"}:
        raise ValueError("regeneration patches require a frozen regeneration scope")

    successor_by_target: dict[str, str] = {}
    lineage_fields = {"regenerated_from_node_id", "regeneration_scope", "lineage_mode"}
    for candidate in output.operations:
        if candidate.op != "ADD_NODE" or candidate.node is None:
            continue
        metadata = candidate.node.metadata
        present = lineage_fields & set(metadata)
        is_production_root = candidate.node.kind in {"strategy", "claim", "opportunity"}
        if present and not is_production_root:
            raise ValueError("only regenerated production roots may declare parallel lineage")
        if not present:
            continue
        if present != lineage_fields:
            raise ValueError(
                "regenerated production roots require complete parallel lineage metadata"
            )
        old_target_id = metadata["regenerated_from_node_id"]
        if not isinstance(old_target_id, str) or old_target_id not in target_kinds:
            raise ValueError("regenerated_from_node_id must name a frozen production target")
        if candidate.node.kind != target_kinds[old_target_id]:
            raise ValueError("a regenerated successor must preserve its production-root kind")
        if metadata["regeneration_scope"] != scope or metadata["lineage_mode"] != "parallel":
            raise ValueError(
                "regenerated production roots require the frozen parallel lineage mode"
            )
        if old_target_id in successor_by_target:
            raise ValueError("each frozen production target requires exactly one successor root")
        assert candidate.client_generated_id is not None
        successor_by_target[old_target_id] = candidate.client_generated_id
    if set(successor_by_target) != set(target_kinds):
        raise ValueError("regeneration patch successors do not match the frozen target workset")
    return successor_by_target


def _validate_constraint_clones(
    output: GraphPatchOutput,
    stage_input: dict[str, Any],
    successor_by_target: dict[str, str],
) -> None:
    run_id = str(stage_input.get("run_id") or "")
    specs = parallel_constraint_clone_specs(stage_input, successor_by_target)
    expected_by_key = {(spec["constraint_id"], spec["successor_id"]): spec for spec in specs}
    clone_operations = [
        candidate
        for candidate in output.operations
        if candidate.op == "ADD_NODE"
        and candidate.node is not None
        and candidate.node.kind == "constraint"
    ]
    actual_by_key: dict[tuple[str, str], PatchOperationCandidate] = {}
    for candidate in clone_operations:
        assert candidate.node is not None
        provenance = candidate.node.metadata.get("provenance_node_ids")
        if not isinstance(provenance, list) or len(provenance) != 1:
            raise ValueError(
                "constraint clones require exactly one original constraint provenance ID"
            )
        branch_root_id = candidate.node.branch_root_node_id
        if branch_root_id is None:
            raise ValueError("constraint clones require a successor branch root")
        key = (provenance[0], branch_root_id)
        if key in actual_by_key:
            raise ValueError(
                "each applicable branch constraint may be cloned only once per successor"
            )
        actual_by_key[key] = candidate
    if set(actual_by_key) != set(expected_by_key):
        raise ValueError(
            "constraint clones do not match applicable branch constraints and successors"
        )

    lineage_operation_by_successor: dict[str, str] = {}
    for candidate in output.operations:
        if candidate.op != "ADD_EDGE" or candidate.edge is None:
            continue
        if candidate.edge.kind != "evolves_into":
            continue
        expected_successor = successor_by_target.get(candidate.edge.source_node_id)
        if expected_successor == candidate.edge.target_node_id:
            lineage_operation_by_successor[candidate.edge.target_node_id] = candidate.operation_id

    copyable_fields = _CONSTRAINT_CLONE_METADATA_FIELDS - {
        "generated_by_run_id",
        "provenance_node_ids",
        "review_status",
    }
    for key, candidate in actual_by_key.items():
        spec = expected_by_key[key]
        original = spec["constraint"]
        original_metadata = original.get("metadata")
        if not isinstance(original_metadata, dict):
            raise ValueError("an applicable branch constraint has invalid semantic metadata")
        expected_metadata = {
            field: original_metadata[field]
            for field in sorted(copyable_fields)
            if field in original_metadata
        }
        expected_metadata.update(
            {
                "generated_by_run_id": run_id,
                "provenance_node_ids": [spec["constraint_id"]],
                "review_status": "provisional",
            }
        )
        assert candidate.node is not None
        if (
            candidate.node.title != original.get("title")
            or candidate.node.body != original.get("body")
            or candidate.node.metadata != expected_metadata
        ):
            raise ValueError("constraint clone content must exactly preserve the frozen constraint")
        lineage_operation_id = lineage_operation_by_successor.get(spec["successor_id"])
        if lineage_operation_id is None or lineage_operation_id not in candidate.depends_on:
            raise ValueError("constraint clones must depend on the successor lineage operation")


def _prior_models(
    stage_input: dict[str, Any],
    stage_name: str,
    model: type[PipelineModel],
) -> list[PipelineModel]:
    prior = stage_input.get("prior_stage_outputs")
    if not isinstance(prior, dict):
        return []
    outputs: list[PipelineModel] = []
    for key, value in prior.items():
        if not (key == stage_name or key.endswith(f":{stage_name}")):
            continue
        if not isinstance(value, dict) or not isinstance(value.get("output"), dict):
            raise ValueError(f"a {stage_name} checkpoint is malformed")
        outputs.append(model.model_validate_json(json.dumps(value["output"])))
    return outputs


def _require_metadata(node: PatchNode, expected: dict[str, Any], *, label: str) -> None:
    mismatches = {
        key: {"expected": value, "actual": node.metadata.get(key)}
        for key, value in expected.items()
        if node.metadata.get(key) != value
    }
    if mismatches:
        raise ValueError(f"patch {label} metadata diverges from its validated stage output")


def _validate_strategy_patch_node(node: PatchNode, candidate: StrategyCandidate) -> None:
    if node.kind != "strategy" or node.title != candidate.title or node.body != candidate.approach:
        raise ValueError("patch strategy content diverges from planning output")
    _require_metadata(
        node,
        {
            "approach": candidate.approach,
            "rationale": candidate.rationale,
            "strategy_template_id": candidate.template_id,
        },
        label="strategy",
    )


def _validate_source_patch_node(node: PatchNode, source: SourceRecord) -> None:
    if node.kind != "source" or node.title != source.title or node.body != source.sanitized_excerpt:
        raise ValueError("patch source content diverges from extraction output")
    expected = {
        "authority": source.authority.model_dump(mode="json"),
        "content_hash": source.content_hash,
        "independence_key": source.independence_key,
        "retrieved_at": source.retrieved_at.isoformat().replace("+00:00", "Z"),
        "sanitized_excerpt": source.sanitized_excerpt,
        "source_kind": source.kind,
    }
    if source.url is not None:
        expected["url"] = str(source.url)
    _require_metadata(node, expected, label="source")


def _validate_claim_patch_node(
    node: PatchNode,
    claim: ClaimRecord,
    source_by_id: dict[str, SourceRecord],
) -> None:
    if node.kind != "claim" or node.body != claim.claim:
        raise ValueError("patch claim content diverges from extraction output")
    independence_keys = sorted(
        {source_by_id[source_id].independence_key for source_id in claim.source_ids}
    )
    expected = {
        "classification": claim.classification,
        "evidence_type": claim.evidence_type,
        "independence_keys": independence_keys,
        "limitations": list(claim.limitations),
        "mechanism_tags": list(claim.mechanism_tags),
        "source_ids": list(claim.source_ids),
        "strength": claim.strength,
        "topic_keys": list(claim.topic_keys),
    }
    if claim.contradiction_target_key is not None:
        expected["contradiction_target_key"] = claim.contradiction_target_key
    _require_metadata(node, expected, label="claim")
    if node.metadata.get("provenance_node_ids") != list(claim.source_ids):
        raise ValueError("patch claim provenance diverges from extraction source relations")


def _validate_opportunity_family_patch(
    added_nodes: dict[str, PatchNode],
    opportunity: OpportunityOutput,
) -> set[str]:
    node = added_nodes.get(opportunity.id)
    if node is None or node.kind != "opportunity":
        raise ValueError("patch is missing a synthesized opportunity")
    if node.title != opportunity.title or node.body != opportunity.mechanism:
        raise ValueError("patch opportunity content diverges from synthesis output")
    opportunity_payload = opportunity.model_dump(mode="json")
    expected_metadata = {
        key: value
        for key, value in opportunity_payload.items()
        if key not in {"id", "target_node_id", "title"}
    }
    _require_metadata(node, expected_metadata, label="opportunity")
    provenance_ids = sorted(
        {
            *(evidence.claim_id for evidence in opportunity.evidence),
            *(
                [opportunity.contradiction.claim_id]
                if opportunity.contradiction.claim_id is not None
                else []
            ),
        }
    )
    if node.metadata.get("provenance_node_ids") != provenance_ids:
        raise ValueError("patch opportunity provenance diverges from synthesis evidence")

    family_ids = {opportunity.id}
    for assumption in opportunity.assumptions:
        family_ids.add(assumption.id)
        assumption_node = added_nodes.get(assumption.id)
        if (
            assumption_node is None
            or assumption_node.kind != "assumption"
            or assumption_node.body != assumption.statement
        ):
            raise ValueError("patch assumption diverges from synthesis output")
        _require_metadata(
            assumption_node,
            {"importance": assumption.importance},
            label="assumption",
        )
        if assumption_node.metadata.get("provenance_node_ids") != [opportunity.id]:
            raise ValueError("patch assumption has incorrect opportunity provenance")
    for risk in opportunity.risks:
        family_ids.add(risk.id)
        risk_node = added_nodes.get(risk.id)
        if risk_node is None or risk_node.kind != "risk" or risk_node.body != risk.statement:
            raise ValueError("patch risk diverges from synthesis output")
        _require_metadata(
            risk_node,
            {"impact": risk.impact, "mitigation": risk.mitigation},
            label="risk",
        )
        if risk_node.metadata.get("provenance_node_ids") != [opportunity.id]:
            raise ValueError("patch risk has incorrect opportunity provenance")
    experiment = opportunity.validation_experiment
    family_ids.add(experiment.id)
    experiment_node = added_nodes.get(experiment.id)
    if (
        experiment_node is None
        or experiment_node.kind != "validation_experiment"
        or experiment_node.body != experiment.method
    ):
        raise ValueError("patch validation experiment diverges from synthesis output")
    _require_metadata(
        experiment_node,
        {key: value for key, value in experiment.model_dump(mode="json").items() if key != "id"},
        label="validation experiment",
    )
    if experiment_node.metadata.get("provenance_node_ids") != [opportunity.id]:
        raise ValueError("patch validation experiment has incorrect opportunity provenance")
    return family_ids


def _validate_patch_stage_binding(
    output: GraphPatchOutput,
    stage_input: dict[str, Any],
    *,
    known_node_ids: set[str],
) -> None:
    operation = _stage_operation(stage_input)
    added_nodes = {
        str(candidate.client_generated_id): candidate.node
        for candidate in output.operations
        if candidate.op == "ADD_NODE"
        and candidate.client_generated_id is not None
        and candidate.node is not None
    }
    planning_outputs = [
        value
        for value in _prior_models(stage_input, "planning", PlanningOutput)
        if isinstance(value, PlanningOutput)
    ]
    extraction_outputs = [
        value
        for value in _prior_models(stage_input, "extracting", ExtractionOutput)
        if isinstance(value, ExtractionOutput)
    ]
    synthesis_outputs = [
        value
        for value in _prior_models(stage_input, "synthesizing", SynthesisOutput)
        if isinstance(value, SynthesisOutput)
    ]
    critique_outputs = [
        value
        for value in _prior_models(stage_input, "critiquing", CritiqueOutput)
        if isinstance(value, CritiqueOutput)
    ]

    strategies = {
        candidate.id: candidate
        for planning in planning_outputs
        for candidate in planning.strategies
    }
    sources = {
        source.id: source for extraction in extraction_outputs for source in extraction.sources
    }
    claims = {claim.id: claim for extraction in extraction_outputs for claim in extraction.claims}
    opportunities = {
        opportunity.id: opportunity
        for synthesis in synthesis_outputs
        for opportunity in synthesis.opportunities
    }
    if opportunities:
        critiqued_ids = {
            critique.opportunity_id
            for critique_output in critique_outputs
            for critique in critique_output.critiques
        }
        if critiqued_ids != set(opportunities):
            raise ValueError("patch opportunities do not match the validated critique checkpoint")

    required_ids: set[str] = set()
    selected_claim_ids: set[str] = set()
    if operation == "generate_strategies":
        if len(strategies) != 3:
            raise ValueError("strategy patch requires exactly three validated planning candidates")
        required_ids.update(strategies)
    elif operation == "research_evidence":
        if not extraction_outputs:
            raise ValueError("research patch requires a validated extraction checkpoint")
        required_ids.update(set(sources) - known_node_ids)
        required_ids.update(claims)
        selected_claim_ids.update(claims)
    elif operation == "synthesize_opportunities":
        if len(opportunities) != 3:
            raise ValueError("opportunity patch requires exactly three synthesized candidates")
    elif operation == "regenerate_stale":
        targets = {
            str(target.get("node_id")): str(target.get("kind"))
            for target in stage_input.get("target_workset") or []
            if isinstance(target, dict) and target.get("node_id") and target.get("kind")
        }
        strategy_targets = {target_id for target_id, kind in targets.items() if kind == "strategy"}
        if strategy_targets:
            mapped = {candidate.target_node_id for candidate in strategies.values()}
            if mapped != strategy_targets:
                raise ValueError("strategy replacements do not match the frozen target workset")
            required_ids.update(strategies)
        claim_targets = {target_id for target_id, kind in targets.items() if kind == "claim"}
        if claim_targets:
            patch_claims = {
                local_id: node for local_id, node in added_nodes.items() if node.kind == "claim"
            }
            mapped = {
                node.metadata.get("regenerated_from_node_id") for node in patch_claims.values()
            }
            if (
                len(patch_claims) != len(claim_targets)
                or mapped != claim_targets
                or set(patch_claims) - set(claims)
            ):
                raise ValueError("claim replacements do not match the frozen target workset")
            selected_claim_ids.update(patch_claims)
            required_ids.update(patch_claims)
            required_source_ids = {
                source_id
                for claim_id in patch_claims
                for source_id in claims[claim_id].source_ids
                if source_id not in known_node_ids
            }
            required_ids.update(required_source_ids)
        opportunity_targets = {
            target_id for target_id, kind in targets.items() if kind in _OPPORTUNITY_FAMILY_KINDS
        }
        if opportunity_targets:
            mapped = {opportunity.target_node_id for opportunity in opportunities.values()}
            if mapped != opportunity_targets:
                raise ValueError("opportunity replacements do not match the frozen target workset")
        required_ids.update(
            local_id for local_id, node in added_nodes.items() if node.kind == "constraint"
        )
    else:
        raise ValueError("patch construction requires a known run operation")

    for candidate_id, candidate in strategies.items():
        node = added_nodes.get(candidate_id)
        if node is None:
            raise ValueError("patch is missing a validated strategy candidate")
        _validate_strategy_patch_node(node, candidate)
        if (
            operation == "regenerate_stale"
            and node.metadata.get("regenerated_from_node_id") != candidate.target_node_id
        ):
            raise ValueError("patch strategy replacement target diverges from planning output")
    for source_id in required_ids & set(sources):
        _validate_source_patch_node(added_nodes[source_id], sources[source_id])
    source_by_id = sources
    for claim_id in selected_claim_ids:
        _validate_claim_patch_node(added_nodes[claim_id], claims[claim_id], source_by_id)

    for opportunity in opportunities.values():
        family_ids = _validate_opportunity_family_patch(added_nodes, opportunity)
        required_ids.update(family_ids)
        if (
            operation == "regenerate_stale"
            and added_nodes[opportunity.id].metadata.get("regenerated_from_node_id")
            != opportunity.target_node_id
        ):
            raise ValueError("patch opportunity replacement target diverges from synthesis output")

    if set(added_nodes) != required_ids:
        raise ValueError("patch added-node set does not match its validated stage outputs")


def _expected_patch_relations(
    output: GraphPatchOutput,
    stage_input: dict[str, Any],
) -> tuple[dict[str, list[str]], Counter[tuple[str, str, str]]]:
    """Derive the exact generated provenance and edges from validated checkpoints."""
    added_nodes = {
        str(candidate.client_generated_id): candidate.node
        for candidate in output.operations
        if candidate.op == "ADD_NODE"
        and candidate.client_generated_id is not None
        and candidate.node is not None
    }
    snapshot = _semantic_snapshot(stage_input)
    node_kinds = {
        str(node.get("id")): str(node.get("kind"))
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id")
    }
    planning_outputs = [
        value
        for value in _prior_models(stage_input, "planning", PlanningOutput)
        if isinstance(value, PlanningOutput)
    ]
    extraction_outputs = [
        value
        for value in _prior_models(stage_input, "extracting", ExtractionOutput)
        if isinstance(value, ExtractionOutput)
    ]
    synthesis_outputs = [
        value
        for value in _prior_models(stage_input, "synthesizing", SynthesisOutput)
        if isinstance(value, SynthesisOutput)
    ]
    strategies = [candidate for planning in planning_outputs for candidate in planning.strategies]
    strategy_replacements = {
        candidate.target_node_id: candidate.id
        for candidate in strategies
        if candidate.target_node_id is not None
    }
    explicit_ids = set((stage_input.get("context_manifest") or {}).get("explicit_node_ids") or [])
    explicit_goals = sorted(
        node_id for node_id in explicit_ids if node_kinds.get(node_id) == "goal"
    )
    if not explicit_goals:
        explicit_goals = sorted(
            node_id for node_id, node_kind in node_kinds.items() if node_kind == "goal"
        )

    provenance_by_id: dict[str, list[str]] = {}
    expected_edges: Counter[tuple[str, str, str]] = Counter()

    def bind(
        local_id: str, parent_ids: list[str], edge_kind: str, *, reverse: bool = False
    ) -> None:
        canonical_parents = sorted(set(parent_ids))
        provenance_by_id[local_id] = canonical_parents
        for parent_id in canonical_parents:
            relation = (
                (local_id, parent_id, edge_kind) if reverse else (parent_id, local_id, edge_kind)
            )
            expected_edges[relation] += 1

    for candidate in strategies:
        if candidate.id not in added_nodes:
            continue
        parent_goal_ids = explicit_goals
        if candidate.target_node_id is not None:
            parent_goal_ids = sorted(
                ancestor_id
                for ancestor_id in _semantic_ancestor_ids(snapshot, candidate.target_node_id)
                if node_kinds.get(ancestor_id) == "goal"
            )
        bind(candidate.id, parent_goal_ids, "evolves_into")

    research_strategy_ids = sorted(
        {
            strategy_replacements.get(plan.selected_strategy_id, plan.selected_strategy_id)
            for planning in planning_outputs
            for plan in planning.research_plans
        }
    )
    sources = {
        source.id: source for extraction in extraction_outputs for source in extraction.sources
    }
    claims = {claim.id: claim for extraction in extraction_outputs for claim in extraction.claims}
    for source_id in sorted(set(sources) & set(added_nodes)):
        bind(source_id, research_strategy_ids, "derived_from")
    for claim_id in sorted(set(claims) & set(added_nodes)):
        bind(claim_id, list(claims[claim_id].source_ids), "extracted_from", reverse=True)

    for synthesis in synthesis_outputs:
        for opportunity in synthesis.opportunities:
            if opportunity.id not in added_nodes:
                continue
            provenance_ids = sorted(
                {
                    *(evidence.claim_id for evidence in opportunity.evidence),
                    *(
                        [opportunity.contradiction.claim_id]
                        if opportunity.contradiction.claim_id is not None
                        else []
                    ),
                }
            )
            provenance_by_id[opportunity.id] = provenance_ids
            for evidence in opportunity.evidence:
                edge_kind = "contradicts" if evidence.signal_type == "contradiction" else "supports"
                expected_edges[(evidence.claim_id, opportunity.id, edge_kind)] = 1
            if opportunity.contradiction.claim_id is not None:
                expected_edges[
                    (opportunity.contradiction.claim_id, opportunity.id, "contradicts")
                ] = 1
            for assumption in opportunity.assumptions:
                if assumption.id in added_nodes:
                    bind(assumption.id, [opportunity.id], "derived_from")
            for risk in opportunity.risks:
                if risk.id in added_nodes:
                    bind(risk.id, [opportunity.id], "derived_from")
            experiment_id = opportunity.validation_experiment.id
            if experiment_id in added_nodes:
                bind(experiment_id, [opportunity.id], "requires_validation")
    if _stage_operation(stage_input) == "regenerate_stale":
        for local_id, node in sorted(added_nodes.items()):
            old_target_id = node.metadata.get("regenerated_from_node_id")
            if node.kind in {"strategy", "claim", "opportunity"} and isinstance(old_target_id, str):
                expected_edges[(old_target_id, local_id, "evolves_into")] += 1
    return provenance_by_id, expected_edges


def _validate_patch_output(output: GraphPatchOutput, stage_input: dict[str, Any]) -> None:
    snapshot = _semantic_snapshot(stage_input)
    known_node_ids = sorted(
        str(node["id"]) for node in snapshot["nodes"] if isinstance(node, dict) and node.get("id")
    )
    known_edge_ids = sorted(
        str(edge["id"]) for edge in snapshot["edges"] if isinstance(edge, dict) and edge.get("id")
    )
    if list(output.known_node_ids) != known_node_ids:
        raise ValueError("patch known_node_ids do not match the frozen context")
    if list(output.known_edge_ids) != known_edge_ids:
        raise ValueError("patch known_edge_ids do not match the frozen context")
    if output.base_canvas_revision != stage_input.get("base_canvas_revision"):
        raise ValueError("patch base revision does not match the frozen run")
    operation = _stage_operation(stage_input)
    frozen_targets = _target_ids(stage_input)
    stale_targets = _stale_target_ids(stage_input)
    member_targets = _member_target_ids(stage_input)
    if operation == "regenerate_stale":
        if set(output.regeneration_target_ids) != frozen_targets:
            raise ValueError("patch regeneration targets do not match the frozen production units")
        if set(output.permitted_stale_resolution_ids) != stale_targets:
            raise ValueError(
                "patch must declare the exact stale members permitted by the frozen workset"
            )
    elif output.regeneration_target_ids or output.permitted_stale_resolution_ids:
        raise ValueError("non-regeneration patches may not permit stale-target resolution")

    run_id = str(stage_input.get("run_id") or "")
    allowed_add_kinds = _allowed_patch_node_kinds(operation, stage_input)
    local_node_kinds = {
        str(candidate.client_generated_id): candidate.node.kind
        for candidate in output.operations
        if candidate.op == "ADD_NODE"
        and candidate.client_generated_id is not None
        and candidate.node is not None
    }
    known_node_kinds = {
        str(node["id"]): str(node.get("kind"))
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id")
    }
    node_kinds = {**known_node_kinds, **local_node_kinds}
    _validate_patch_stage_binding(output, stage_input, known_node_ids=set(known_node_ids))
    successor_by_target: dict[str, str] = {}
    if operation == "regenerate_stale":
        successor_by_target = _parallel_successors(output, stage_input)
    node_versions = {
        str(node["id"]): node.get("version")
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id")
    }
    edge_versions = {
        str(edge["id"]): edge.get("version")
        for edge in snapshot["edges"]
        if isinstance(edge, dict) and edge.get("id")
    }
    added_edges = tuple(
        candidate.edge
        for candidate in output.operations
        if candidate.op == "ADD_EDGE" and candidate.edge is not None
    )
    expected_provenance, expected_edges = _expected_patch_relations(output, stage_input)
    actual_edges = Counter(
        (edge.source_node_id, edge.target_node_id, edge.kind) for edge in added_edges
    )
    if actual_edges != expected_edges:
        raise ValueError("patch edge set does not exactly match validated semantic lineage")
    if operation == "regenerate_stale":
        _validate_constraint_clones(output, stage_input, successor_by_target)
    valid_provenance_ids = set(known_node_ids) | set(local_node_kinds)
    snapshot_edges = {
        str(edge["id"]): edge
        for edge in snapshot["edges"]
        if isinstance(edge, dict) and edge.get("id")
    }

    for candidate in output.operations:
        if candidate.op == "MOVE_NODE":
            raise ValueError("semantic intelligence patches may not move authoritative nodes")
        if operation != "regenerate_stale" and candidate.op not in {"ADD_NODE", "ADD_EDGE"}:
            raise ValueError("new-generation patches may only add localized nodes and edges")
        if operation == "regenerate_stale" and candidate.op not in {"ADD_NODE", "ADD_EDGE"}:
            raise ValueError(
                "parallel regeneration patches may only add fresh successor nodes and edges"
            )
        if candidate.node_id is not None:
            if operation == "regenerate_stale" and candidate.node_id not in member_targets:
                raise ValueError(
                    "regeneration may mutate only members of its frozen production units"
                )
            if (
                candidate.op != "MOVE_NODE"
                and node_versions.get(candidate.node_id) != candidate.expected_version
            ):
                raise ValueError("patch node version does not match the frozen context")
        if candidate.edge_id is not None:
            if edge_versions.get(candidate.edge_id) != candidate.expected_version:
                raise ValueError("patch edge version does not match the frozen context")
            if operation == "regenerate_stale":
                edge = snapshot_edges[candidate.edge_id]
                if not member_targets & {
                    str(edge.get("source_node_id")),
                    str(edge.get("target_node_id")),
                }:
                    raise ValueError(
                        "regeneration may mutate only edges incident to its production units"
                    )
        if (
            candidate.op == "ADD_EDGE"
            and candidate.edge is not None
            and candidate.edge.metadata.get("generated_by_run_id") != run_id
        ):
            raise ValueError("generated edges require the originating run ID")
        if candidate.op != "ADD_NODE" or candidate.node is None:
            continue
        if candidate.node.kind not in allowed_add_kinds:
            raise ValueError(f"{operation} patches may not add {candidate.node.kind} nodes")
        _validate_generated_metadata(candidate.node, run_id=run_id)
        provenance = candidate.node.metadata.get("provenance_node_ids")
        if (
            not isinstance(provenance, list)
            or not provenance
            or provenance != sorted(set(provenance))
            or set(provenance) - valid_provenance_ids
        ):
            raise ValueError("generated nodes require canonical known provenance IDs")
        local_id = str(candidate.client_generated_id)
        if provenance != expected_provenance.get(local_id) and candidate.node.kind != "constraint":
            raise ValueError("generated node provenance does not match validated checkpoints")
        if candidate.node.kind == "constraint":
            continue
        required_parent_kinds = {
            "strategy": {"goal"},
            "source": {"strategy"},
            "claim": {"source"},
            "opportunity": {"claim"},
            "assumption": {"opportunity"},
            "risk": {"opportunity"},
            "validation_experiment": {"opportunity"},
        }[candidate.node.kind]
        if not any(node_kinds.get(parent_id) in required_parent_kinds for parent_id in provenance):
            raise ValueError("generated node provenance does not preserve semantic lineage")
        if not all(
            _has_lineage_edge(
                node_kind=candidate.node.kind,
                local_id=local_id,
                provenance_id=parent_id,
                edges=added_edges,
            )
            for parent_id in provenance
        ):
            raise ValueError("generated node provenance requires a directed semantic lineage edge")

    branch_constraints = [node for node in snapshot["nodes"] if isinstance(node, dict)]
    for candidate in output.operations:
        if candidate.op != "DELETE_NODE" or candidate.node_id is None:
            continue
        expected_incident = sorted(
            str(edge["id"])
            for edge in snapshot["edges"]
            if isinstance(edge, dict)
            and edge.get("id")
            and candidate.node_id
            in {str(edge.get("source_node_id")), str(edge.get("target_node_id"))}
        )
        expected_constraints = sorted(
            str(node["id"])
            for node in branch_constraints
            if node.get("id")
            and node.get("kind") == "constraint"
            and str(node.get("branch_root_node_id")) == candidate.node_id
        )
        if list(candidate.required_incident_edge_ids) != expected_incident:
            raise ValueError("DELETE_NODE does not declare every frozen incident edge")
        if list(candidate.required_branch_constraint_ids) != expected_constraints:
            raise ValueError("DELETE_NODE does not declare every frozen branch constraint")


def validate_contextual_stage_output(
    stage_name: str,
    output: PipelineModel,
    *,
    stage_input: dict[str, Any],
) -> None:
    if stage_name == "planning":
        assert isinstance(output, PlanningOutput)
        _validate_planning_output(output, stage_input)
    elif stage_name == "researching":
        assert isinstance(output, ResearchOutput)
        _validate_research_output(output, stage_input)
    elif stage_name == "extracting":
        assert isinstance(output, ExtractionOutput)
        _validate_extraction_output(output, stage_input)
    elif stage_name == "synthesizing":
        assert isinstance(output, SynthesisOutput)
        _validate_synthesis_output(output, stage_input)
    elif stage_name == "critiquing":
        assert isinstance(output, CritiqueOutput)
        _validate_critique_output(output, stage_input)
    elif stage_name == "constructing_patch":
        assert isinstance(output, GraphPatchOutput)
        _validate_patch_output(output, stage_input)


class PipelineStageOutputValidator:
    def validate(
        self,
        stage_name: str,
        result: StageResultEnvelope,
        *,
        stage_input: dict[str, Any],
    ) -> StageResultEnvelope:
        if result.stage_name != stage_name:
            raise ValueError("stage result identity does not match the active stage")
        model = STAGE_OUTPUT_MODELS.get(stage_name)
        if model is None:
            raise ValueError(f"unsupported pipeline stage: {stage_name}")
        # Provider payloads cross a JSON boundary. Validate with strict JSON semantics so
        # arrays are accepted for immutable tuples without allowing Python-side coercions.
        validated = model.model_validate_json(json.dumps(result.output))
        validate_contextual_stage_output(stage_name, validated, stage_input=stage_input)
        return result.model_copy(update={"output": validated.model_dump(mode="json")})


__all__ = [
    "ClaimRecord",
    "ClusteringOutput",
    "CritiqueOutput",
    "EvidenceCluster",
    "ExtractionOutput",
    "GraphPatchOutput",
    "OpportunityOutput",
    "PipelineStageOutputValidator",
    "PlanningOutput",
    "ResearchOutput",
    "SourceRecord",
    "SynthesisOutput",
]
