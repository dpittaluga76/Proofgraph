import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from proofgraph.generation.models import GenerationRun, RunOperation, RunStatus
from proofgraph.graph.models import Canvas

pytestmark = pytest.mark.django_db(transaction=True)


def base_run(canvas: Canvas, index: int) -> dict[str, object]:
    return {
        "canvas": canvas,
        "operation": RunOperation.GENERATE_STRATEGIES,
        "idempotency_key": f"queue-{index}",
        "request_fingerprint": f"fingerprint-{index}",
        "base_canvas_revision": 0,
        "context_snapshot": {},
        "context_manifest": {},
        "context_hash": "hash",
        "selected_node_ids": [],
        "expected_node_versions": {},
        "execution_configuration": {},
    }


def test_claim_and_reclaim_queries_use_partial_indexes_at_representative_cardinality() -> None:
    canvas = Canvas.objects.create(title="Queue plans")
    now = timezone.now()
    queued = [GenerationRun(**base_run(canvas, index)) for index in range(100)]
    running = [
        GenerationRun(
            **base_run(canvas, index + 100),
            status=RunStatus.RUNNING,
            worker_id="worker",
            lease_token=uuid.uuid4(),
            lease_epoch=1,
            attempt=1,
            heartbeat_at=now - timedelta(seconds=90),
            lease_expires_at=now - timedelta(seconds=index + 1),
        )
        for index in range(100)
    ]
    completed = [
        GenerationRun(
            **base_run(canvas, index + 200),
            status=RunStatus.COMPLETED,
            completed_at=now,
        )
        for index in range(4_000)
    ]
    GenerationRun.objects.bulk_create([*queued, *running, *completed], batch_size=500)

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE generation_run")
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM generation_run
            WHERE status = 'queued' AND attempt < max_attempts
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        claim_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM generation_run
            WHERE status = 'running'
              AND lease_expires_at < now()
              AND attempt < max_attempts
            ORDER BY lease_expires_at, created_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        reclaim_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM generation_run
            WHERE attempt < max_attempts
              AND (
                status = 'queued'
                OR (status = 'running' AND lease_expires_at < now())
              )
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        combined_claim_plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "run_queued_claim_idx" in claim_plan
    assert "Seq Scan" not in claim_plan
    assert "run_expired_lease_idx" in reclaim_plan
    assert "Seq Scan" not in reclaim_plan
    assert "run_queued_claim_idx" in combined_claim_plan
    assert "run_expired_lease_idx" in combined_claim_plan
    assert "Seq Scan" not in combined_claim_plan
