import signal
import threading
import time
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import close_old_connections, connection, reset_queries

from proofgraph.generation.execution import process_claimed_run
from proofgraph.generation.queue import LeaseKeeper, claim_run
from proofgraph.generation.research_cache import delete_expired_cache_entries
from proofgraph.generation.telemetry import emit_telemetry


class Command(BaseCommand):
    help = "Run the PostgreSQL-backed durable generation worker."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one eligible run, then exit.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Idle PostgreSQL queue polling interval.",
        )
        parser.add_argument(
            "--worker-id", default=None, help="Stable worker identity for telemetry."
        )

    def handle(self, *_args: object, **options: object) -> None:
        connection.ensure_connection()
        self.stdout.write(self.style.SUCCESS("Generation worker connected to PostgreSQL."))

        stop_event = threading.Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)

        poll_interval = max(float(options["poll_interval"]), 0.1)
        worker_id = options["worker_id"] or f"worker-{uuid.uuid4()}"
        started = time.monotonic()
        next_cache_cleanup = started
        completed_jobs = 0
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= next_cache_cleanup:
                delete_expired_cache_entries()
                next_cache_cleanup = now + settings.GENERATION_CACHE_CLEANUP_SECONDS
            lease = claim_run(worker_id)
            if lease is None:
                if options["once"]:
                    break
                stop_event.wait(poll_interval)
                continue

            keeper = LeaseKeeper(lease)
            keeper.start()
            try:
                process_claimed_run(lease)
            finally:
                keeper.stop()
                completed_jobs += 1
                reset_queries()
                close_old_connections()

            if options["once"]:
                break
            elapsed = time.monotonic() - started
            if (
                completed_jobs >= settings.GENERATION_MAX_JOBS_PER_WORKER
                or elapsed >= settings.GENERATION_MAX_WORKER_LIFETIME_SECONDS
            ):
                emit_telemetry(
                    "worker.recycling",
                    worker_id=worker_id,
                    jobs=completed_jobs,
                    elapsed_seconds=elapsed,
                )
                break

        connection.close()
        self.stdout.write(f"Generation worker stopped after {completed_jobs} job(s).")
