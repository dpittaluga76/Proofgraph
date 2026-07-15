from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from proofgraph.demo.cleanup import cleanup_expired_demo_sessions


class Command(BaseCommand):
    help = "Clean a bounded batch of expired, fenced demo sessions."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=settings.DEMO_CLEANUP_BATCH_SIZE,
            help="Maximum expired sessions to inspect in this run.",
        )

    def handle(self, *_args: object, **options: object) -> None:
        cleaned = cleanup_expired_demo_sessions(options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Cleaned {cleaned} expired demo session(s)."))
