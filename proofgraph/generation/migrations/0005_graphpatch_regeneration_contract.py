# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0004_remove_redundant_intelligence_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="graphpatch",
            name="permitted_stale_resolution_ids",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="graphpatch",
            name="regeneration_target_ids",
            field=models.JSONField(default=list),
        ),
    ]
