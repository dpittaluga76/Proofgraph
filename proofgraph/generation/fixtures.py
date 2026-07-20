from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from proofgraph.generation.context import canonical_json
from proofgraph.generation.pipeline_schemas import (
    STAGE_OUTPUT_MODELS,
    parallel_constraint_clone_specs,
)
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.retention import validate_progress_payload, validate_retained_payload
from proofgraph.generation.schemas import ProgressEventEnvelope, StageResultEnvelope

Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FixtureStage = Literal[
    "planning",
    "researching",
    "extracting",
    "synthesizing",
    "critiquing",
    "constructing_patch",
]

REQUIRED_FIXTURE_FILES = (
    "manifest.json",
    "sources.json",
    "claims.json",
    "planning-outputs.json",
    "synthesis-outputs.json",
    "critique-outputs.json",
    "patch-construction-outputs.json",
    "progress-events.json",
    "semantic-input-hashes.json",
)


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class FixtureCase(FixtureModel):
    case_id: str = Field(min_length=1)
    operation: Literal[
        "generate_strategies",
        "research_evidence",
        "synthesize_opportunities",
        "regenerate_stale",
    ]
    stage: FixtureStage
    regeneration_phase: str | None = None
    target_kinds: tuple[str, ...] = ()
    pipeline_version: str
    provider_identity: str
    semantic_input_key: str = Field(min_length=1)
    semantic_input_hash: Hash
    output_key: str = Field(min_length=1)
    progress_key: str | None = None

    @model_validator(mode="after")
    def validate_canonical_target_kinds(self) -> FixtureCase:
        if tuple(sorted(set(self.target_kinds))) != self.target_kinds:
            raise ValueError("fixture target_kinds must be sorted and deduplicated")
        return self


class FixtureManifest(FixtureModel):
    scenario_id: Literal["security_questionnaires_v1"]
    fixture_version: Literal["1"]
    pipeline_version: Literal["intelligence_pipeline_v1"]
    prompt_version: Literal["opportunity_pipeline_prompts_v1"]
    strategy_version: Literal["opportunity_strategies_v1"]
    content_policy: Literal["synthetic_or_redistributable_derived_evidence_only"]
    patch_builder_version: Literal["checkpoint_patch_builder_v1"]
    patch_builder_hash: Hash
    document_hashes: dict[str, Hash]
    semantic_contexts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    semantic_inputs: dict[str, dict[str, Any]]
    cases: tuple[FixtureCase, ...] = Field(min_length=1)
    coverage: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest_uniqueness(self) -> FixtureManifest:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("fixture case IDs must be unique")
        semantic_commitments: dict[str, str] = {}
        for case in self.cases:
            committed_hash = semantic_commitments.setdefault(
                case.semantic_input_key, case.semantic_input_hash
            )
            if committed_hash != case.semantic_input_hash:
                raise ValueError("fixture cases sharing a semantic input key must share one hash")
            if case.semantic_input_key not in self.semantic_inputs:
                continue
            semantic_input = self.expanded_semantic_input(case.semantic_input_key)
            if case.semantic_input_hash != fixture_semantic_hash(semantic_input):
                raise ValueError(f"fixture case {case.case_id} has a stale semantic input hash")
        if tuple(sorted(set(self.coverage))) != self.coverage:
            raise ValueError("fixture coverage entries must be sorted and deduplicated")
        expected_documents = set(REQUIRED_FIXTURE_FILES) - {"manifest.json"}
        if set(self.document_hashes) != expected_documents:
            raise ValueError("fixture document hashes must cover every immutable bundle asset")
        return self

    def expanded_semantic_input(self, key: str) -> dict[str, Any]:
        value = self.semantic_inputs[key]
        context_key = value.get("$context")
        if context_key is None:
            return value
        if not isinstance(context_key, str) or context_key not in self.semantic_contexts:
            raise ValueError(f"fixture semantic input {key} has an unknown context")
        return {
            **self.semantic_contexts[context_key],
            **{k: v for k, v in value.items() if k != "$context"},
        }


def fixture_semantic_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


_FIXTURE_RUNTIME_METADATA_KEYS = {
    "approach",
    "generated_by_run_id",
    "provenance_node_ids",
    "rationale",
    "retrieved_at",
    "sanitized_excerpt",
    "source_kind",
    "source_patch_id",
    "url",
}
_FIXTURE_ROLE_BY_KIND_AND_TITLE = {
    (
        "claim",
        "Answer gathering and approval handoffs can delay enterprise deals.",
    ): "claim_deal_delay",
    (
        "claim",
        "Security questionnaire response work repeats across enterprise sales cycles.",
    ): "claim_repeated_labor",
    (
        "claim",
        "Teams coordinate questionnaire work through fragile spreadsheet and document handoffs.",
    ): "claim_workaround_pain",
    (
        "claim",
        "Teams may reject automation that removes human security review.",
    ): "claim_trust_burden",
    (
        "source",
        "Synthetic security questionnaire workflow benchmark",
    ): "source_benchmark",
    ("source", "Synthetic buyer interview summary"): "source_interviews",
    (
        "strategy",
        "Productize the recurring questionnaire workflow",
    ): "strategy",
}


def _role_maps(stage_input: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    snapshot = stage_input.get("context_snapshot") or {}
    node_roles: dict[str, str] = {}
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        metadata = node.get("metadata")
        role = metadata.get("fixture_role") if isinstance(metadata, dict) else None
        inferred_role = _FIXTURE_ROLE_BY_KIND_AND_TITLE.get(
            (str(node.get("kind")), str(node.get("title")))
        )
        node_roles[str(node["id"])] = str(role or inferred_role or node["id"])
    edge_roles: dict[str, str] = {}
    for edge in snapshot.get("edges") or []:
        if not isinstance(edge, dict) or not edge.get("id"):
            continue
        metadata = edge.get("metadata")
        role = metadata.get("fixture_role") if isinstance(metadata, dict) else None
        source_role = node_roles.get(str(edge.get("source_node_id")))
        target_role = node_roles.get(str(edge.get("target_node_id")))
        inferred_role = f"{source_role}_{target_role}" if source_role and target_role else None
        edge_roles[str(edge["id"])] = str(role or inferred_role or edge["id"])
    return node_roles, edge_roles


def _semantic_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    generated_by_run_id = value.get("generated_by_run_id")
    applied_fixture_patch = bool(value.get("source_patch_id")) or (
        isinstance(generated_by_run_id, str) and generated_by_run_id != "fixture"
    )
    semantic = {
        key: child
        for key, child in sorted(value.items())
        if key != "fixture_role"
        and (not applied_fixture_patch or key not in _FIXTURE_RUNTIME_METADATA_KEYS)
    }
    authority = semantic.get("authority")
    if applied_fixture_patch and isinstance(authority, dict):
        semantic["authority"] = {
            key: authority[key] for key in ("authoritative", "hierarchy_rank") if key in authority
        }
    return semantic


def _normalize_fixture_ids(
    value: Any,
    node_roles: dict[str, str],
    edge_roles: dict[str, str],
) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_fixture_ids(child, node_roles, edge_roles)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_normalize_fixture_ids(child, node_roles, edge_roles) for child in value]
    if isinstance(value, str):
        return node_roles.get(value, edge_roles.get(value, value))
    return value


def _fixture_prior_output(value: Any) -> Any:
    """Remove redundant audit fields that cannot change downstream semantics."""
    normalized = deepcopy(value)
    if isinstance(normalized, dict):
        normalized.pop("candidate_claim_ids", None)
    return normalized


def fixture_semantic_input(stage_name: str, stage_input: dict[str, Any]) -> dict[str, Any]:
    snapshot = stage_input.get("context_snapshot") or {}
    manifest = stage_input.get("context_manifest") or {}
    request = manifest.get("request") or {}
    applied_fixture_patch = any(
        isinstance(node, dict)
        and isinstance(node.get("metadata"), dict)
        and node["metadata"].get("source_patch_id")
        for node in snapshot.get("nodes") or []
    )
    node_roles, edge_roles = _role_maps(stage_input)
    nodes = []
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        role = node_roles[str(node["id"])]
        semantic_node = {
            "role": role,
            "kind": node.get("kind"),
            "title": node.get("title"),
            "metadata": _normalize_fixture_ids(
                _semantic_metadata(node.get("metadata")), node_roles, edge_roles
            ),
            "branch_root_role": node_roles.get(str(node.get("branch_root_node_id"))),
            "stale": node.get("stale"),
            "version": node.get("version"),
        }
        if node.get("body") is not None:
            semantic_node["body"] = node["body"]
        if node.get("sanitized_excerpt") is not None:
            semantic_node["sanitized_excerpt"] = node["sanitized_excerpt"]
        nodes.append(semantic_node)
    edges = []
    for edge in snapshot.get("edges") or []:
        if not isinstance(edge, dict) or not edge.get("id"):
            continue
        edges.append(
            {
                "role": edge_roles[str(edge["id"])],
                "kind": edge.get("kind"),
                "source_role": node_roles.get(str(edge.get("source_node_id"))),
                "target_role": node_roles.get(str(edge.get("target_node_id"))),
                "metadata": _normalize_fixture_ids(
                    _semantic_metadata(edge.get("metadata")), node_roles, edge_roles
                ),
                "version": edge.get("version"),
            }
        )
    explicit_roles = sorted(
        node_roles.get(str(node_id), str(node_id))
        for node_id in manifest.get("explicit_node_ids") or []
    )
    targets = []
    for target in stage_input.get("target_workset") or []:
        if not isinstance(target, dict):
            continue
        targets.append(
            {
                "role": node_roles.get(str(target.get("node_id")), str(target.get("node_id"))),
                "kind": target.get("kind"),
                "version": target.get("version"),
                "distance": target.get("distance"),
                "branch_anchor_role": node_roles.get(str(target.get("branch_anchor_id"))),
            }
        )
    prior_hashes = {
        key: fixture_semantic_hash(
            _normalize_fixture_ids(
                _fixture_prior_output(value.get("output") or {}), node_roles, edge_roles
            )
        )
        for key, value in sorted((stage_input.get("prior_stage_outputs") or {}).items())
        if isinstance(value, dict)
    }
    return {
        "operation": request.get("operation"),
        "stage": stage_name,
        "regeneration_phase": stage_input.get("regeneration_phase"),
        "instruction": request.get("instruction"),
        "regeneration_scope": request.get("regeneration_scope"),
        # Forward replay stages are matched by semantic graph state. Demo reset and
        # accepted patches legitimately advance this audit marker without changing that
        # state. Existing regeneration recordings retain their frozen revision context.
        "base_canvas_revision": (
            0
            if request.get("operation") != "regenerate_stale" or applied_fixture_patch
            else stage_input.get("base_canvas_revision")
        ),
        "explicit_roles": explicit_roles,
        "nodes": sorted(nodes, key=lambda value: value["role"]),
        "edges": sorted(edges, key=lambda value: value["role"]),
        "targets": sorted(targets, key=lambda value: (value["kind"], value["role"])),
        "prior_output_hashes": prior_hashes,
    }


class FixtureBundle:
    def __init__(
        self,
        root: Path,
        manifest: FixtureManifest,
        documents: dict[str, Any],
    ) -> None:
        self.root = root
        self.manifest = manifest
        self.documents = documents

    @classmethod
    def load(cls, root: Path) -> FixtureBundle:
        missing = [name for name in REQUIRED_FIXTURE_FILES if not (root / name).is_file()]
        if missing:
            raise ValueError(f"fixture bundle is incomplete: {missing}")
        documents = {
            name: json.loads((root / name).read_text(encoding="utf-8"))
            for name in REQUIRED_FIXTURE_FILES
        }
        manifest = FixtureManifest.model_validate_json(json.dumps(documents["manifest.json"]))
        commitment_document = documents["semantic-input-hashes.json"]
        commitments = (
            commitment_document.get("semantic_input_hashes")
            if isinstance(commitment_document, dict)
            else None
        )
        expected_commitments = {
            case.semantic_input_key: case.semantic_input_hash for case in manifest.cases
        }
        if commitments != expected_commitments:
            raise ValueError("fixture semantic input commitments do not cover every case")
        actual_document_hashes = {
            name: fixture_semantic_hash(document)
            for name, document in documents.items()
            if name != "manifest.json"
        }
        if actual_document_hashes != manifest.document_hashes:
            raise ValueError("fixture bundle document hash mismatch")
        builder_source = "\n".join(
            inspect.getsource(builder).replace("\r\n", "\n")
            for builder in (_prior_fixture_outputs, _build_fixture_patch)
        )
        actual_builder_hash = hashlib.sha256(builder_source.encode("utf-8")).hexdigest()
        if actual_builder_hash != manifest.patch_builder_hash:
            raise ValueError("fixture patch builder hash mismatch")
        for name, document in documents.items():
            validate_retained_payload(document, path=f"fixture.{name}")
        return cls(root, manifest, documents)

    def output_template(self, case: FixtureCase) -> dict[str, Any]:
        document_name = {
            "planning": "planning-outputs.json",
            "researching": "sources.json",
            "extracting": "claims.json",
            "synthesizing": "synthesis-outputs.json",
            "critiquing": "critique-outputs.json",
            "constructing_patch": "patch-construction-outputs.json",
        }[case.stage]
        document = self.documents[document_name]
        outputs = document.get("outputs") if isinstance(document, dict) else None
        value = outputs.get(case.output_key) if isinstance(outputs, dict) else None
        if not isinstance(value, dict):
            raise ValueError(f"fixture output {case.output_key} is missing from {document_name}")
        if "$extends" not in value:
            return value
        base_key = value.get("$extends")
        base = outputs.get(base_key) if isinstance(base_key, str) else None
        if not isinstance(base, dict) or "$extends" in base:
            raise ValueError(f"fixture output {case.output_key} has an invalid base template")
        resolved = deepcopy(base)
        overrides = value.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError(f"fixture output {case.output_key} has invalid overrides")
        resolved.update(deepcopy(overrides))
        take = value.get("take_opportunities")
        if take is not None:
            if (
                not isinstance(take, int)
                or take < 1
                or not isinstance(resolved.get("opportunities"), list)
            ):
                raise ValueError(
                    f"fixture output {case.output_key} has an invalid opportunity slice"
                )
            resolved["opportunities"] = resolved["opportunities"][:take]
        target_roles = value.get("target_roles")
        if target_roles is not None:
            opportunities = resolved.get("opportunities")
            if (
                not isinstance(target_roles, list)
                or not isinstance(opportunities, list)
                or len(target_roles) != len(opportunities)
            ):
                raise ValueError(f"fixture output {case.output_key} has invalid target roles")
            for opportunity, role in zip(opportunities, target_roles, strict=True):
                opportunity["target_node_id"] = f"$node:{role}"
        opportunity_overrides = value.get("opportunity_overrides")
        if opportunity_overrides is not None:
            opportunities = resolved.get("opportunities")
            if (
                not isinstance(opportunity_overrides, list)
                or not isinstance(opportunities, list)
                or len(opportunity_overrides) != len(opportunities)
                or not all(isinstance(item, dict) for item in opportunity_overrides)
            ):
                raise ValueError(
                    f"fixture output {case.output_key} has invalid opportunity overrides"
                )
            for opportunity, override in zip(opportunities, opportunity_overrides, strict=True):
                opportunity.update(deepcopy(override))
        take_critiques = value.get("take_critiques")
        if take_critiques is not None:
            critiques = resolved.get("critiques")
            if (
                not isinstance(take_critiques, int)
                or take_critiques < 1
                or not isinstance(critiques, list)
            ):
                raise ValueError(f"fixture output {case.output_key} has an invalid critique slice")
            resolved["critiques"] = critiques[:take_critiques]
        return resolved

    def progress_templates(self, case: FixtureCase) -> list[dict[str, Any]]:
        if case.progress_key is None:
            return []
        document = self.documents["progress-events.json"]
        values = (
            document.get("events", {}).get(case.progress_key)
            if isinstance(document, dict)
            else None
        )
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise ValueError(f"fixture progress events {case.progress_key} are missing")
        return values


def _replace_fixture_value(
    value: Any,
    *,
    stage_input: dict[str, Any],
    node_ids: dict[str, str],
    edge_ids: dict[str, str],
    node_versions: dict[str, int],
    edge_versions: dict[str, int],
) -> Any:
    if isinstance(value, list):
        return [
            _replace_fixture_value(
                child,
                stage_input=stage_input,
                node_ids=node_ids,
                edge_ids=edge_ids,
                node_versions=node_versions,
                edge_versions=edge_versions,
            )
            for child in value
        ]
    if isinstance(value, dict):
        if value == {"$fixture": "known_node_ids"}:
            return sorted(node_ids.values())
        if value == {"$fixture": "known_edge_ids"}:
            return sorted(edge_ids.values())
        if value == {"$fixture": "target_node_ids"}:
            return sorted(
                str(target["node_id"])
                for target in stage_input.get("target_workset") or []
                if isinstance(target, dict) and target.get("node_id")
            )
        if value.get("$fixture") == "node_ids" and isinstance(value.get("roles"), list):
            roles = value["roles"]
            if not all(isinstance(role, str) and role in node_ids for role in roles):
                raise ValueError("fixture node_ids directive references an unknown role")
            return sorted({node_ids[role] for role in roles})
        return {
            key: _replace_fixture_value(
                child,
                stage_input=stage_input,
                node_ids=node_ids,
                edge_ids=edge_ids,
                node_versions=node_versions,
                edge_versions=edge_versions,
            )
            for key, child in value.items()
        }
    if not isinstance(value, str):
        return value
    if value == "$run_id":
        return str(stage_input.get("run_id"))
    if value == "$base_canvas_revision":
        return stage_input.get("base_canvas_revision")
    for prefix, mapping in (
        ("$node:", node_ids),
        ("$edge:", edge_ids),
        ("$node_version:", node_versions),
        ("$edge_version:", edge_versions),
    ):
        if value.startswith(prefix):
            role = value.removeprefix(prefix)
            if role not in mapping:
                raise ValueError(f"fixture placeholder references unknown role: {role}")
            return mapping[role]
    return value


def materialize_fixture(value: Any, stage_input: dict[str, Any]) -> Any:
    snapshot = stage_input.get("context_snapshot") or {}
    node_roles, edge_roles = _role_maps(stage_input)
    node_ids = {role: node_id for node_id, role in node_roles.items()}
    edge_ids = {role: edge_id for edge_id, role in edge_roles.items()}
    node_versions = {
        node_roles[str(node["id"])]: int(node["version"])
        for node in snapshot.get("nodes") or []
        if isinstance(node, dict) and node.get("id") and isinstance(node.get("version"), int)
    }
    edge_versions = {
        edge_roles[str(edge["id"])]: int(edge["version"])
        for edge in snapshot.get("edges") or []
        if isinstance(edge, dict) and edge.get("id") and isinstance(edge.get("version"), int)
    }
    return _replace_fixture_value(
        value,
        stage_input=stage_input,
        node_ids=node_ids,
        edge_ids=edge_ids,
        node_versions=node_versions,
        edge_versions=edge_versions,
    )


def _prior_fixture_outputs(stage_input: dict[str, Any], stage_name: str) -> list[dict[str, Any]]:
    prior = stage_input.get("prior_stage_outputs")
    if not isinstance(prior, dict):
        return []
    outputs: list[dict[str, Any]] = []
    for key in sorted(prior):
        value = prior[key]
        if not (key == stage_name or key.endswith(f":{stage_name}")):
            continue
        if not isinstance(value, dict) or not isinstance(value.get("output"), dict):
            raise ValueError(f"fixture {stage_name} checkpoint is malformed")
        outputs.append(value["output"])
    return outputs


def _build_fixture_patch(stage_input: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic fixture patch from already validated checkpoints.

    The fixture adapter deliberately derives candidate graph operations from the exact
    planning, extraction, and synthesis envelopes in the run. Production providers still
    return their own patch payloads, which the shared validator binds to the same envelopes.
    """

    snapshot = stage_input.get("context_snapshot") or {}
    snapshot_nodes = [node for node in snapshot.get("nodes") or [] if isinstance(node, dict)]
    snapshot_edges = [edge for edge in snapshot.get("edges") or [] if isinstance(edge, dict)]
    known_node_ids = sorted(str(node["id"]) for node in snapshot_nodes if node.get("id"))
    known_edge_ids = sorted(str(edge["id"]) for edge in snapshot_edges if edge.get("id"))
    known_node_kinds = {
        str(node["id"]): str(node.get("kind")) for node in snapshot_nodes if node.get("id")
    }
    run_id = str(stage_input.get("run_id"))
    manifest = stage_input.get("context_manifest") or {}
    request = manifest.get("request") if isinstance(manifest, dict) else {}
    operation = request.get("operation") if isinstance(request, dict) else None
    target_workset = [
        target for target in stage_input.get("target_workset") or [] if isinstance(target, dict)
    ]
    regeneration_scope = request.get("regeneration_scope") if isinstance(request, dict) else None

    operations: list[dict[str, Any]] = []
    local_node_kinds: dict[str, str] = {}
    creator_by_id: dict[str, str] = {}
    edge_keys: set[tuple[str, str, str]] = set()
    successor_by_target: dict[str, str] = {}
    lineage_operation_by_successor: dict[str, str] = {}

    def add_node(
        local_id: str,
        *,
        kind: str,
        title: str,
        body: str | None,
        metadata: dict[str, Any],
        branch_root_node_id: str | None = None,
        additional_dependencies: tuple[str, ...] = (),
    ) -> None:
        operation_id = f"add_{local_id}"
        provenance = metadata.get("provenance_node_ids") or []
        dependencies = sorted(
            {
                *(
                    creator_by_id[parent_id]
                    for parent_id in provenance
                    if parent_id in creator_by_id
                ),
                *(
                    [creator_by_id[branch_root_node_id]]
                    if branch_root_node_id in creator_by_id
                    else []
                ),
                *additional_dependencies,
            }
        )
        node_payload = {
            "kind": kind,
            "title": title,
            "body": body,
            "metadata": metadata,
        }
        if branch_root_node_id is not None:
            node_payload["branch_root_node_id"] = branch_root_node_id
        operations.append(
            {
                "operation_id": operation_id,
                "op": "ADD_NODE",
                "depends_on": dependencies,
                "client_generated_id": local_id,
                "node": node_payload,
            }
        )
        creator_by_id[local_id] = operation_id
        local_node_kinds[local_id] = kind

    def add_edge(source_id: str, target_id: str, kind: str) -> str:
        key = (source_id, target_id, kind)
        if key in edge_keys:
            return next(
                operation["operation_id"]
                for operation in operations
                if operation.get("op") == "ADD_EDGE"
                and operation.get("edge", {}).get("source_node_id") == source_id
                and operation.get("edge", {}).get("target_node_id") == target_id
                and operation.get("edge", {}).get("kind") == kind
            )
        edge_keys.add(key)
        dependencies = sorted(
            {
                creator_by_id[node_id]
                for node_id in (source_id, target_id)
                if node_id in creator_by_id
            }
        )
        index = len(edge_keys)
        operations.append(
            {
                "operation_id": f"add_edge_{index}",
                "op": "ADD_EDGE",
                "depends_on": dependencies,
                "client_generated_id": f"generated_edge_{index}",
                "edge": {
                    "source_node_id": source_id,
                    "target_node_id": target_id,
                    "kind": kind,
                    "metadata": {"generated_by_run_id": run_id},
                },
            }
        )
        return f"add_edge_{index}"

    planning_outputs = _prior_fixture_outputs(stage_input, "planning")
    strategies = [
        candidate
        for planning in planning_outputs
        for candidate in planning.get("strategies") or []
        if isinstance(candidate, dict)
    ]
    strategy_replacements = {
        str(candidate["target_node_id"]): str(candidate["id"])
        for candidate in strategies
        if candidate.get("target_node_id")
    }
    explicit_ids = set((stage_input.get("context_manifest") or {}).get("explicit_node_ids") or [])
    goal_ids = sorted(
        node_id
        for node_id, kind in known_node_kinds.items()
        if kind == "goal" and (not explicit_ids or node_id in explicit_ids)
    )
    for candidate in strategies:
        candidate_id = str(candidate["id"])
        target_id = candidate.get("target_node_id")
        parent_goal_ids = sorted(
            str(edge.get("source_node_id"))
            for edge in snapshot_edges
            if target_id is not None
            and str(edge.get("target_node_id")) == str(target_id)
            and known_node_kinds.get(str(edge.get("source_node_id"))) == "goal"
        )
        provenance = parent_goal_ids or goal_ids
        metadata: dict[str, Any] = {
            "generated_by_run_id": run_id,
            "provenance_node_ids": provenance,
            "approach": candidate["approach"],
            "rationale": candidate["rationale"],
            "strategy_template_id": candidate["template_id"],
        }
        if target_id is not None:
            metadata.update(
                {
                    "regenerated_from_node_id": str(target_id),
                    "regeneration_scope": regeneration_scope,
                    "lineage_mode": "parallel",
                }
            )
        add_node(
            candidate_id,
            kind="strategy",
            title=str(candidate["title"]),
            body=str(candidate["approach"]),
            metadata=metadata,
        )
        for goal_id in provenance:
            add_edge(goal_id, candidate_id, "evolves_into")
        if target_id is not None:
            target_id = str(target_id)
            successor_by_target[target_id] = candidate_id
            lineage_operation_by_successor[candidate_id] = add_edge(
                target_id,
                candidate_id,
                "evolves_into",
            )

    extraction_outputs = _prior_fixture_outputs(stage_input, "extracting")
    sources = {
        str(source["id"]): source
        for extraction in extraction_outputs
        for source in extraction.get("sources") or []
        if isinstance(source, dict)
    }
    claims = {
        str(claim["id"]): claim
        for extraction in extraction_outputs
        for claim in extraction.get("claims") or []
        if isinstance(claim, dict)
    }
    research_strategy_ids = [
        str(plan["selected_strategy_id"])
        for planning in planning_outputs
        for plan in planning.get("research_plans") or []
        if isinstance(plan, dict) and plan.get("selected_strategy_id")
    ]
    source_parent_ids = sorted(
        {
            strategy_replacements.get(strategy_id, strategy_id)
            for strategy_id in research_strategy_ids
        }
    )
    claim_targets = sorted(
        str(target["node_id"])
        for target in target_workset
        if target.get("kind") == "claim" and target.get("node_id")
    )
    selected_claim_ids: list[str]
    if operation == "research_evidence":
        selected_claim_ids = sorted(claims)
    elif claim_targets:
        preferred = (
            "claim_workaround_pain"
            if len(target_workset) > 1 and "claim_workaround_pain" in claims
            else "claim_repeated_labor"
        )
        if preferred not in claims:
            preferred = sorted(claims)[0]
        selected_claim_ids = [preferred]
    else:
        selected_claim_ids = []
    required_source_ids = sorted(
        {
            str(source_id)
            for claim_id in selected_claim_ids
            for source_id in claims[claim_id].get("source_ids") or []
        }
    )
    if operation == "research_evidence":
        required_source_ids = sorted(sources)
    for source_id in required_source_ids:
        source = sources[source_id]
        metadata = {
            "generated_by_run_id": run_id,
            "provenance_node_ids": source_parent_ids,
            "authority": source["authority"],
            "content_hash": source["content_hash"],
            "independence_key": source["independence_key"],
            "retrieved_at": source["retrieved_at"],
            "sanitized_excerpt": source["sanitized_excerpt"],
            "source_kind": source["kind"],
        }
        if source.get("url") is not None:
            metadata["url"] = source["url"]
        add_node(
            source_id,
            kind="source",
            title=str(source["title"]),
            body=str(source["sanitized_excerpt"]),
            metadata=metadata,
        )
        for strategy_id in source_parent_ids:
            add_edge(strategy_id, source_id, "derived_from")

    for index, claim_id in enumerate(selected_claim_ids):
        claim = claims[claim_id]
        source_ids = sorted(str(source_id) for source_id in claim["source_ids"])
        metadata = {
            "generated_by_run_id": run_id,
            "provenance_node_ids": source_ids,
            "classification": claim["classification"],
            "evidence_type": claim["evidence_type"],
            "independence_keys": sorted(
                {str(sources[source_id]["independence_key"]) for source_id in source_ids}
            ),
            "limitations": claim["limitations"],
            "mechanism_tags": claim["mechanism_tags"],
            "review_status": "provisional",
            "source_ids": source_ids,
            "strength": claim["strength"],
            "topic_keys": claim["topic_keys"],
        }
        if claim.get("contradiction_target_key") is not None:
            metadata["contradiction_target_key"] = claim["contradiction_target_key"]
        target_claim_id: str | None = None
        if claim_targets:
            target_claim_id = claim_targets[index]
            metadata.update(
                {
                    "regenerated_from_node_id": target_claim_id,
                    "regeneration_scope": regeneration_scope,
                    "lineage_mode": "parallel",
                }
            )
        add_node(
            claim_id,
            kind="claim",
            title=str(claim["claim"]),
            body=str(claim["claim"]),
            metadata=metadata,
        )
        for source_id in source_ids:
            add_edge(claim_id, source_id, "extracted_from")
        if target_claim_id is not None:
            successor_by_target[target_claim_id] = claim_id
            lineage_operation_by_successor[claim_id] = add_edge(
                target_claim_id,
                claim_id,
                "evolves_into",
            )

    synthesis_outputs = _prior_fixture_outputs(stage_input, "synthesizing")
    opportunities = [
        opportunity
        for synthesis in synthesis_outputs
        for opportunity in synthesis.get("opportunities") or []
        if isinstance(opportunity, dict)
    ]
    for opportunity in opportunities:
        opportunity_id = str(opportunity["id"])
        provenance = sorted(
            {
                *(
                    str(evidence["claim_id"])
                    for evidence in opportunity.get("evidence") or []
                    if isinstance(evidence, dict)
                ),
                *(
                    [str(opportunity["contradiction"]["claim_id"])]
                    if opportunity.get("contradiction", {}).get("claim_id") is not None
                    else []
                ),
            }
        )
        metadata = {
            key: deepcopy(value)
            for key, value in opportunity.items()
            if key not in {"id", "target_node_id", "title"}
        }
        metadata.update(
            {
                "generated_by_run_id": run_id,
                "provenance_node_ids": provenance,
            }
        )
        if opportunity.get("target_node_id") is not None:
            metadata.update(
                {
                    "regenerated_from_node_id": str(opportunity["target_node_id"]),
                    "regeneration_scope": regeneration_scope,
                    "lineage_mode": "parallel",
                }
            )
        add_node(
            opportunity_id,
            kind="opportunity",
            title=str(opportunity["title"]),
            body=str(opportunity["mechanism"]),
            metadata=metadata,
        )
        for evidence in opportunity.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            edge_kind = "contradicts" if evidence["signal_type"] == "contradiction" else "supports"
            add_edge(str(evidence["claim_id"]), opportunity_id, edge_kind)
        contradiction_id = opportunity.get("contradiction", {}).get("claim_id")
        if contradiction_id is not None:
            add_edge(str(contradiction_id), opportunity_id, "contradicts")
        if opportunity.get("target_node_id") is not None:
            target_opportunity_id = str(opportunity["target_node_id"])
            successor_by_target[target_opportunity_id] = opportunity_id
            lineage_operation_by_successor[opportunity_id] = add_edge(
                target_opportunity_id,
                opportunity_id,
                "evolves_into",
            )

        for assumption in opportunity.get("assumptions") or []:
            assumption_id = str(assumption["id"])
            add_node(
                assumption_id,
                kind="assumption",
                title=str(assumption["statement"]),
                body=str(assumption["statement"]),
                metadata={
                    "generated_by_run_id": run_id,
                    "provenance_node_ids": [opportunity_id],
                    "importance": assumption["importance"],
                },
            )
            add_edge(opportunity_id, assumption_id, "derived_from")
        for risk in opportunity.get("risks") or []:
            risk_id = str(risk["id"])
            add_node(
                risk_id,
                kind="risk",
                title=str(risk["statement"]),
                body=str(risk["statement"]),
                metadata={
                    "generated_by_run_id": run_id,
                    "provenance_node_ids": [opportunity_id],
                    "impact": risk["impact"],
                    "mitigation": risk["mitigation"],
                },
            )
            add_edge(opportunity_id, risk_id, "derived_from")
        experiment = opportunity["validation_experiment"]
        experiment_id = str(experiment["id"])
        add_node(
            experiment_id,
            kind="validation_experiment",
            title=str(experiment["hypothesis"]),
            body=str(experiment["method"]),
            metadata={
                "generated_by_run_id": run_id,
                "provenance_node_ids": [opportunity_id],
                **{key: value for key, value in experiment.items() if key != "id"},
            },
        )
        add_edge(opportunity_id, experiment_id, "requires_validation")

    if operation == "regenerate_stale":
        copyable_constraint_fields = {
            "category",
            "context_scope",
            "description",
            "notes",
            "pinned",
            "summary",
            "tags",
        }
        for spec in parallel_constraint_clone_specs(stage_input, successor_by_target):
            constraint = spec["constraint"]
            constraint_id = str(spec["constraint_id"])
            successor_id = str(spec["successor_id"])
            original_metadata = constraint.get("metadata") or {}
            clone_metadata = {
                field: deepcopy(original_metadata[field])
                for field in sorted(copyable_constraint_fields)
                if field in original_metadata
            }
            clone_metadata.update(
                {
                    "generated_by_run_id": run_id,
                    "provenance_node_ids": [constraint_id],
                    "review_status": "provisional",
                }
            )
            clone_id = f"{constraint_id}.parallel_for.{successor_id}"
            add_node(
                clone_id,
                kind="constraint",
                title=str(constraint["title"]),
                body=constraint.get("body"),
                metadata=clone_metadata,
                branch_root_node_id=successor_id,
                additional_dependencies=(lineage_operation_by_successor[successor_id],),
            )

    regeneration_target_ids = sorted(
        str(target["node_id"]) for target in target_workset if target.get("node_id")
    )
    permitted_stale_resolution_ids = sorted(
        {
            str(node_id)
            for target in target_workset
            for node_id in target.get("stale_node_ids") or [target.get("node_id")]
            if node_id
        }
    )
    return {
        "base_canvas_revision": stage_input.get("base_canvas_revision"),
        "known_node_ids": known_node_ids,
        "known_edge_ids": known_edge_ids,
        "operations": operations,
        "regeneration_target_ids": (
            regeneration_target_ids if operation == "regenerate_stale" else []
        ),
        "permitted_stale_resolution_ids": (
            permitted_stale_resolution_ids if operation == "regenerate_stale" else []
        ),
    }


class StrictFixtureProviders:
    def __init__(self, bundle: FixtureBundle) -> None:
        self.bundle = bundle

    def _case(self, stage_name: FixtureStage, request: ProviderStageRequest) -> FixtureCase:
        configuration = request.configuration
        bundle_identity = {
            "fixture_bundle_id": self.bundle.manifest.scenario_id,
            "fixture_version": self.bundle.manifest.fixture_version,
            "pipeline_version": self.bundle.manifest.pipeline_version,
            "prompt_version": self.bundle.manifest.prompt_version,
            "strategy_version": self.bundle.manifest.strategy_version,
        }
        requested_identity = {
            "fixture_bundle_id": configuration.fixture_bundle_id,
            "fixture_version": configuration.fixture_version,
            "pipeline_version": configuration.pipeline_version,
            "prompt_version": configuration.prompt_version,
            "strategy_version": configuration.strategy_version,
        }
        if requested_identity != bundle_identity:
            raise ProviderExecutionError(
                "fixture_input_mismatch",
                "The frozen execution profile does not match the loaded fixture bundle.",
                retryable=True,
                details={"expected": bundle_identity, "actual": requested_identity},
            )
        manifest = request.stage_input.get("context_manifest") or {}
        operation = (manifest.get("request") or {}).get("operation")
        phase = request.stage_input.get("regeneration_phase")
        target_kinds = tuple(
            sorted(
                {
                    str(target.get("kind"))
                    for target in request.stage_input.get("target_workset") or []
                    if isinstance(target, dict) and target.get("kind")
                }
            )
        )
        semantic_input = fixture_semantic_input(stage_name, request.stage_input)
        input_hash = fixture_semantic_hash(semantic_input)
        candidates = [
            case
            for case in self.bundle.manifest.cases
            if case.operation == operation
            and case.stage == stage_name
            and case.regeneration_phase == phase
            and case.target_kinds == target_kinds
            and case.pipeline_version == configuration.pipeline_version
            and case.provider_identity == configuration.provider_identity
        ]
        case = next(
            (candidate for candidate in candidates if candidate.semantic_input_hash == input_hash),
            None,
        )
        if case is None:
            raise ProviderExecutionError(
                "fixture_input_mismatch",
                "No immutable fixture matches the semantic stage input.",
                retryable=True,
                details={
                    "scenario_id": self.bundle.manifest.scenario_id,
                    "stage": stage_name,
                    "semantic_input_hash": input_hash,
                    "candidate_hashes": [candidate.semantic_input_hash for candidate in candidates],
                },
            )
        return case

    def _execute(
        self,
        stage_name: FixtureStage,
        request: ProviderStageRequest,
    ) -> StageResultEnvelope:
        case = self._case(stage_name, request)
        output_template = self.bundle.output_template(case)
        if stage_name == "constructing_patch":
            if output_template != {"$build_from_validated_checkpoints": "v1"}:
                raise ValueError("fixture patch output requires the versioned checkpoint builder")
            raw_output = _build_fixture_patch(request.stage_input)
        else:
            raw_output = materialize_fixture(output_template, request.stage_input)
        output_model = STAGE_OUTPUT_MODELS[stage_name]
        output = output_model.model_validate_json(json.dumps(raw_output))
        events: list[ProgressEventEnvelope] = []
        for raw_event in self.bundle.progress_templates(case):
            materialized = materialize_fixture(raw_event, request.stage_input)
            event = ProgressEventEnvelope.model_validate_json(json.dumps(materialized))
            validate_progress_payload(event.event_type, event.payload)
            events.extend(request.deliver_progress((event,)))
        return StageResultEnvelope(
            stage_name=stage_name,
            output=output.model_dump(mode="json"),
            provider_identity=request.configuration.provider_identity,
            model_response_id=f"fixture:{case.case_id}",
            progress_events=tuple(events),
        )

    def plan(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self._execute("planning", request)

    def research(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self._execute("researching", request)

    def extract(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self._execute("extracting", request)

    def synthesize(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self._execute("synthesizing", request)

    def critique(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self._execute("critiquing", request)

    def construct_patch(self, request: ProviderStageRequest) -> StageResultEnvelope:
        return self._execute("constructing_patch", request)


__all__ = [
    "FixtureBundle",
    "FixtureCase",
    "FixtureManifest",
    "StrictFixtureProviders",
    "fixture_semantic_hash",
    "fixture_semantic_input",
    "materialize_fixture",
]
