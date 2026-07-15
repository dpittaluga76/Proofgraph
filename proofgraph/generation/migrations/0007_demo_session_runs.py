# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("demo", "0001_initial"),
        ("generation", "0006_protect_graph_patch_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationrun",
            name="demo_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="generation_runs",
                to="demo.demosession",
            ),
        ),
        migrations.AddIndex(
            model_name="generationrun",
            index=models.Index(
                condition=Q(status__in=["queued", "running"]),
                fields=["demo_session", "status"],
                name="run_demo_active_idx",
            ),
        ),
    ]
