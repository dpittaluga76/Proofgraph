from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from proofgraph.generation.pipeline_schemas import (
    ClusteringOutput,
    EvidenceCluster,
    ExtractionOutput,
    RejectedEvidence,
)
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.schemas import StageResultEnvelope
from proofgraph.generation.telemetry import emit_telemetry

MAX_RETAINED_CLAIMS = 12
_STRENGTH_RANK = {"strong": 3, "medium": 2, "weak": 1}


def _claim_rank(
    claim: Any,
    source_by_id: dict[str, Any],
) -> tuple[int, int, int, int, float, str]:
    sources = [source_by_id[source_id] for source_id in claim.source_ids]
    authoritative = int(any(source.authority.authoritative for source in sources))
    authority_rank = min(source.authority.hierarchy_rank for source in sources)
    independence_count = len({source.independence_key for source in sources})
    recency = max(source.retrieved_at.timestamp() for source in sources)
    return (
        -authoritative,
        authority_rank,
        -independence_count,
        -_STRENGTH_RANK[claim.strength],
        -recency,
        claim.id,
    )


def select_retained_claims(extraction: ExtractionOutput) -> ExtractionOutput:
    source_by_id = {source.id: source for source in extraction.sources}
    ranked = sorted(extraction.claims, key=lambda claim: _claim_rank(claim, source_by_id))
    seen: set[tuple[object, ...]] = set()
    retained = []
    rejected = list(extraction.rejected)
    for claim in ranked:
        semantic_key = (
            claim.claim.casefold(),
            claim.evidence_type,
            claim.topic_keys,
            claim.mechanism_tags,
            claim.contradiction_target_key,
        )
        if semantic_key in seen:
            rejected.append(
                RejectedEvidence(
                    subject_kind="claim",
                    source_or_claim_id=claim.id,
                    reason="duplicate",
                    details="Duplicate semantic claim excluded during deterministic retention.",
                )
            )
            continue
        seen.add(semantic_key)
        if len(retained) < MAX_RETAINED_CLAIMS:
            retained.append(claim)
        else:
            rejected.append(
                RejectedEvidence(
                    subject_kind="claim",
                    source_or_claim_id=claim.id,
                    reason="rejected",
                    details="Claim excluded by the deterministic twelve-claim budget.",
                )
            )
    emit_telemetry(
        "extraction.retained",
        input_count=len(extraction.claims),
        retained_count=len(retained),
        rejected_count=len(rejected),
    )
    return extraction.model_copy(
        update={
            "claims": tuple(retained),
            "rejected": tuple(rejected),
        }
    )


def _extraction_from_stage_input(stage_input: dict[str, Any]) -> ExtractionOutput:
    prior = stage_input.get("prior_stage_outputs")
    if not isinstance(prior, dict):
        raise ValueError("clustering requires prior extraction output")
    candidates = [
        value for key, value in prior.items() if key == "extracting" or key.endswith(":extracting")
    ]
    if not candidates:
        raise ValueError("clustering requires prior extraction output")
    candidate = candidates[-1]
    if not isinstance(candidate, dict) or not isinstance(candidate.get("output"), dict):
        raise ValueError("the extraction checkpoint output is malformed")
    return ExtractionOutput.model_validate_json(json.dumps(candidate["output"]))


class ExactEvidenceClusterer:
    version = "deterministic_clusterer_v1"

    def cluster(self, request: ProviderStageRequest) -> StageResultEnvelope:
        extraction = select_retained_claims(_extraction_from_stage_input(request.stage_input))
        source_by_id = {source.id: source for source in extraction.sources}
        grouped: dict[tuple[object, ...], list[Any]] = defaultdict(list)
        for claim in extraction.claims:
            key = (
                claim.evidence_type,
                claim.topic_keys,
                claim.mechanism_tags,
                claim.contradiction_target_key,
            )
            grouped[key].append(claim)

        clusters: list[EvidenceCluster] = []
        for key in sorted(grouped, key=lambda value: json.dumps(value, sort_keys=True)):
            claims = grouped[key]
            claim_ids = tuple(sorted(claim.id for claim in claims))
            source_ids = tuple(
                sorted({source_id for claim in claims for source_id in claim.source_ids})
            )
            independence_keys = tuple(
                sorted({source_by_id[source_id].independence_key for source_id in source_ids})
            )
            serialized_key = json.dumps(key, sort_keys=True, separators=(",", ":"))
            cluster_id = f"cluster_{hashlib.sha256(serialized_key.encode()).hexdigest()[:24]}"
            clusters.append(
                EvidenceCluster(
                    id=cluster_id,
                    evidence_type=key[0],
                    topic_keys=key[1],
                    mechanism_tags=key[2],
                    contradiction_target_key=key[3],
                    claim_ids=claim_ids,
                    source_ids=source_ids,
                    independence_keys=independence_keys,
                    independent_support_count=len(independence_keys),
                )
            )
        output = ClusteringOutput(clusters=tuple(clusters))
        emit_telemetry(
            "evidence.clustered",
            cluster_count=len(clusters),
            independent_source_counts=[cluster.independent_support_count for cluster in clusters],
        )
        return StageResultEnvelope(
            stage_name="clustering",
            output=output.model_dump(mode="json"),
            provider_identity=request.configuration.provider_identity,
        )


__all__ = ["ExactEvidenceClusterer", "select_retained_claims"]
