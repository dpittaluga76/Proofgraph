from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models.functions import Now
from django.utils import timezone

from proofgraph.generation.context import canonical_json
from proofgraph.generation.models import (
    SourceContentCache,
    SourceIngestionRequest,
    SourceIngestionStatus,
)
from proofgraph.generation.research_cache import ResearchCacheStore
from proofgraph.generation.retention import validate_retained_payload
from proofgraph.generation.schemas import SourceIngestionEnvelope
from proofgraph.generation.secure_sources import (
    SecureSourceRetriever,
    SourceRetrievalError,
    TransientSourceDocument,
    normalize_https_url,
)
from proofgraph.generation.source_identity import classify_source_authority
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, GraphOperation, Node, NodeKind
from proofgraph.graph.serialization import serialize_node

SOURCE_INGESTION_LEASE_SECONDS = 30
CACHE_FRESH_SECONDS = 60 * 60
CACHE_EXPIRY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class SourceIngestionLease:
    ingestion_id: uuid.UUID
    canvas_id: uuid.UUID
    lease_token: uuid.UUID
    lease_epoch: int
    operation_key: str
    request_fingerprint: str


@dataclass(frozen=True)
class SourceIngestionResult:
    payload: dict[str, Any]
    status: int


def _fingerprint(envelope: SourceIngestionEnvelope) -> str:
    value: dict[str, Any] = {
        "url": envelope.url,
        "title": envelope.title,
    }
    if envelope.text is not None:
        value["text_hash"] = hashlib.sha256(envelope.text.encode("utf-8")).hexdigest()
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def serialize_source_node(node: Node) -> dict[str, Any]:
    if node.kind != NodeKind.SOURCE:
        raise ValueError("only source nodes may use source serialization")
    serialized = serialize_node(node)
    return {
        "id": serialized["id"],
        "canvas_id": serialized["canvas_id"],
        "kind": serialized["kind"],
        "title": serialized["title"],
        "sanitized_excerpt": serialized["body"],
        "metadata": serialized["metadata"],
        "version": serialized["version"],
        "created_at": serialized["created_at"],
    }


def serialize_ingestion(ingestion: SourceIngestionRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ingestion_id": str(ingestion.id),
        "canvas_id": str(ingestion.canvas_id),
        "status": ingestion.status,
        "lease_epoch": ingestion.lease_epoch,
        "error": ingestion.error,
        "created_at": ingestion.created_at.isoformat(),
        "updated_at": ingestion.updated_at.isoformat(),
        "source": None,
    }
    if ingestion.result_source_node_id is not None:
        payload["source"] = serialize_source_node(ingestion.result_source_node)
    return payload


def _reserve(
    canvas_id: uuid.UUID,
    envelope: SourceIngestionEnvelope,
) -> SourceIngestionLease | SourceIngestionResult:
    fingerprint = _fingerprint(envelope)
    token = uuid.uuid4()
    worker_id = f"source-api:{uuid.uuid4()}"
    with transaction.atomic():
        canvas = (
            Canvas.objects.select_for_update()
            .annotate(database_now=Now())
            .filter(pk=canvas_id)
            .first()
        )
        if canvas is None:
            raise GraphAPIError(status=404, code="canvas_not_found", message="Canvas not found.")
        now = canvas.database_now
        ingestion = (
            SourceIngestionRequest.objects.select_for_update()
            .filter(canvas=canvas, operation_key=envelope.operation_key)
            .first()
        )
        if ingestion is not None:
            if ingestion.request_fingerprint != fingerprint:
                emit_telemetry(
                    "source_ingestion.reservation",
                    ingestion_id=ingestion.id,
                    canvas_id=canvas_id,
                    outcome="conflict",
                )
                raise GraphAPIError(
                    status=409,
                    code="source_operation_key_conflict",
                    message="The source operation key was used for a different request.",
                )
            if ingestion.status == SourceIngestionStatus.COMPLETED:
                emit_telemetry(
                    "source_ingestion.reservation",
                    ingestion_id=ingestion.id,
                    canvas_id=canvas_id,
                    outcome="completed_replay",
                )
                return SourceIngestionResult(serialize_ingestion(ingestion), 200)
            if ingestion.status == SourceIngestionStatus.FAILED:
                emit_telemetry(
                    "source_ingestion.reservation",
                    ingestion_id=ingestion.id,
                    canvas_id=canvas_id,
                    outcome="failed_replay",
                )
                return SourceIngestionResult(serialize_ingestion(ingestion), 409)
            if ingestion.lease_expires_at is not None and ingestion.lease_expires_at > now:
                emit_telemetry(
                    "source_ingestion.reservation",
                    ingestion_id=ingestion.id,
                    canvas_id=canvas_id,
                    outcome="inflight_reuse",
                    lease_epoch=ingestion.lease_epoch,
                )
                return SourceIngestionResult(serialize_ingestion(ingestion), 202)
            ingestion.worker_id = worker_id
            ingestion.lease_token = token
            ingestion.lease_epoch += 1
            ingestion.lease_expires_at = now + timedelta(seconds=SOURCE_INGESTION_LEASE_SECONDS)
            ingestion.updated_at = now
            ingestion.save(
                update_fields=[
                    "worker_id",
                    "lease_token",
                    "lease_epoch",
                    "lease_expires_at",
                    "updated_at",
                ]
            )
            outcome = "reclaimed"
        else:
            ingestion = SourceIngestionRequest.objects.create(
                canvas=canvas,
                operation_key=envelope.operation_key,
                request_fingerprint=fingerprint,
                status=SourceIngestionStatus.RUNNING,
                worker_id=worker_id,
                lease_token=token,
                lease_epoch=1,
                lease_expires_at=now + timedelta(seconds=SOURCE_INGESTION_LEASE_SECONDS),
                created_at=now,
                updated_at=now,
            )
            outcome = "reserved"
    emit_telemetry(
        "source_ingestion.reservation",
        ingestion_id=ingestion.id,
        canvas_id=canvas_id,
        outcome=outcome,
        lease_epoch=ingestion.lease_epoch,
    )
    return SourceIngestionLease(
        ingestion_id=ingestion.id,
        canvas_id=canvas_id,
        lease_token=token,
        lease_epoch=ingestion.lease_epoch,
        operation_key=envelope.operation_key,
        request_fingerprint=fingerprint,
    )


def _locked_fenced_ingestion(lease: SourceIngestionLease) -> SourceIngestionRequest:
    ingestion = (
        SourceIngestionRequest.objects.select_for_update()
        .filter(
            pk=lease.ingestion_id,
            canvas_id=lease.canvas_id,
            status=SourceIngestionStatus.RUNNING,
            lease_token=lease.lease_token,
            lease_epoch=lease.lease_epoch,
        )
        .first()
    )
    if ingestion is None:
        emit_telemetry(
            "source_ingestion.fence_lost",
            ingestion_id=lease.ingestion_id,
            canvas_id=lease.canvas_id,
            lease_epoch=lease.lease_epoch,
        )
        raise GraphAPIError(
            status=409,
            code="source_ingestion_lease_lost",
            message="The source ingestion lease was reclaimed by another request.",
        )
    return ingestion


def _finalize_failure(lease: SourceIngestionLease, error: SourceRetrievalError) -> None:
    persisted = {
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "details": error.details,
    }
    validate_retained_payload(persisted)
    with transaction.atomic():
        Canvas.objects.select_for_update().get(pk=lease.canvas_id)
        ingestion = _locked_fenced_ingestion(lease)
        ingestion.status = SourceIngestionStatus.FAILED
        ingestion.worker_id = None
        ingestion.lease_token = None
        ingestion.lease_expires_at = None
        ingestion.error = persisted
        ingestion.updated_at = timezone.now()
        ingestion.save(
            update_fields=[
                "status",
                "worker_id",
                "lease_token",
                "lease_expires_at",
                "error",
                "updated_at",
            ]
        )
    emit_telemetry(
        "source_ingestion.failed",
        ingestion_id=lease.ingestion_id,
        canvas_id=lease.canvas_id,
        lease_epoch=lease.lease_epoch,
        code=error.code,
        retryable=error.retryable,
    )


def _source_metadata(document: TransientSourceDocument) -> dict[str, Any]:
    authority = None
    if document.normalized_url is not None:
        authority = classify_source_authority(
            document.normalized_url,
            hierarchy_rank=6,
            title=document.title,
        )
    return {
        "canonical_url": document.normalized_url,
        "content_hash": document.content_hash,
        "source_kind": document.kind,
        "retrieved_at": document.retrieved_at_iso,
        "independence_key": document.independence_key,
        "content_type": document.content_type,
        "authority": {
            "domain": authority.domain if authority is not None else None,
            "publisher": authority.publisher if authority is not None else "user supplied",
            "authoritative": authority.authoritative if authority is not None else False,
            "hierarchy_rank": authority.hierarchy_rank if authority is not None else 6,
        },
        "untrusted_source": True,
        "cache_hit": document.cache_hit,
    }


def _finalize_success(
    lease: SourceIngestionLease,
    document: TransientSourceDocument,
) -> SourceIngestionResult:
    now = timezone.now()
    metadata = _source_metadata(document)
    validate_retained_payload(
        {
            "kind": "source",
            "sanitized_excerpt": document.sanitized_excerpt,
            "metadata": metadata,
        }
    )
    with transaction.atomic():
        canvas = Canvas.objects.select_for_update().get(pk=lease.canvas_id)
        ingestion = _locked_fenced_ingestion(lease)
        source = Node.objects.create(
            canvas=canvas,
            kind=NodeKind.SOURCE,
            title=document.title,
            body=document.sanitized_excerpt,
            metadata=metadata,
        )
        canvas.revision += 1
        canvas.updated_at = now
        canvas.save(update_fields=["revision", "updated_at"])
        source_payload = serialize_source_node(source)
        operation_payload = {
            "ingestion_id": str(ingestion.id),
            "source_kind": document.kind,
            "canonical_url": document.normalized_url,
            "content_hash": document.content_hash,
        }
        validate_retained_payload(operation_payload)
        GraphOperation.objects.create(
            canvas=canvas,
            actor_type="source_ingestion",
            operation_key=lease.operation_key,
            request_fingerprint=lease.request_fingerprint,
            operation_type="ADD_SOURCE",
            payload=operation_payload,
            result_payload={"source": source_payload},
            canvas_revision=canvas.revision,
        )
        if document.normalized_url is not None and not document.cache_hit:
            cache_metadata = {
                "title": document.title,
                "content_hash": document.content_hash,
                "independence_key": document.independence_key,
                "sanitized_excerpt": document.sanitized_excerpt,
                "content_type": document.content_type,
                "redirect_count": document.redirect_count,
            }
            validate_retained_payload(cache_metadata)
            SourceContentCache.objects.update_or_create(
                canvas=canvas,
                normalized_url=document.normalized_url,
                content_hash=document.content_hash,
                defaults={
                    "retained_content": None,
                    "retrieval_metadata": cache_metadata,
                    "retrieved_at": datetime.fromisoformat(
                        document.retrieved_at_iso.replace("Z", "+00:00")
                    ),
                    "fresh_until": now + timedelta(seconds=CACHE_FRESH_SECONDS),
                    "expires_at": now + timedelta(seconds=CACHE_EXPIRY_SECONDS),
                },
            )
        ingestion.status = SourceIngestionStatus.COMPLETED
        ingestion.worker_id = None
        ingestion.lease_token = None
        ingestion.lease_expires_at = None
        ingestion.result_source_node = source
        ingestion.updated_at = now
        ingestion.save(
            update_fields=[
                "status",
                "worker_id",
                "lease_token",
                "lease_expires_at",
                "result_source_node",
                "updated_at",
            ]
        )
        result = SourceIngestionResult(serialize_ingestion(ingestion), 201)
    emit_telemetry(
        "source_ingestion.completed",
        ingestion_id=lease.ingestion_id,
        canvas_id=lease.canvas_id,
        source_id=source.id,
        lease_epoch=lease.lease_epoch,
    )
    return result


def create_source(
    canvas_id: uuid.UUID,
    envelope: SourceIngestionEnvelope,
    *,
    retriever: SecureSourceRetriever | None = None,
) -> SourceIngestionResult:
    reservation = _reserve(canvas_id, envelope)
    if isinstance(reservation, SourceIngestionResult):
        return reservation
    retriever = retriever or SecureSourceRetriever()
    retrieved_at_iso = timezone.now().isoformat()
    started = time.monotonic()
    try:
        if envelope.url is not None:
            normalized_url = normalize_https_url(envelope.url)
            cached = ResearchCacheStore().get_source(
                canvas=Canvas.objects.get(pk=canvas_id),
                normalized_url=normalized_url,
            )
            if cached is None:
                document = retriever.retrieve_url(
                    normalized_url,
                    retrieved_at_iso=retrieved_at_iso,
                )
            else:
                document = TransientSourceDocument(
                    kind="user_url",
                    normalized_url=normalized_url,
                    title=str(cached["title"]),
                    retrieved_at_iso=str(cached["retrieved_at"]),
                    content_hash=str(cached["content_hash"]),
                    independence_key=str(cached["independence_key"]),
                    sanitized_excerpt=str(cached["sanitized_excerpt"]),
                    untrusted_content="",
                    content_type=str(cached["content_type"]),
                    redirect_count=int(cached.get("redirect_count", 0)),
                    cache_hit=True,
                )
        else:
            assert envelope.text is not None
            document = retriever.receive_text(
                envelope.text,
                title=envelope.title,
                retrieved_at_iso=retrieved_at_iso,
            )
    except SourceRetrievalError as error:
        _finalize_failure(reservation, error)
        raise GraphAPIError(
            status=502 if error.retryable else 422,
            code=error.code,
            message=error.message,
            details={
                **error.details,
                "ingestion_id": str(reservation.ingestion_id),
                "retryable": error.retryable,
            },
        ) from error
    emit_telemetry(
        "source_ingestion.retrieved",
        ingestion_id=reservation.ingestion_id,
        canvas_id=reservation.canvas_id,
        source_kind=document.kind,
        cache_hit=document.cache_hit,
        latency_ms=int((time.monotonic() - started) * 1_000),
        redirect_count=document.redirect_count,
    )
    return _finalize_success(reservation, document)


def get_source_ingestion(ingestion_id: uuid.UUID) -> SourceIngestionRequest:
    ingestion = SourceIngestionRequest.objects.filter(pk=ingestion_id).first()
    if ingestion is None:
        raise GraphAPIError(
            status=404,
            code="source_ingestion_not_found",
            message="Source ingestion not found.",
        )
    return ingestion


def get_source(source_id: uuid.UUID) -> Node:
    source = Node.objects.filter(pk=source_id, kind=NodeKind.SOURCE).first()
    if source is None:
        raise GraphAPIError(status=404, code="source_not_found", message="Source not found.")
    return source
