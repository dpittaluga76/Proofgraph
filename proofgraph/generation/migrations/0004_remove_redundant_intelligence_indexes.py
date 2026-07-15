# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0003_intelligence_persistence"),
        ("graph", "0002_canvas_lifecycle_delete"),
    ]

    operations = [
        migrations.AlterField(
            model_name="researchquerycache",
            name="canvas",
            field=models.ForeignKey(
                db_constraint=False,
                db_index=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="research_query_cache_entries",
                to="graph.canvas",
            ),
        ),
        migrations.AlterField(
            model_name="sourcecontentcache",
            name="canvas",
            field=models.ForeignKey(
                db_constraint=False,
                db_index=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="source_content_cache_entries",
                to="graph.canvas",
            ),
        ),
        migrations.AlterField(
            model_name="sourceingestionrequest",
            name="canvas",
            field=models.ForeignKey(
                db_constraint=False,
                db_index=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="source_ingestion_requests",
                to="graph.canvas",
            ),
        ),
        migrations.AlterField(
            model_name="sourceingestionrequest",
            name="result_source_node",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="completed_source_ingestions",
                to="graph.node",
            ),
        ),
    ]
