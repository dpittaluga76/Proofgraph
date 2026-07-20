from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser

from proofgraph.evaluation.artifacts import write_json_atomic, write_text_atomic
from proofgraph.evaluation.schemas import (
    BlindPacket,
    EvaluationGenerationRun,
    ModelJudgeRatingArtifact,
    PrivateBlindMap,
)
from proofgraph.evaluation.scoring import (
    ACCEPTANCE_RULE_IDS,
    ACCEPTANCE_RULE_VERSIONS,
    analyze_ratings,
    render_markdown_report,
)


def _load(path: Path, model: type):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


class Command(BaseCommand):
    help = (
        "Validate two automated model-judge artifacts, average their scores, report "
        "disagreements, and score the benchmark."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--packet", type=Path, required=True)
        parser.add_argument("--private-map", type=Path, required=True)
        parser.add_argument("--generation", type=Path, required=True)
        parser.add_argument(
            "--judge-a",
            "--rater-a",
            dest="judge_a",
            type=Path,
            required=True,
            help="Vera rating artifact; --rater-a is a deprecated compatibility alias.",
        )
        parser.add_argument(
            "--judge-b",
            "--rater-b",
            dest="judge_b",
            type=Path,
            required=True,
            help="Marco rating artifact; --rater-b is a deprecated compatibility alias.",
        )
        parser.add_argument("--output-json", type=Path, required=True)
        parser.add_argument("--output-markdown", type=Path, required=True)
        parser.add_argument(
            "--acceptance-rule",
            choices=ACCEPTANCE_RULE_VERSIONS,
            default="v1",
            help=(
                "Acceptance protocol to apply. Defaults to frozen v1; select v2 explicitly "
                "for the pre-registered builder-fit ceiling correction."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        packet = _load(options["packet"], BlindPacket)
        private_map = _load(options["private_map"], PrivateBlindMap)
        generation = _load(options["generation"], EvaluationGenerationRun)
        judge_a = _load(options["judge_a"], ModelJudgeRatingArtifact)
        judge_b = _load(options["judge_b"], ModelJudgeRatingArtifact)
        report = analyze_ratings(
            packet,
            private_map,
            judge_a,
            judge_b,
            generation,
            acceptance_rule=options["acceptance_rule"],
        )
        write_json_atomic(options["output_json"], report)
        write_text_atomic(options["output_markdown"], render_markdown_report(report))
        status = "PASS" if report["acceptance_passed"] else "FAIL"
        self.stdout.write(self.style.SUCCESS(f"Evaluation result: {status}"))
        self.stdout.write(f"Acceptance rule: {ACCEPTANCE_RULE_IDS[options['acceptance_rule']]}")
        if not report["acceptance_passed"]:
            failed_dimensions = [
                dimension
                for dimension, item in report["dimensions"].items()
                if item["required"] and item["passes_required_threshold"] is False
            ]
            self.stdout.write(
                self.style.WARNING(f"Failed required dimensions: {', '.join(failed_dimensions)}")
            )
