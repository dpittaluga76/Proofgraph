from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from statistics import fmean
from typing import Any

_IDENTIFIER_FIELDS = (
    "run_id",
    "canvas_id",
    "demo_session_id",
    "patch_id",
    "original_run_id",
    "ingestion_id",
    "graph_operation_id",
)
_DEMO_EVENTS = (
    "demo.session_created",
    "demo.session_expired",
    "demo.session_cleaned",
    "demo.cleanup_waiting_for_fence",
    "demo.reset",
    "demo.profile_rejected",
    "demo.concurrent_quota_rejected",
    "demo.session_quota_rejected",
    "demo.global_quota_rejected",
    "demo.circuit_breaker_open",
    "demo.replay_selected",
)
_PATCH_REGENERATION_EVENTS = (
    "regeneration.started",
    "patch.regeneration_requested",
    "patch.regeneration_linked",
    "patch.regeneration_replayed",
    "patch.regeneration_conflict",
    "patch.regeneration_terminal",
)


def parse_telemetry_lines(
    lines: Iterable[str],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            if strict:
                raise ValueError(f"telemetry line {line_number} is not valid JSON") from error
            continue
        if not isinstance(value, dict) or not isinstance(value.get("event"), str):
            if strict:
                raise ValueError(f"telemetry line {line_number} must be an object with an event")
            continue
        records.append(value)
    return records


def _event_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(record["event"]) for record in records).items()))


def _number(record: Mapping[str, Any], field: str) -> float | None:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _numeric_summary(values: Iterable[float]) -> dict[str, int | float | None]:
    retained = list(values)
    if not retained:
        return {"count": 0, "average": None, "maximum": None}
    return {
        "count": len(retained),
        "average": round(fmean(retained), 3),
        "maximum": round(max(retained), 3),
    }


def _duration_by_stage(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    durations: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.get("event") != "stage.completed":
            continue
        duration = _number(record, "duration_ms")
        if duration is not None:
            durations[str(record.get("stage") or "unknown")].append(duration)
    return {stage: _numeric_summary(values) for stage, values in sorted(durations.items())}


def _provider_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    calls: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    latency: dict[str, list[float]] = defaultdict(list)
    tokens: Counter[str] = Counter()
    response_ids = 0
    for record in records:
        event = str(record.get("event"))
        if event not in {
            "provider.structured_output",
            "provider.failure",
            "research.provider",
            "research.provider_failure",
        }:
            continue
        provider = str(record.get("provider") or record.get("provider_identity") or "unknown")
        if event.endswith("failure"):
            failures[provider] += 1
        else:
            calls[provider] += 1
        duration = _number(record, "latency_ms")
        if duration is not None:
            latency[provider].append(duration)
        usage = record.get("token_usage")
        if isinstance(usage, Mapping):
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    tokens[field] += value
        if record.get("model_response_id"):
            response_ids += 1
    providers = sorted(set(calls) | set(failures) | set(latency))
    return {
        "calls": sum(calls.values()),
        "failures": sum(failures.values()),
        "response_ids": response_ids,
        "token_usage": dict(sorted(tokens.items())),
        "by_provider": {
            provider: {
                "calls": calls[provider],
                "failures": failures[provider],
                "latency_ms": _numeric_summary(latency[provider]),
            }
            for provider in providers
        },
    }


def aggregate_observability(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    events = _event_counts(records)
    queue_depths = [
        depth
        for record in records
        if record.get("event") == "queue.depth" and (depth := _number(record, "depth")) is not None
    ]
    completed_run_durations = [
        duration
        for record in records
        if record.get("event") == "run.completed"
        and (duration := _number(record, "duration_ms")) is not None
    ]
    patch_ratios = [
        ratio
        for record in records
        if record.get("event") in {"patch.applied", "patch.apply_replayed"}
        and (ratio := _number(record, "accepted_operation_ratio")) is not None
    ]
    independent_support = [
        float(value)
        for record in records
        if record.get("event") == "evidence.clustered"
        for value in record.get("independent_source_counts", [])
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    cache_records = [
        record
        for record in records
        if record.get("event") in {"research_cache.query", "research_cache.source"}
    ]
    cache_outcomes = Counter(str(record.get("outcome") or "unknown") for record in cache_records)
    cache_reasons = Counter(
        str(record["invalidation_reason"])
        for record in cache_records
        if record.get("invalidation_reason")
    )
    reservation_outcomes = Counter(
        str(record.get("outcome") or "unknown")
        for record in records
        if record.get("event") == "source_ingestion.reservation"
    )
    demo_counts = {event: events.get(event, 0) for event in _DEMO_EVENTS}
    regeneration_counts = {event: events.get(event, 0) for event in _PATCH_REGENERATION_EVENTS}
    return {
        "event_count": len(records),
        "events": events,
        "queue": {
            "queued": events.get("run.queued", 0),
            "claims": events.get("run.claimed", 0),
            "reclaims": sum(
                record.get("event") == "run.claimed" and record.get("reclaimed") is True
                for record in records
            ),
            "depth": {
                **_numeric_summary(queue_depths),
                "latest": queue_depths[-1] if queue_depths else None,
            },
        },
        "stages": {
            "started": events.get("stage.started", 0),
            "completed": events.get("stage.completed", 0),
            "reused": events.get("stage.reused", 0),
            "duration_ms_by_stage": _duration_by_stage(records),
        },
        "failure_retry": {
            "failed": events.get("run.failed", 0),
            "retryable_failures": sum(
                record.get("event") == "run.failed" and record.get("retryable") is True
                for record in records
            ),
            "retry_requests": events.get("run.retry_requested", 0),
            "attempts_exhausted": sum(
                record.get("event") == "run.poisoned"
                or (
                    record.get("event") == "run.failed"
                    and record.get("code") == "attempts_exhausted"
                )
                for record in records
            ),
            "cancel_requests": events.get("run.cancel_requested", 0),
            "cancelled": events.get("run.cancelled", 0),
            "run_duration_ms": _numeric_summary(completed_run_durations),
        },
        "leases": {
            "claims": events.get("run.claimed", 0),
            "heartbeats": events.get("run.heartbeat", 0),
            "lost": events.get("run.lease_lost", 0),
            "patch_ready_recoveries": events.get("run.patch_ready_recovered", 0),
        },
        "providers": _provider_metrics(records),
        "patches": {
            "ready": events.get("patch.ready", 0),
            "applied": events.get("patch.applied", 0),
            "rejected": events.get("patch.rejected", 0),
            "conflicts": events.get("patch.apply_conflict", 0)
            + events.get("patch.regeneration_conflict", 0),
            "accepted_operation_ratio": _numeric_summary(patch_ratios),
            "regeneration": regeneration_counts,
        },
        "evidence_quality": {
            "retention_batches": events.get("extraction.retained", 0),
            "input_claims": sum(
                int(record.get("input_count") or 0)
                for record in records
                if record.get("event") == "extraction.retained"
            ),
            "retained_claims": sum(
                int(record.get("retained_count") or 0)
                for record in records
                if record.get("event") == "extraction.retained"
            ),
            "rejected_claims": sum(
                int(record.get("rejected_count") or 0)
                for record in records
                if record.get("event") == "extraction.retained"
            ),
            "clusters": sum(
                int(record.get("cluster_count") or 0)
                for record in records
                if record.get("event") == "evidence.clustered"
            ),
            "independent_support": _numeric_summary(independent_support),
        },
        "cache": {
            "outcomes": dict(sorted(cache_outcomes.items())),
            "invalidation_reasons": dict(sorted(cache_reasons.items())),
        },
        "source_ingestion": {
            "reservations": dict(sorted(reservation_outcomes.items())),
            "reclaims": reservation_outcomes["reclaimed"],
            "fence_losses": events.get("source_ingestion.fence_lost", 0),
            "completed": events.get("source_ingestion.completed", 0),
            "failed": events.get("source_ingestion.failed", 0),
        },
        "demo": demo_counts,
    }


def _identifier_values(record: Mapping[str, Any]) -> set[str]:
    return {str(record[field]) for field in _IDENTIFIER_FIELDS if record.get(field) is not None}


def telemetry_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        event = str(record.get("event") or "")
        required = {"timestamp", "component", "event"}
        if event.startswith("run."):
            required.update({"run_id", "canvas_id"})
        if event.startswith("stage."):
            required.update(
                {
                    "run_id",
                    "canvas_id",
                    "worker_id",
                    "lease_epoch",
                    "attempt",
                    "stage",
                }
            )
        if event in {
            "provider.structured_output",
            "provider.failure",
            "research.provider",
            "research.provider_failure",
        }:
            required.update({"provider", "latency_ms"})
        if event.startswith("patch."):
            required.add("patch_id")
        if event.startswith("source_ingestion."):
            required.update({"ingestion_id", "canvas_id", "operation_key"})
        if event.startswith("demo."):
            required.add("demo_session_id")
        if event.startswith("graph."):
            required.update({"canvas_id", "graph_operation_id"})
        missing = sorted(field for field in required if record.get(field) is None)
        if missing:
            issues.append({"index": index, "event": event, "missing": missing})
    return {
        "passed": not issues,
        "record_count": len(records),
        "issue_count": len(issues),
        "issues": issues,
    }


def correlated_records(
    records: Sequence[Mapping[str, Any]],
    *identifiers: Any,
) -> list[dict[str, Any]]:
    discovered = {str(value) for value in identifiers if value is not None}
    correlated: list[dict[str, Any]] = []
    previous_size = -1
    while previous_size != len(discovered):
        previous_size = len(discovered)
        correlated = [dict(record) for record in records if _identifier_values(record) & discovered]
        for record in correlated:
            discovered.update(_identifier_values(record))
    return correlated


def build_audit_snapshot(
    *,
    run_ids: Sequence[Any] = (),
    patch_ids: Sequence[Any] = (),
    include_payloads: bool = False,
) -> dict[str, Any]:
    from django.db.models import Q

    from proofgraph.generation.models import GenerationRun, GraphPatch
    from proofgraph.graph.models import GraphOperation

    run_query = Q()
    if run_ids:
        run_query |= Q(id__in=run_ids)
    if patch_ids:
        run_query |= Q(patch__id__in=patch_ids)
    run_queryset = (
        GenerationRun.objects.filter(run_query)
        if run_ids or patch_ids
        else (GenerationRun.objects.none())
    )
    runs = list(
        run_queryset.select_related("canvas")
        .prefetch_related("stages", "events", "patch__decisions")
        .order_by("created_at", "id")
    )
    canvas_ids = sorted({run.canvas_id for run in runs}, key=str)
    patches = list(
        GraphPatch.objects.filter(Q(run_id__in=[run.id for run in runs]) | Q(id__in=patch_ids))
        .prefetch_related("decisions")
        .order_by("created_at", "id")
    )
    graph_operations = list(
        GraphOperation.objects.filter(canvas_id__in=canvas_ids).order_by("canvas_revision", "id")
    )
    stage_names = {stage.name for run in runs for stage in run.stages.all()}
    decisions = [decision for patch in patches for decision in patch.decisions.all()]
    configuration_coverage = [
        all(
            configuration.get(field)
            for field in ("prompt_version", "strategy_version", "provider_identity")
        )
        for run in runs
        if isinstance((configuration := run.execution_configuration), dict)
    ]
    coverage = {
        "prompt_strategy_model": bool(configuration_coverage) and all(configuration_coverage),
        "context": bool(runs)
        and all(bool(run.context_snapshot) and bool(run.context_manifest) for run in runs),
        "sources": "researching" in stage_names,
        "claims": "extracting" in stage_names,
        "candidates": bool({"planning", "synthesizing"} & stage_names),
        "critiques": "critiquing" in stage_names,
        "accepted_operations": any(decision.decision == "accepted" for decision in decisions),
        "rejected_operations": any(
            decision.decision in {"rejected", "skipped_conflict"} for decision in decisions
        ),
        "user_edits": any(
            operation.actor_type not in {"graph_patch", "source_ingestion"}
            for operation in graph_operations
        ),
    }
    run_records = []
    for run in runs:
        record: dict[str, Any] = {
            "run_id": str(run.id),
            "canvas_id": str(run.canvas_id),
            "demo_session_id": (
                str(run.demo_session_id) if run.demo_session_id is not None else None
            ),
            "operation": run.operation,
            "idempotency_key": run.idempotency_key,
            "status": run.status,
            "attempt": run.attempt,
            "execution_configuration": run.execution_configuration,
            "context_hash": run.context_hash,
            "stage_names": [stage.name for stage in run.stages.all()],
            "event_types": [event.event_type for event in run.events.all()],
        }
        if include_payloads:
            record.update(
                {
                    "context_snapshot": run.context_snapshot,
                    "context_manifest": run.context_manifest,
                    "stages": [
                        {
                            "name": stage.name,
                            "status": stage.status,
                            "attempt": stage.attempt,
                            "model_response_id": stage.openai_response_id,
                            "output": stage.output,
                            "error": stage.error,
                        }
                        for stage in run.stages.all()
                    ],
                    "events": [
                        {
                            "event_type": event.event_type,
                            "payload": event.payload,
                            "canvas_sequence": event.canvas_sequence,
                            "run_sequence": event.run_sequence,
                        }
                        for event in run.events.all()
                    ],
                }
            )
        run_records.append(record)
    patch_records = []
    for patch in patches:
        record = {
            "patch_id": str(patch.id),
            "run_id": str(patch.run_id),
            "status": patch.status,
            "operation_count": len(patch.operations),
            "decisions": [
                {
                    "operation_index": decision.operation_index,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "graph_operation_id": decision.graph_operation_id,
                }
                for decision in patch.decisions.all()
            ],
        }
        if include_payloads:
            record["operations"] = patch.operations
        patch_records.append(record)
    operation_records = [
        {
            "graph_operation_id": operation.id,
            "canvas_id": str(operation.canvas_id),
            "actor_type": operation.actor_type,
            "actor_id": operation.actor_id,
            "operation_key": operation.operation_key,
            "operation_type": operation.operation_type,
            "canvas_revision": operation.canvas_revision,
            **(
                {
                    "payload": operation.payload,
                    "result_payload": operation.result_payload,
                }
                if include_payloads
                else {}
            ),
        }
        for operation in graph_operations
    ]
    return {
        "coverage": coverage,
        "runs": run_records,
        "patches": patch_records,
        "graph_operations": operation_records,
    }


def _scenario_record(
    records: Sequence[Mapping[str, Any]],
    scenario: str,
) -> Mapping[str, Any] | None:
    for record in records:
        event = record.get("event")
        if scenario == "successful_run" and event == "run.completed":
            return record
        if (
            scenario == "retryable_provider_failure"
            and event == "run.failed"
            and record.get("retryable") is True
            and any(
                marker in str(record.get("code") or "").casefold()
                for marker in ("provider", "openai", "timeout", "rate_limit")
            )
        ):
            return record
        if scenario == "lease_loss" and event == "run.lease_lost":
            return record
        if scenario == "patch_conflict" and event in {
            "patch.apply_conflict",
            "patch.regeneration_conflict",
        }:
            return record
    return None


def build_diagnostic_drill(
    records: Sequence[Mapping[str, Any]],
    *,
    include_audit_payloads: bool = False,
) -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    for scenario in (
        "successful_run",
        "retryable_provider_failure",
        "lease_loss",
        "patch_conflict",
    ):
        signal = _scenario_record(records, scenario)
        if signal is None:
            scenarios[scenario] = {"present": False}
            continue
        identifiers = [
            signal.get(field)
            for field in ("run_id", "patch_id", "canvas_id")
            if signal.get(field) is not None
        ]
        logs = correlated_records(records, *identifiers)
        audit = build_audit_snapshot(
            run_ids=[signal["run_id"]] if signal.get("run_id") else [],
            patch_ids=[signal["patch_id"]] if signal.get("patch_id") else [],
            include_payloads=include_audit_payloads,
        )
        event_types = sorted(
            {event_type for run in audit["runs"] for event_type in run["event_types"]}
        )
        persistent_identity = bool(audit["runs"]) and (
            scenario != "patch_conflict" or bool(audit["patches"])
        )
        scenarios[scenario] = {
            "present": True,
            "signal": dict(signal),
            "correlated_log_count": len(logs),
            "log_events": sorted({str(record["event"]) for record in logs}),
            "metrics": aggregate_observability(logs),
            "persistent_event_types": event_types,
            "audit": audit,
            "correlated": bool(logs) and persistent_identity,
        }
    return {
        "passed": all(
            scenario.get("present") is True and scenario.get("correlated") is True
            for scenario in scenarios.values()
        ),
        "scenarios": scenarios,
    }


__all__ = [
    "aggregate_observability",
    "build_audit_snapshot",
    "build_diagnostic_drill",
    "correlated_records",
    "parse_telemetry_lines",
    "telemetry_quality",
]
