import uuid
from datetime import timedelta
from threading import Thread

import pytest
from django.db import close_old_connections, transaction
from django.utils import timezone

from proofgraph.generation.events import TERMINAL_EVENT_TYPES
from proofgraph.generation.models import (
    GenerationEvent,
    GenerationRun,
    GraphPatch,
    RunOperation,
    RunStatus,
)
from proofgraph.generation.queue import (
    LeaseLostError,
    claim_run,
    finalize_expired_patch_ready_runs,
    lock_fenced_run,
    renew_lease,
    terminalize_exhausted_runs,
)
from proofgraph.graph.models import Canvas

pytestmark = pytest.mark.django_db(transaction=True)


def make_run(canvas: Canvas, *, attempt: int = 0, max_attempts: int = 3) -> GenerationRun:
    return GenerationRun.objects.create(
        canvas=canvas,
        operation=RunOperation.GENERATE_STRATEGIES,
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint=str(uuid.uuid4()),
        base_canvas_revision=0,
        context_snapshot={"nodes": []},
        context_manifest={},
        context_hash="hash",
        selected_node_ids=[],
        expected_node_versions={},
        execution_configuration={"profile_id": "test"},
        attempt=attempt,
        max_attempts=max_attempts,
    )


def test_claim_and_expired_reclaim_increment_attempt_epoch_and_token() -> None:
    canvas = Canvas.objects.create(title="Claim")
    run = make_run(canvas)

    first = claim_run("worker-a")
    assert first is not None
    run.refresh_from_db()
    assert (run.status, run.attempt, run.lease_epoch) == (RunStatus.RUNNING, 1, 1)
    first_expiry = run.lease_expires_at

    GenerationRun.objects.filter(pk=run.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    second = claim_run("worker-b")

    assert second is not None
    run.refresh_from_db()
    assert (run.attempt, run.lease_epoch, run.worker_id) == (2, 2, "worker-b")
    assert second.lease_token != first.lease_token
    assert run.lease_expires_at > first_expiry
    assert renew_lease(first) is False
    with transaction.atomic(), pytest.raises(LeaseLostError):
        lock_fenced_run(first)


def test_current_lease_heartbeat_extends_database_expiry() -> None:
    canvas = Canvas.objects.create(title="Heartbeat")
    run = make_run(canvas)
    lease = claim_run("worker")
    assert lease is not None
    run.refresh_from_db()
    original_expiry = run.lease_expires_at

    assert renew_lease(lease) is True
    run.refresh_from_db()

    assert run.heartbeat_at is not None
    assert run.lease_expires_at >= original_expiry


def test_exhausted_queued_run_is_poisoned_once_and_never_claimed() -> None:
    canvas = Canvas.objects.create(title="Poison")
    run = make_run(canvas, attempt=2, max_attempts=2)

    assert terminalize_exhausted_runs() == 1
    assert terminalize_exhausted_runs() == 0
    assert claim_run("worker") is None
    run.refresh_from_db()

    assert run.status == RunStatus.FAILED
    assert run.error["code"] == "attempts_exhausted"
    assert run.error["retryable"] is False
    assert GenerationEvent.objects.filter(run=run, event_type__in=TERMINAL_EVENT_TYPES).count() == 1


def test_claims_are_ordered_and_one_run_is_owned_at_a_time() -> None:
    canvas = Canvas.objects.create(title="Queue order")
    first_run = make_run(canvas)
    second_run = make_run(canvas)

    first_lease = claim_run("worker-a")
    second_lease = claim_run("worker-b")

    assert first_lease is not None and first_lease.run_id == first_run.id
    assert second_lease is not None and second_lease.run_id == second_run.id


def test_expired_patch_ready_crash_window_finishes_without_duplicate_finalization() -> None:
    canvas = Canvas.objects.create(title="Patch recovery")
    run = make_run(canvas)
    lease = claim_run("worker-a")
    assert lease is not None
    GraphPatch.objects.create(
        run=run,
        canvas=canvas,
        base_canvas_revision=0,
        operations=[],
    )
    GenerationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.PATCH_READY,
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    assert finalize_expired_patch_ready_runs() == 1
    assert finalize_expired_patch_ready_runs() == 0
    run.refresh_from_db()

    assert run.status == RunStatus.COMPLETED
    assert run.worker_id is None
    assert run.lease_epoch == lease.lease_epoch + 1
    assert run.events.filter(event_type="run.completed").count() == 1
    with transaction.atomic(), pytest.raises(LeaseLostError):
        lock_fenced_run(lease, statuses=(RunStatus.PATCH_READY,))


def test_skip_locked_allows_a_concurrent_worker_to_claim_the_next_run() -> None:
    canvas = Canvas.objects.create(title="Concurrent queue")
    first = make_run(canvas)
    second = make_run(canvas)
    claimed = []

    def claim_in_thread() -> None:
        close_old_connections()
        try:
            claimed.append(claim_run("worker-b"))
        finally:
            close_old_connections()

    with transaction.atomic():
        locked = (
            GenerationRun.objects.select_for_update()
            .filter(status=RunStatus.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        assert locked is not None and locked.id == first.id
        thread = Thread(target=claim_in_thread)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert claimed[0] is not None
    assert claimed[0].run_id == second.id
