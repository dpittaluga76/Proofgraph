# ruff: noqa: RUF012 - Django migrations use declarative mutable class attributes.

from django.db import migrations
from django.utils import timezone


def _canonical_string_list(value):  # type: ignore[no-untyped-def]
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    canonical = sorted(set(value))
    return canonical if value == canonical else None


def backfill_regeneration_contracts(apps, schema_editor):  # type: ignore[no-untyped-def]
    GenerationStage = apps.get_model("generation", "GenerationStage")
    GraphPatch = apps.get_model("generation", "GraphPatch")
    database = schema_editor.connection.alias

    patches = GraphPatch.objects.using(database).filter(run__operation="regenerate_stale")
    for patch in patches.iterator():
        stage = (
            GenerationStage.objects.using(database)
            .filter(
                run_id=patch.run_id,
                name="constructing_patch",
                status="completed",
            )
            .order_by("-completed_at", "id")
            .first()
        )
        envelope = stage.output if stage is not None and isinstance(stage.output, dict) else {}
        output = envelope.get("output") if isinstance(envelope.get("output"), dict) else {}
        targets = _canonical_string_list(output.get("regeneration_target_ids"))
        permitted = _canonical_string_list(
            output.get(
                "permitted_stale_resolution_ids",
                output.get("resolves_stale_node_ids"),
            )
        )
        if targets is not None and permitted is not None:
            patch.regeneration_target_ids = targets
            patch.permitted_stale_resolution_ids = permitted
            patch.save(
                update_fields=[
                    "regeneration_target_ids",
                    "permitted_stale_resolution_ids",
                ]
            )
            continue
        if patch.status == "pending":
            patch.status = "rejected"
            patch.decided_at = patch.decided_at or timezone.now()
            patch.save(update_fields=["status", "decided_at"])


PATCH_CONTRACT_SQL = r"""
ALTER TABLE graph_patch
    ADD CONSTRAINT ck_patch_operations_array
    CHECK (jsonb_typeof(operations) = 'array');

ALTER TABLE graph_patch
    ADD CONSTRAINT ck_patch_regeneration_targets_array
    CHECK (jsonb_typeof(regeneration_target_ids) = 'array');

ALTER TABLE graph_patch
    ADD CONSTRAINT ck_patch_permitted_resolution_array
    CHECK (jsonb_typeof(permitted_stale_resolution_ids) = 'array');

ALTER TABLE graph_patch
    ADD CONSTRAINT ck_patch_client_id_map_object
    CHECK (jsonb_typeof(client_id_map) = 'object');

CREATE OR REPLACE FUNCTION protect_graph_patch_contract()
RETURNS trigger AS $function$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.run_id IS DISTINCT FROM NEW.run_id
       OR OLD.canvas_id IS DISTINCT FROM NEW.canvas_id
       OR OLD.base_canvas_revision IS DISTINCT FROM NEW.base_canvas_revision
       OR OLD.operations IS DISTINCT FROM NEW.operations
       OR OLD.regeneration_target_ids IS DISTINCT FROM NEW.regeneration_target_ids
       OR OLD.permitted_stale_resolution_ids IS DISTINCT FROM NEW.permitted_stale_resolution_ids
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION 'graph patch candidate contract is immutable';
    END IF;
    RETURN NEW;
END;
$function$ LANGUAGE plpgsql;

CREATE TRIGGER graph_patch_contract_immutable_trigger
BEFORE UPDATE ON graph_patch
FOR EACH ROW EXECUTE FUNCTION protect_graph_patch_contract();
"""

PATCH_CONTRACT_REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS graph_patch_contract_immutable_trigger ON graph_patch;
DROP FUNCTION IF EXISTS protect_graph_patch_contract();
ALTER TABLE graph_patch DROP CONSTRAINT IF EXISTS ck_patch_client_id_map_object;
ALTER TABLE graph_patch DROP CONSTRAINT IF EXISTS ck_patch_permitted_resolution_array;
ALTER TABLE graph_patch DROP CONSTRAINT IF EXISTS ck_patch_regeneration_targets_array;
ALTER TABLE graph_patch DROP CONSTRAINT IF EXISTS ck_patch_operations_array;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0005_graphpatch_regeneration_contract"),
    ]

    operations = [
        migrations.RunPython(backfill_regeneration_contracts, migrations.RunPython.noop),
        migrations.RunSQL(PATCH_CONTRACT_SQL, PATCH_CONTRACT_REVERSE_SQL),
    ]
