from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from openai import OpenAI

from proofgraph.evaluation.generation import (
    EVALUATION_MODEL,
    OpenAIEvaluationGenerator,
    run_generation,
)
from proofgraph.evaluation.scenarios import DEFAULT_SCENARIO_PATH, load_scenarios


class Command(BaseCommand):
    help = (
        "Run or resume the cost-bearing four-variant GPT-5.6 evaluation generation. "
        "The private output artifact contains provider response IDs and variant labels."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIO_PATH)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--seed", type=int, default=27_001)
        parser.add_argument("--model", default=EVALUATION_MODEL, choices=[EVALUATION_MODEL])
        parser.add_argument(
            "--confirm-cost",
            action="store_true",
            help="Required acknowledgement that this command makes about 200 provider calls.",
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
        run = run_generation(
            scenario_set,
            generator,
            options["output"],
            seed=options["seed"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Generation artifact has {len(run.outputs)} / "
                f"{len(scenario_set.scenarios) * 4} outputs at {options['output']}."
            )
        )
