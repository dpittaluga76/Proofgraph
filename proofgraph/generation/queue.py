from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connections, transaction
from django.db.models import F, Q
from django.db.models.functions import Now

from proofgraph.generation.events import append_event_locked
from proofgraph.generation.models import GenerationEventType, GenerationRun, RunStatus
from proofgraph.generation.telemetry import emit_telemetry


class LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunLease:
    run_id: uuid.UUID
    worker_id: str
    lease_token: uuid.UUID
    lease_epoch: int
    canvas_id: uuid.UUID | None = None


def _poison_error() -> dict[str, object]:
    return {
        "code": "attempts_exhausted",
        "message": "The run exhausted its maximum worker attempts.",
        "retryable": False,
        "stage": None,
        "details": {},
    }


def terminalize_exhausted_runs() -> int:
    terminalized = 0
    while True:
        with transaction.atomic():
            run = (
                GenerationRun.objects.select_for_update(skip_locked=True)
                .filter(attempt__gte=F("max_attempts"))
                .filter(
                    Q(status=RunStatus.QUEUED)
                    | Q(
                        status__in=[RunStatus.RUNNING, RunStatus.PATCH_READY],
                        lease_expires_at__lt=Now(),
                    )
                )
                .order_by("created_at", "id")
                .first()
            )
            if run is None:
                break
            run.status = RunStatus.FAILED
            run.error = _poison_error()
            run.completed_at = Now()
            run.worker_id = None
            run.lease_token = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.save(
                update_fields=[
                    "status",
                    "error",
                    "completed_at",
                    "worker_id",
                    "lease_token",
                    "heartbeat_at",
                    "lease_expires_at",
                ]
            )
            append_event_locked(
                run,
                GenerationEventType.RUN_FAILED,
                {**run.error, "attempt": run.attempt},
                terminal_once=True,
            )
            terminalized += 1
            emit_telemetry("run.poisoned", run_id=run.id, attempt=run.attempt)
    return terminalized


def finalize_expired_patch_ready_runs() -> int:
    """Close the tiny crash window between durable patch creation and run completion."""
    completed = 0
    while True:
        recovery_token = uuid.uuid4()
        recovery_worker_id = f"patch-ready-recovery:{recovery_token}"
        lease_duration = timedelta(seconds=settings.GENERATION_LEASE_SECONDS)
        with transaction.atomic():
            run = (
                GenerationRun.objects.select_for_update(skip_locked=True)
                .filter(
                    status=RunStatus.PATCH_READY,
                    lease_expires_at__lt=Now(),
                    patch__isnull=False,
                )
                .order_by("created_at", "id")
                .first()
            )
            if run is None:
                break
            run.worker_id = recovery_worker_id
            run.lease_token = recovery_token
            run.lease_epoch += 1
            run.heartbeat_at = Now()
            run.lease_expires_at = Now() + lease_duration
            run.save(
                update_fields=[
                    "worker_id",
                    "lease_token",
                    "lease_epoch",
                    "heartbeat_at",
                    "lease_expires_at",
                ]
            )
            run.refresh_from_db()
            recovery_lease = RunLease(
                run.id,
                recovery_worker_id,
                recovery_token,
                run.lease_epoch,
                run.canvas_id,
            )

        with transaction.atomic():
            run = lock_fenced_run(recovery_lease, statuses=(RunStatus.PATCH_READY,))
            patch_id = run.patch.id
            run.status = RunStatus.COMPLETED
            run.completed_at = Now()
            run.worker_id = None
            run.lease_token = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "worker_id",
                    "lease_token",
                    "heartbeat_at",
                    "lease_expires_at",
                ]
            )
            append_event_locked(
                run,
                GenerationEventType.RUN_COMPLETED,
                {
                    "patch_id": str(patch_id),
                    "attempt": run.attempt,
                    "recovered_after_lease_expiry": True,
                },
                terminal_once=True,
            )
            completed += 1
        emit_telemetry(
            "run.patch_ready_recovered",
            run_id=run.id,
            patch_id=patch_id,
            lease_epoch=recovery_lease.lease_epoch,
        )
    return completed


def claim_run(worker_id: str) -> RunLease | None:
    finalize_expired_patch_ready_runs()
    terminalize_exhausted_runs()
    token = uuid.uuid4()
    lease_duration = timedelta(seconds=settings.GENERATION_LEASE_SECONDS)
    with transaction.atomic():
        run = (
            GenerationRun.objects.select_for_update(skip_locked=True)
            .filter(attempt__lt=F("max_attempts"))
            .filter(
                Q(status=RunStatus.QUEUED) | Q(status=RunStatus.RUNNING, lease_expires_at__lt=Now())
            )
            .order_by("created_at", "id")
            .first()
        )
        if run is None:
            emit_telemetry(
                "queue.depth", depth=GenerationRun.objects.filter(status="queued").count()
            )
            return None

        resume = run.status == RunStatus.RUNNING or run.attempt > 0
        run.status = RunStatus.RUNNING
        run.worker_id = worker_id
        run.lease_token = token
        run.lease_epoch += 1
        run.attempt += 1
        run.heartbeat_at = Now()
        run.lease_expires_at = Now() + lease_duration
        if run.started_at is None:
            run.started_at = Now()
        run.save(
            update_fields=[
                "status",
                "worker_id",
                "lease_token",
                "lease_epoch",
                "attempt",
                "heartbeat_at",
                "lease_expires_at",
                "started_at",
            ]
        )
        run.refresh_from_db()
        event_type = GenerationEventType.RUN_RESUMED if resume else GenerationEventType.RUN_STARTED
        append_event_locked(
            run,
            event_type,
            {
                "attempt": run.attempt,
                "lease_epoch": run.lease_epoch,
                "worker_id": worker_id,
            },
        )
        lease = RunLease(run.id, worker_id, token, run.lease_epoch, run.canvas_id)

    emit_telemetry(
        "run.claimed",
        run_id=lease.run_id,
        canvas_id=lease.canvas_id,
        worker_id=worker_id,
        attempt=run.attempt,
        lease_epoch=lease.lease_epoch,
        reclaimed=resume,
    )
    return lease


def lock_fenced_run(
    lease: RunLease,
    *,
    statuses: tuple[str, ...] = (RunStatus.RUNNING,),
    require_live_lease: bool = True,
) -> GenerationRun:
    queryset = GenerationRun.objects.select_for_update().filter(
        pk=lease.run_id,
        status__in=statuses,
        worker_id=lease.worker_id,
        lease_token=lease.lease_token,
        lease_epoch=lease.lease_epoch,
    )
    if require_live_lease:
        queryset = queryset.filter(lease_expires_at__gt=Now())
    run = queryset.first()
    if run is None:
        emit_telemetry(
            "run.lease_lost",
            run_id=lease.run_id,
            canvas_id=lease.canvas_id,
            worker_id=lease.worker_id,
            lease_epoch=lease.lease_epoch,
        )
        raise LeaseLostError(f"Lease lost for generation run {lease.run_id}")
    return run


def renew_lease(lease: RunLease) -> bool:
    lease_duration = timedelta(seconds=settings.GENERATION_LEASE_SECONDS)
    updated = GenerationRun.objects.filter(
        pk=lease.run_id,
        status=RunStatus.RUNNING,
        worker_id=lease.worker_id,
        lease_token=lease.lease_token,
        lease_epoch=lease.lease_epoch,
        lease_expires_at__gt=Now(),
    ).update(
        heartbeat_at=Now(),
        lease_expires_at=Now() + lease_duration,
    )
    if updated:
        emit_telemetry(
            "run.heartbeat",
            run_id=lease.run_id,
            canvas_id=lease.canvas_id,
            worker_id=lease.worker_id,
            lease_epoch=lease.lease_epoch,
        )
    else:
        emit_telemetry(
            "run.lease_lost",
            run_id=lease.run_id,
            canvas_id=lease.canvas_id,
            worker_id=lease.worker_id,
            lease_epoch=lease.lease_epoch,
        )
    return updated == 1


class LeaseKeeper:
    def __init__(self, lease: RunLease) -> None:
        self.lease = lease
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-{lease.run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=settings.GENERATION_HEARTBEAT_SECONDS + 2)

    def _run(self) -> None:
        close_old_connections()
        try:
            interval = settings.GENERATION_HEARTBEAT_SECONDS
            while not self._stop.wait(interval):
                if not renew_lease(self.lease):
                    self.lost.set()
                    return
        finally:
            connections["default"].close()
