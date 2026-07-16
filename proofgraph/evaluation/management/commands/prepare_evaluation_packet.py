from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser

from proofgraph.evaluation.artifacts import write_json_atomic
from proofgraph.evaluation.blinding import prepare_blind_packet
from proofgraph.evaluation.scenarios import DEFAULT_SCENARIO_PATH, load_scenarios
from proofgraph.evaluation.schemas import AdjudicationArtifact, EvaluationGenerationRun


class Command(BaseCommand):
    help = (
        "Create the automated-judge blind packet and separate private map. Legacy empty human "
        "rating and adjudication templates are retained for artifact compatibility."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIO_PATH)
        parser.add_argument("--generation", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True)
        parser.add_argument("--seed", type=int, default=27_002)

    def handle(self, *args: object, **options: object) -> None:
        scenario_set = load_scenarios(options["scenarios"])
        run_path: Path = options["generation"]
        run = EvaluationGenerationRun.model_validate_json(run_path.read_text(encoding="utf-8"))
        packet, private_map, rater_a, rater_b = prepare_blind_packet(
            scenario_set,
            run,
            seed=options["seed"],
        )
        output_dir: Path = options["output_dir"]
        write_json_atomic(output_dir / "blind-packet.json", packet.model_dump(mode="json"))
        write_json_atomic(
            output_dir / "private-variant-map.json", private_map.model_dump(mode="json")
        )
        write_json_atomic(output_dir / "rating-rater-a.json", rater_a.model_dump(mode="json"))
        write_json_atomic(output_dir / "rating-rater-b.json", rater_b.model_dump(mode="json"))
        adjudications = AdjudicationArtifact(
            packet_id=packet.packet_id,
            adjudications=[],
        )
        write_json_atomic(
            output_dir / "adjudications.json",
            adjudications.model_dump(mode="json"),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Prepared {len(private_map.mappings)} blinded outputs in {output_dir}. "
                "Automated judges receive only blind-packet.json; never pass them "
                "private-variant-map.json, generation metadata, or peer scores."
            )
        )
