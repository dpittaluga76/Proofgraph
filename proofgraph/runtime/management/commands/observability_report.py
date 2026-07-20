from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from proofgraph.runtime.observability import (
    aggregate_observability,
    build_diagnostic_drill,
    parse_telemetry_lines,
    telemetry_quality,
)


class Command(BaseCommand):
    help = "Aggregate ProofGraph JSONL telemetry and optionally verify the PG-028 drill."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--input",
            default="-",
            help="JSONL telemetry path, or '-' to read standard input.",
        )
        parser.add_argument(
            "--output",
            default="-",
            help="Report path, or '-' to write standard output.",
        )
        parser.add_argument(
            "--require-drill",
            action="store_true",
            help="Require success, retryable provider failure, lease loss, and patch conflict.",
        )
        parser.add_argument(
            "--include-audit-payloads",
            action="store_true",
            help=(
                "Include persisted contexts, stage outputs, events, patches, "
                "and operation payloads."
            ),
        )

    def handle(self, *_args: object, **options: Any) -> None:
        input_path = str(options["input"])
        try:
            if input_path == "-":
                records = parse_telemetry_lines(sys.stdin)
            else:
                with Path(input_path).open(encoding="utf-8") as source:
                    records = parse_telemetry_lines(source)
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from error
        report: dict[str, Any] = {
            "metrics": aggregate_observability(records),
            "telemetry_quality": telemetry_quality(records),
        }
        if options["require_drill"] or options["include_audit_payloads"]:
            report["diagnostic_drill"] = build_diagnostic_drill(
                records,
                include_audit_payloads=bool(options["include_audit_payloads"]),
            )
        serialized = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
        output_path = str(options["output"])
        if output_path == "-":
            self.stdout.write(serialized, ending="")
        else:
            try:
                Path(output_path).write_text(serialized, encoding="utf-8")
            except OSError as error:
                raise CommandError(str(error)) from error
        drill = report.get("diagnostic_drill")
        if options["require_drill"]:
            if not isinstance(drill, dict) or drill.get("passed") is not True:
                raise CommandError(
                    "PG-028 diagnostic drill did not contain all correlated scenarios."
                )
            if report["telemetry_quality"]["passed"] is not True:
                raise CommandError("PG-028 telemetry records are missing required fields.")
