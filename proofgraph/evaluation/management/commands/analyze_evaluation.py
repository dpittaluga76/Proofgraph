from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser

from proofgraph.evaluation.artifacts import write_json_atomic, write_text_atomic
from proofgraph.evaluation.schemas import (
    AdjudicationArtifact,
    BlindPacket,
    EvaluationGenerationRun,
    PrivateBlindMap,
    RatingArtifact,
)
from proofgraph.evaluation.scoring import analyze_ratings, render_markdown_report


def _load(path: Path, model: type):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


class Command(BaseCommand):
    help = "Validate two completed ratings, apply exact adjudications, and score the benchmark."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--packet", type=Path, required=True)
        parser.add_argument("--private-map", type=Path, required=True)
        parser.add_argument("--generation", type=Path, required=True)
        parser.add_argument("--rater-a", type=Path, required=True)
        parser.add_argument("--rater-b", type=Path, required=True)
        parser.add_argument("--adjudications", type=Path, required=True)
        parser.add_argument("--output-json", type=Path, required=True)
        parser.add_argument("--output-markdown", type=Path, required=True)

    def handle(self, *args: object, **options: object) -> None:
        packet = _load(options["packet"], BlindPacket)
        private_map = _load(options["private_map"], PrivateBlindMap)
        generation = _load(options["generation"], EvaluationGenerationRun)
        rater_a = _load(options["rater_a"], RatingArtifact)
        rater_b = _load(options["rater_b"], RatingArtifact)
        adjudications = _load(options["adjudications"], AdjudicationArtifact)
        report = analyze_ratings(
            packet,
            private_map,
            rater_a,
            rater_b,
            adjudications,
            generation,
        )
        write_json_atomic(options["output_json"], report)
        write_text_atomic(options["output_markdown"], render_markdown_report(report))
        status = "PASS" if report["acceptance_passed"] else "FAIL"
        self.stdout.write(self.style.SUCCESS(f"Evaluation result: {status}"))
