from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from openai import OpenAI

from proofgraph.evaluation.artifacts import write_json_atomic
from proofgraph.evaluation.generation import EvaluationProviderError
from proofgraph.evaluation.judging import (
    JUDGE_DEFAULT_WORKERS,
    JUDGE_MAX_WORKERS,
    JudgeArtifactError,
    JudgeResponseError,
    OpenAIModelJudge,
    materialize_rating_artifacts,
    run_judging,
)
from proofgraph.evaluation.schemas import EVALUATION_MODELS, BlindPacket


class Command(BaseCommand):
    help = (
        "Run or resume the two cost-bearing automated blind judges, then materialize their "
        "validated rating artifacts. The private judge-run artifact contains provider metadata."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--packet", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True)
        parser.add_argument("--seed", type=int, default=27_003)
        parser.add_argument(
            "--judge-a-model",
            required=True,
            choices=EVALUATION_MODELS,
            help="Model for Vera Crosscheck — Evidence Auditor.",
        )
        parser.add_argument(
            "--judge-b-model",
            required=True,
            choices=EVALUATION_MODELS,
            help="Model for Marco Launch — Bootstrap Operator.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            choices=range(1, JUDGE_MAX_WORKERS + 1),
            default=JUDGE_DEFAULT_WORKERS,
            help=(
                "Concurrent provider calls. Six is the default; reduce this after repeated "
                "rate-limit errors. Worker count does not change the frozen judge-run identity."
            ),
        )
        parser.add_argument(
            "--confirm-cost",
            action="store_true",
            help="Required acknowledgement that this command makes 40 provider calls.",
        )

    def handle(self, *args: object, **options: object) -> None:
        if not options["confirm_cost"]:
            raise CommandError(
                "Refusing the paid automated judge run without --confirm-cost (40 API calls)."
            )
        if not settings.OPENAI_API_KEY:
            raise CommandError("OPENAI_API_KEY is required for automated evaluation judging.")

        packet_path: Path = options["packet"]
        packet = BlindPacket.model_validate_json(packet_path.read_text(encoding="utf-8"))
        output_dir: Path = options["output_dir"]
        private_run_path = output_dir / "private-judge-run.json"
        client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=2, timeout=120.0)
        evaluator = OpenAIModelJudge(client)
        self.stdout.write(
            f"Judging with {options['workers']} concurrent provider workers; completed "
            "judge/scenario checkpoints will be resumed."
        )
        try:
            run = run_judging(
                packet,
                evaluator,
                private_run_path,
                seed=options["seed"],
                judge_a_model=options["judge_a_model"],
                judge_b_model=options["judge_b_model"],
                workers=options["workers"],
            )
            judge_a, judge_b = materialize_rating_artifacts(packet, run)
        except (JudgeArtifactError, JudgeResponseError, EvaluationProviderError) as error:
            raise CommandError(str(error)) from error

        write_json_atomic(output_dir / "rating-judge-a.json", judge_a.model_dump(mode="json"))
        write_json_atomic(output_dir / "rating-judge-b.json", judge_b.model_dump(mode="json"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Completed {len(run.results)} / {len(packet.scenarios) * 2} automated "
                f"judge calls and materialized 80 ratings per judge in {output_dir}."
            )
        )
