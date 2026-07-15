from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from proofgraph.generation.models import ResearchQueryCache, SourceContentCache
from proofgraph.generation.retention import validate_retained_payload
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.graph.models import Canvas

DEFAULT_FRESH_SECONDS = 60 * 60
MAX_CACHE_SECONDS = 24 * 60 * 60


def normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


class ResearchCacheStore:
    def get_query(
        self,
        *,
        canvas: Canvas,
        query: str,
        provider_identity: str,
        strategy_version: str,
        prompt_version: str,
        context_hash: str,
    ) -> dict[str, Any] | None:
        now = timezone.now()
        normalized_query = normalize_query(query)
        entry = (
            ResearchQueryCache.objects.filter(
                canvas=canvas,
                normalized_query=normalized_query,
                provider_identity=provider_identity,
                strategy_version=strategy_version,
                prompt_version=prompt_version,
                context_hash=context_hash,
                fresh_until__gt=now,
                expires_at__gt=now,
            )
            .order_by("-retrieved_at")
            .first()
        )
        invalidation_reason: str | None = None
        if entry is None:
            exact = (
                ResearchQueryCache.objects.filter(
                    canvas=canvas,
                    normalized_query=normalized_query,
                    provider_identity=provider_identity,
                    strategy_version=strategy_version,
                    prompt_version=prompt_version,
                    context_hash=context_hash,
                )
                .order_by("-retrieved_at")
                .first()
            )
            if exact is not None:
                invalidation_reason = "expired" if exact.expires_at <= now else "freshness_expired"
            elif ResearchQueryCache.objects.filter(
                canvas=canvas,
                normalized_query=normalized_query,
            ).exists():
                invalidation_reason = "version_or_context_changed"
            else:
                invalidation_reason = "not_found"
        emit_telemetry(
            "research_cache.query",
            canvas_id=canvas.id,
            provider_identity=provider_identity,
            outcome="hit" if entry else "miss",
            invalidation_reason=invalidation_reason,
        )
        if entry is None:
            return None
        result = dict(entry.result)
        sources = result.get("sources")
        if isinstance(sources, list):
            result["sources"] = [
                {**source, "cache_hit": True} if isinstance(source, dict) else source
                for source in sources
            ]
        return result

    def put_query(
        self,
        *,
        canvas: Canvas,
        query: str,
        provider_identity: str,
        strategy_version: str,
        prompt_version: str,
        context_hash: str,
        result: dict[str, Any],
        fresh_seconds: int = DEFAULT_FRESH_SECONDS,
    ) -> ResearchQueryCache:
        validate_retained_payload(result)
        now = timezone.now()
        fresh_seconds = max(0, min(fresh_seconds, MAX_CACHE_SECONDS))
        entry, _created = ResearchQueryCache.objects.update_or_create(
            canvas=canvas,
            normalized_query=normalize_query(query),
            provider_identity=provider_identity,
            strategy_version=strategy_version,
            prompt_version=prompt_version,
            context_hash=context_hash,
            defaults={
                "result": result,
                "retrieved_at": now,
                "fresh_until": now + timedelta(seconds=fresh_seconds),
                "expires_at": now + timedelta(seconds=MAX_CACHE_SECONDS),
            },
        )
        return entry

    def get_source(
        self,
        *,
        canvas: Canvas,
        normalized_url: str,
    ) -> dict[str, Any] | None:
        now = timezone.now()
        entry = (
            SourceContentCache.objects.filter(
                canvas=canvas,
                normalized_url=normalized_url,
                fresh_until__gt=now,
                expires_at__gt=now,
            )
            .order_by("-retrieved_at", "content_hash")
            .first()
        )
        invalidation_reason: str | None = None
        if entry is None:
            newest = (
                SourceContentCache.objects.filter(
                    canvas=canvas,
                    normalized_url=normalized_url,
                )
                .order_by("-retrieved_at", "content_hash")
                .first()
            )
            if newest is None:
                invalidation_reason = "not_found"
            else:
                invalidation_reason = "expired" if newest.expires_at <= now else "freshness_expired"
        emit_telemetry(
            "research_cache.source",
            canvas_id=canvas.id,
            normalized_url=normalized_url,
            outcome="hit" if entry else "miss",
            invalidation_reason=invalidation_reason,
        )
        if entry is None:
            return None
        return {
            **entry.retrieval_metadata,
            "content_hash": entry.content_hash,
            "retrieved_at": entry.retrieved_at.isoformat(),
            "cache_hit": True,
        }

    def put_source(
        self,
        *,
        canvas: Canvas,
        normalized_url: str,
        content_hash: str,
        retrieval_metadata: dict[str, Any],
        fresh_seconds: int = DEFAULT_FRESH_SECONDS,
    ) -> SourceContentCache:
        validate_retained_payload(retrieval_metadata)
        now = timezone.now()
        fresh_seconds = max(0, min(fresh_seconds, MAX_CACHE_SECONDS))
        entry, _created = SourceContentCache.objects.update_or_create(
            canvas=canvas,
            normalized_url=normalized_url,
            content_hash=content_hash,
            defaults={
                "retained_content": None,
                "retrieval_metadata": retrieval_metadata,
                "retrieved_at": now,
                "fresh_until": now + timedelta(seconds=fresh_seconds),
                "expires_at": now + timedelta(seconds=MAX_CACHE_SECONDS),
            },
        )
        return entry


def expired_cache_entries() -> tuple[QuerySet[ResearchQueryCache], QuerySet[SourceContentCache]]:
    now = timezone.now()
    return (
        ResearchQueryCache.objects.filter(expires_at__lte=now),
        SourceContentCache.objects.filter(expires_at__lte=now),
    )


def delete_expired_cache_entries() -> tuple[int, int]:
    research, sources = expired_cache_entries()
    research_count = research.delete()[0]
    source_count = sources.delete()[0]
    emit_telemetry(
        "research_cache.expired_deleted",
        query_count=research_count,
        source_count=source_count,
    )
    return research_count, source_count
