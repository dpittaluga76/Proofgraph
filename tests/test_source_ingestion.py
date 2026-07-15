from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client
from django.utils import timezone

from proofgraph.generation.models import (
    ResearchQueryCache,
    SourceContentCache,
    SourceIngestionRequest,
    SourceIngestionStatus,
)
from proofgraph.generation.schemas import SourceIngestionEnvelope
from proofgraph.generation.secure_sources import (
    SecureSourceRetriever,
    SourceRetrievalError,
    TransientSourceDocument,
)
from proofgraph.generation.source_ingestion import (
    SourceIngestionLease,
    SourceIngestionResult,
    _finalize_failure,
    _reserve,
)
from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas, GraphOperation, Node, NodeKind

pytestmark = pytest.mark.django_db(transaction=True)


def envelope(*, operation_key: str = "source-one") -> SourceIngestionEnvelope:
    return SourceIngestionEnvelope(
        operation_key=operation_key,
        url="https://example.com/evidence",
    )


def document(*, untrusted_content: str = "transient full document") -> TransientSourceDocument:
    return TransientSourceDocument(
        kind="user_url",
        normalized_url="https://example.com/evidence",
        title="Public evidence",
        retrieved_at_iso="2026-07-14T12:00:00+00:00",
        content_hash="sha256:" + ("a" * 64),
        independence_key="publisher:example.com",
        sanitized_excerpt="Bounded derived excerpt.",
        untrusted_content=untrusted_content,
        content_type="text/html",
    )


def test_source_endpoint_reserves_outside_work_and_is_idempotent() -> None:
    canvas = Canvas.objects.create(title="Source intake")
    sentinel = "SENTINEL COMPLETE SOURCE DOCUMENT"
    calls = 0

    def retrieve(
        _self: SecureSourceRetriever,
        _url: str,
        *,
        retrieved_at_iso: str,
    ) -> TransientSourceDocument:
        nonlocal calls
        calls += 1
        assert retrieved_at_iso
        assert not connection.in_atomic_block
        return document(untrusted_content=sentinel)

    client = Client()
    body = {"operation_key": "source-one", "url": "https://example.com/evidence"}
    with patch.object(SecureSourceRetriever, "retrieve_url", retrieve):
        created = client.post(
            f"/api/canvases/{canvas.id}/sources",
            data=json.dumps(body),
            content_type="application/json",
        )
        replay = client.post(
            f"/api/canvases/{canvas.id}/sources",
            data=json.dumps(body),
            content_type="application/json",
        )

    assert created.status_code == 201, created.content
    assert replay.status_code == 200
    assert created.json()["source"] == replay.json()["source"]
    assert calls == 1
    source_id = created.json()["source"]["id"]
    source = client.get(f"/api/sources/{source_id}")
    ingestion = client.get(f"/api/source-ingestions/{created.json()['ingestion_id']}")
    assert source.status_code == ingestion.status_code == 200
    assert source.json()["source"]["sanitized_excerpt"] == "Bounded derived excerpt."

    persisted = json.dumps(
        {
            "nodes": list(Node.objects.values("title", "body", "metadata")),
            "operations": list(GraphOperation.objects.values("payload", "result_payload")),
            "ingestions": list(SourceIngestionRequest.objects.values("error")),
            "caches": list(SourceContentCache.objects.values("retrieval_metadata")),
        },
        default=str,
    )
    assert sentinel not in persisted
    assert SourceContentCache.objects.get().retained_content is None


def test_fresh_source_content_cache_is_reused_across_operation_keys() -> None:
    canvas = Canvas.objects.create(title="Source cache reuse")
    calls = 0

    def retrieve(
        _self: SecureSourceRetriever,
        _url: str,
        *,
        retrieved_at_iso: str,
    ) -> TransientSourceDocument:
        nonlocal calls
        calls += 1
        return document()

    client = Client()
    with patch.object(SecureSourceRetriever, "retrieve_url", retrieve):
        first = client.post(
            f"/api/canvases/{canvas.id}/sources",
            data=json.dumps(
                {"operation_key": "source-cache-one", "url": "https://example.com/evidence"}
            ),
            content_type="application/json",
        )
        second = client.post(
            f"/api/canvases/{canvas.id}/sources",
            data=json.dumps(
                {"operation_key": "source-cache-two", "url": "https://example.com/evidence"}
            ),
            content_type="application/json",
        )

    assert first.status_code == second.status_code == 201
    assert calls == 1
    assert (
        first.json()["source"]["metadata"]["retrieved_at"]
        == (second.json()["source"]["metadata"]["retrieved_at"])
    )
    assert (
        first.json()["source"]["metadata"]["content_hash"]
        == (second.json()["source"]["metadata"]["content_hash"])
    )
    assert first.json()["source"]["metadata"]["cache_hit"] is False
    assert second.json()["source"]["metadata"]["cache_hit"] is True
    assert SourceContentCache.objects.count() == 1


def test_conflicting_key_and_identical_inflight_retry_contract() -> None:
    canvas = Canvas.objects.create(title="Single flight")
    first = _reserve(canvas.id, envelope(operation_key="same-key"))
    assert isinstance(first, SourceIngestionLease)
    client = Client()

    inflight = client.post(
        f"/api/canvases/{canvas.id}/sources",
        data=json.dumps(
            {
                "operation_key": "same-key",
                "url": "https://example.com/evidence",
            }
        ),
        content_type="application/json",
    )
    conflict = client.post(
        f"/api/canvases/{canvas.id}/sources",
        data=json.dumps(
            {
                "operation_key": "same-key",
                "url": "https://different.example/evidence",
            }
        ),
        content_type="application/json",
    )

    assert inflight.status_code == 202
    assert inflight.json()["ingestion_id"] == str(first.ingestion_id)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "source_operation_key_conflict"


def test_concurrent_reservation_has_one_owner_and_one_inflight_result() -> None:
    canvas = Canvas.objects.create(title="Concurrent reservation")

    def reserve() -> SourceIngestionLease | SourceIngestionResult:
        close_old_connections()
        try:
            return _reserve(canvas.id, envelope(operation_key="concurrent-key"))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: reserve(), range(2)))

    assert sum(isinstance(result, SourceIngestionLease) for result in results) == 1
    waiting = next(result for result in results if isinstance(result, SourceIngestionResult))
    assert waiting.status == 202
    assert SourceIngestionRequest.objects.count() == 1


def test_expired_reservation_reclaim_fences_the_stale_owner() -> None:
    canvas = Canvas.objects.create(title="Reclaim")
    original = _reserve(canvas.id, envelope(operation_key="reclaim-key"))
    assert isinstance(original, SourceIngestionLease)
    SourceIngestionRequest.objects.filter(pk=original.ingestion_id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    replacement = _reserve(canvas.id, envelope(operation_key="reclaim-key"))
    assert isinstance(replacement, SourceIngestionLease)
    assert replacement.lease_epoch == original.lease_epoch + 1
    assert replacement.lease_token != original.lease_token

    with pytest.raises(GraphAPIError) as captured:
        _finalize_failure(
            original,
            SourceRetrievalError("stale", "Stale owner must be fenced."),
        )
    assert captured.value.code == "source_ingestion_lease_lost"
    ingestion = SourceIngestionRequest.objects.get(pk=original.ingestion_id)
    assert ingestion.status == SourceIngestionStatus.RUNNING
    assert ingestion.lease_token == replacement.lease_token


def test_reservation_lease_comparison_uses_postgresql_time() -> None:
    canvas = Canvas.objects.create(title="Database clock")
    original = _reserve(canvas.id, envelope(operation_key="database-clock"))
    assert isinstance(original, SourceIngestionLease)

    with patch(
        "proofgraph.generation.source_ingestion.timezone.now",
        return_value=timezone.now() + timedelta(days=1),
    ):
        replay = _reserve(canvas.id, envelope(operation_key="database-clock"))

    assert isinstance(replay, SourceIngestionResult)
    assert replay.status == 202
    assert SourceIngestionRequest.objects.get(pk=original.ingestion_id).lease_epoch == 1


def test_source_ingestion_result_rejects_cross_canvas_node() -> None:
    canvas = Canvas.objects.create(title="Primary")
    other = Canvas.objects.create(title="Other")
    foreign_source = Node.objects.create(
        canvas=other,
        kind=NodeKind.SOURCE,
        title="Foreign source",
        body="Safe excerpt",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        SourceIngestionRequest.objects.create(
            canvas=canvas,
            operation_key="foreign-source",
            request_fingerprint="fingerprint",
            status=SourceIngestionStatus.COMPLETED,
            result_source_node=foreign_source,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_cache_and_ingestion_rows_follow_canvas_lifecycle() -> None:
    canvas = Canvas.objects.create(title="Cache lifecycle")
    now = timezone.now()
    query = ResearchQueryCache.objects.create(
        canvas=canvas,
        normalized_query="query",
        provider_identity="provider",
        strategy_version="strategy",
        prompt_version="prompt",
        context_hash="context",
        result={"sources": []},
        retrieved_at=now,
        fresh_until=now + timedelta(hours=1),
        expires_at=now + timedelta(hours=24),
    )
    content = SourceContentCache.objects.create(
        canvas=canvas,
        normalized_url="https://example.com/",
        content_hash="sha256:" + ("b" * 64),
        retained_content=None,
        retrieval_metadata={"sanitized_excerpt": "Safe"},
        retrieved_at=now,
        fresh_until=now + timedelta(hours=1),
        expires_at=now + timedelta(hours=24),
    )
    ingestion = _reserve(canvas.id, envelope(operation_key="lifecycle"))
    assert isinstance(ingestion, SourceIngestionLease)

    canvas.delete()

    assert not ResearchQueryCache.objects.filter(pk=query.pk).exists()
    assert not SourceContentCache.objects.filter(pk=content.pk).exists()
    assert not SourceIngestionRequest.objects.filter(pk=ingestion.ingestion_id).exists()


def test_source_input_hard_bounds_fail_before_reservation() -> None:
    canvas = Canvas.objects.create(title="Bounds")
    response = Client().post(
        f"/api/canvases/{canvas.id}/sources",
        data=json.dumps(
            {
                "operation_key": "too-large",
                "text": "é" * (60 * 1024),
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_source_request"
    assert not SourceIngestionRequest.objects.exists()


def test_source_ingestion_reclaim_index_is_partial() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'source_ingestion_request' "
            "AND indexname = 'source_ingestion_reclaim_idx'"
        )
        index_definition = cursor.fetchone()[0]
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN (COSTS OFF) "
            "SELECT id FROM source_ingestion_request "
            "WHERE status = 'running' AND lease_expires_at <= CURRENT_TIMESTAMP "
            "ORDER BY lease_expires_at, id LIMIT 1"
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "WHERE (status = 'running'::text)" in index_definition
    assert "source_ingestion_reclaim_idx" in plan
