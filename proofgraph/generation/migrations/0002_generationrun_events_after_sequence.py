# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("generation", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="generationrun",
            name="events_after_sequence",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddConstraint(
            model_name="generationrun",
            constraint=models.CheckConstraint(
                condition=models.Q(("events_after_sequence__gte", 0)),
                name="ck_run_events_after_sequence",
            ),
        ),
    ]
