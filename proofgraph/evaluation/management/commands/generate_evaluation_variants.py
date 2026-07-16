from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from openai import OpenAI

from proofgraph.evaluation.generation import (
    EVALUATION_DEFAULT_WORKERS,
    EVALUATION_MAX_WORKERS,
    EVALUATION_MODEL,
    EVALUATION_MODELS,
    EvaluationArtifactError,
    EvaluationProviderError,
    OpenAIEvaluationGenerator,
    run_generation,
)
from proofgraph.evaluation.scenarios import DEFAULT_SCENARIO_PATH, load_scenarios


class Command(BaseCommand):
    help = (
        "Run or resume the cost-bearing four-variant GPT-5.6-family evaluation generation. "
        "The private output artifact contains provider response IDs and variant labels."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIO_PATH)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--seed", type=int, default=27_001)
        parser.add_argument(
            "--model",
            required=True,
            choices=EVALUATION_MODELS,
            help=(
                "Freeze one allowed GPT-5.6 family model for every variant in this run. "
                f"The internal library default is {EVALUATION_MODEL}."
            ),
        )
        parser.add_argument(
            "--confirm-cost",
            action="store_true",
            help="Required acknowledgement that this command makes about 200 provider calls.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            choices=range(1, EVALUATION_MAX_WORKERS + 1),
            default=EVALUATION_DEFAULT_WORKERS,
            help=(
                "Concurrent provider calls. Six is the balanced default; reduce this after "
                "repeated rate-limit errors. This does not change the frozen run identity."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        if not options["confirm_cost"]:
            raise CommandError(
                "Refusing the paid benchmark run without --confirm-cost (about 200 API calls)."
            )
        if not settings.OPENAI_API_KEY:
            raise CommandError("OPENAI_API_KEY is required for evaluation generation.")
        scenario_set = load_scenarios(options["scenarios"])
        client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=2, timeout=120.0)
        generator = OpenAIEvaluationGenerator(client, model=options["model"])
        self.stdout.write(
            f"Generating with {options['workers']} concurrent provider workers; "
            "saved stages will be resumed."
        )
        try:
            run = run_generation(
                scenario_set,
                generator,
                options["output"],
                seed=options["seed"],
                workers=options["workers"],
            )
        except (EvaluationArtifactError, EvaluationProviderError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Generation artifact has {len(run.outputs)} / "
                f"{len(scenario_set.scenarios) * 4} outputs at {options['output']}."
            )
        )
