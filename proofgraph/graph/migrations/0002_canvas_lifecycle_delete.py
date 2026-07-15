# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

import django.db.models.deletion
from django.db import migrations, models

FORWARD_SQL = """
DO $$
DECLARE
    table_name text;
    constraint_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'node', 'edge', 'graph_operation', 'node_staleness_cause'
    ] LOOP
        SELECT constraint_record.conname
        INTO constraint_name
        FROM pg_constraint AS constraint_record
        JOIN pg_attribute AS column_record
          ON column_record.attrelid = constraint_record.conrelid
         AND column_record.attnum = constraint_record.conkey[1]
        WHERE constraint_record.conrelid = table_name::regclass
          AND constraint_record.confrelid = 'canvas'::regclass
          AND constraint_record.contype = 'f'
          AND array_length(constraint_record.conkey, 1) = 1
          AND column_record.attname = 'canvas_id';

        IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'canvas foreign key not found for table %', table_name;
        END IF;

        EXECUTE format(
            'ALTER TABLE %I DROP CONSTRAINT %I',
            table_name,
            constraint_name
        );
    END LOOP;
END;
$$;

ALTER TABLE node
    ADD CONSTRAINT fk_node_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE edge
    ADD CONSTRAINT fk_edge_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE graph_operation
    ADD CONSTRAINT fk_graph_op_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE node_staleness_cause
    ADD CONSTRAINT fk_stale_cause_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
"""


REVERSE_SQL = """
ALTER TABLE node DROP CONSTRAINT fk_node_canvas_lifecycle;
ALTER TABLE edge DROP CONSTRAINT fk_edge_canvas_lifecycle;
ALTER TABLE graph_operation DROP CONSTRAINT fk_graph_op_canvas_lifecycle;
ALTER TABLE node_staleness_cause DROP CONSTRAINT fk_stale_cause_canvas_lifecycle;

ALTER TABLE node
    ADD CONSTRAINT fk_node_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE edge
    ADD CONSTRAINT fk_edge_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE graph_operation
    ADD CONSTRAINT fk_graph_op_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE node_staleness_cause
    ADD CONSTRAINT fk_stale_cause_canvas_lifecycle
    FOREIGN KEY (canvas_id) REFERENCES canvas(id)
    DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):
    dependencies = [("graph", "0001_initial")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)],
            state_operations=[
                migrations.AlterField(
                    model_name="node",
                    name="canvas",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="nodes",
                        to="graph.canvas",
                    ),
                ),
                migrations.AlterField(
                    model_name="edge",
                    name="canvas",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="edges",
                        to="graph.canvas",
                    ),
                ),
                migrations.AlterField(
                    model_name="graphoperation",
                    name="canvas",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="operations",
                        to="graph.canvas",
                    ),
                ),
                migrations.AlterField(
                    model_name="nodestalenesscause",
                    name="canvas",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="node_staleness_causes",
                        to="graph.canvas",
                    ),
                ),
            ],
        )
    ]
