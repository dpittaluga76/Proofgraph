from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Verify that the configured PostgreSQL database is reachable."

    def handle(self, *_args: object, **_options: object) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            database_name, database_user = cursor.fetchone()

        self.stdout.write(
            self.style.SUCCESS(f"PostgreSQL ready: database={database_name} user={database_user}")
        )
