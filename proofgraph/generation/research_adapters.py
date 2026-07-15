from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from proofgraph.generation.pipeline_schemas import (
    PlanningOutput,
    QueryPlanItem,
    ResearchOutput,
    SourceAuthority,
    SourceRecord,
)
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.research_cache import ResearchCacheStore
from proofgraph.generation.schemas import ProgressEventEnvelope, StageResultEnvelope, TokenUsage
from proofgraph.generation.source_identity import (
    classify_source_authority,
    publisher_independence_key,
)
from proofgraph.generation.telemetry import emit_telemetry
from proofgraph.graph.models import Canvas

MAX_RESEARCH_QUERIES = 5
MAX_RETAINED_SOURCES = 10
GITHUB_API_VERSION = "2026-03-10"
STACK_EXCHANGE_API_VERSION = "2.3"


@dataclass(frozen=True)
class ResearchBackendResult:
    sources: tuple[SourceRecord, ...]
    response_id: str | None = None
    token_usage: TokenUsage | None = None


class ResearchBackend(Protocol):
    identity: str

    def search(self, query: str, *, max_results: int) -> ResearchBackendResult: ...


def _excerpt(value: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", html.unescape(value))
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:500]


def _content_hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _source_id(provider: str, value: str) -> str:
    digest = hashlib.sha256(f"{provider}:{value}".encode()).hexdigest()[:24]
    return f"source_{provider}_{digest}"


def _cited_web_snippets(payload: dict[str, Any]) -> dict[str, str]:
    snippets: dict[str, str] = {}
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            for annotation in content.get("annotations") or []:
                if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if not isinstance(url, str):
                    continue
                start = annotation.get("start_index")
                end = annotation.get("end_index")
                if (
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start < end <= len(text)
                ):
                    excerpt = text[max(0, start - 240) : min(len(text), end + 240)]
                else:
                    excerpt = text
                sanitized = _excerpt(excerpt)
                if sanitized:
                    snippets.setdefault(url, sanitized)
    return snippets


def _authority(
    url: str,
    *,
    hierarchy_rank: int,
    title: str | None = None,
    allow_first_party: bool = True,
) -> SourceAuthority:
    decision = classify_source_authority(
        url,
        hierarchy_rank=hierarchy_rank,
        title=title,
        allow_first_party=allow_first_party,
    )
    return SourceAuthority(
        domain=decision.domain,
        publisher=decision.publisher,
        authoritative=decision.authoritative,
        hierarchy_rank=decision.hierarchy_rank,
    )


def _json_request(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 15.0,
) -> tuple[dict[str, Any], Any]:
    request = Request(url, headers=headers)
    try:
        response = urlopen(request, timeout=timeout)
        with response:
            return json.loads(response.read(2 * 1024 * 1024)), response.headers
    except HTTPError:
        raise
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderExecutionError(
            "research_provider_unavailable",
            "The research provider could not be reached or returned invalid data.",
            retryable=True,
        ) from error


class GitHubPublicSearchAdapter:
    identity = f"github_rest:{GITHUB_API_VERSION}"

    def __init__(self, token: str | None = None) -> None:
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def search(self, query: str, *, max_results: int) -> ResearchBackendResult:
        started = time.monotonic()
        url = "https://api.github.com/search/issues?" + urlencode(
            {"q": query, "per_page": min(max_results, 10), "sort": "updated"}
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ProofgraphResearch/1.0",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            payload, response_headers = _json_request(url, headers=headers)
        except HTTPError as error:
            remaining = error.headers.get("X-RateLimit-Remaining")
            if error.code in {403, 429} and remaining == "0":
                raise ProviderExecutionError(
                    "github_rate_limited",
                    "GitHub search rate limit was exhausted.",
                    retryable=True,
                    details={"reset": error.headers.get("X-RateLimit-Reset")},
                ) from error
            raise ProviderExecutionError(
                "github_search_failed",
                "GitHub search returned an error.",
                retryable=error.code >= 500 or error.code == 429,
                details={"status": error.code},
            ) from error
        if response_headers.get("X-RateLimit-Remaining") == "0":
            emit_telemetry("research.rate_limit", provider=self.identity, remaining=0)
        now = datetime.now(UTC)
        sources: list[SourceRecord] = []
        for item in (payload.get("items") or [])[:max_results]:
            if not isinstance(item, dict) or not isinstance(item.get("html_url"), str):
                continue
            source_url = item["html_url"]
            title = _excerpt(str(item.get("title") or "GitHub discussion"))
            snippet = _excerpt(str(item.get("body") or title)) or title
            sources.append(
                SourceRecord(
                    id=_source_id("github", source_url),
                    kind="github",
                    url=source_url,
                    title=title,
                    retrieved_at=now,
                    content_hash=_content_hash(snippet),
                    independence_key=publisher_independence_key(source_url),
                    authority=_authority(
                        source_url,
                        hierarchy_rank=5,
                        title=title,
                        allow_first_party=False,
                    ),
                    sanitized_excerpt=snippet,
                )
            )
        emit_telemetry(
            "research.provider",
            provider=self.identity,
            latency_ms=int((time.monotonic() - started) * 1_000),
            retained_count=len(sources),
        )
        return ResearchBackendResult(tuple(sources))


class StackExchangeSearchAdapter:
    identity = f"stack_exchange:{STACK_EXCHANGE_API_VERSION}"

    def __init__(self, key: str | None = None, *, site: str = "stackoverflow") -> None:
        self.key = key if key is not None else os.environ.get("STACK_EXCHANGE_KEY")
        self.site = site

    def search(self, query: str, *, max_results: int) -> ResearchBackendResult:
        started = time.monotonic()
        params = {
            "q": query,
            "site": self.site,
            "pagesize": min(max_results, 10),
            "order": "desc",
            "sort": "relevance",
            "filter": "withbody",
        }
        if self.key:
            params["key"] = self.key
        url = (
            f"https://api.stackexchange.com/{STACK_EXCHANGE_API_VERSION}/search/advanced?"
            + urlencode(params)
        )
        try:
            payload, _headers = _json_request(
                url,
                headers={"User-Agent": "ProofgraphResearch/1.0"},
            )
        except HTTPError as error:
            raise ProviderExecutionError(
                "stack_exchange_rate_limited"
                if error.code in {429, 502}
                else "stack_exchange_failed",
                "Stack Exchange search was throttled or unavailable.",
                retryable=error.code >= 500 or error.code == 429,
                details={"status": error.code},
            ) from error
        if payload.get("backoff") is not None or payload.get("quota_remaining") == 0:
            raise ProviderExecutionError(
                "stack_exchange_rate_limited",
                "Stack Exchange requested a search backoff.",
                retryable=True,
                details={
                    "backoff_seconds": payload.get("backoff"),
                    "quota_remaining": payload.get("quota_remaining"),
                },
            )
        now = datetime.now(UTC)
        sources: list[SourceRecord] = []
        for item in (payload.get("items") or [])[:max_results]:
            if not isinstance(item, dict) or not isinstance(item.get("link"), str):
                continue
            source_url = item["link"]
            title = _excerpt(str(item.get("title") or "Stack Exchange question"))
            snippet = _excerpt(str(item.get("body") or ""))
            if not snippet:
                continue
            sources.append(
                SourceRecord(
                    id=_source_id("stack", source_url),
                    kind="stack_exchange",
                    url=source_url,
                    title=title,
                    retrieved_at=now,
                    content_hash=_content_hash(snippet),
                    independence_key=publisher_independence_key(source_url),
                    authority=_authority(
                        source_url,
                        hierarchy_rank=5,
                        title=title,
                        allow_first_party=False,
                    ),
                    sanitized_excerpt=snippet,
                )
            )
        emit_telemetry(
            "research.provider",
            provider=self.identity,
            latency_ms=int((time.monotonic() - started) * 1_000),
            retained_count=len(sources),
            quota_remaining=payload.get("quota_remaining"),
        )
        return ResearchBackendResult(tuple(sources))


def _object_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


class OpenAIHostedWebSearchAdapter:
    identity = "openai_web_search:gpt-5.6:v1"

    def __init__(self, client: Any) -> None:
        self.client = client

    def search(self, query: str, *, max_results: int) -> ResearchBackendResult:
        started = time.monotonic()
        try:
            response = self.client.responses.create(
                model="gpt-5.6",
                reasoning={"effort": "low"},
                tools=[{"type": "web_search"}],
                tool_choice="auto",
                include=["web_search_call.action.sources"],
                input=query,
            )
        except Exception as error:
            status = getattr(error, "status_code", None)
            raise ProviderExecutionError(
                "openai_rate_limited" if status == 429 else "openai_web_search_failed",
                "OpenAI hosted web search failed.",
                retryable=status == 429
                or status is None
                or (isinstance(status, int) and status >= 500),
                details={"status": status},
            ) from error
        payload = _object_payload(response)
        cited_snippets = _cited_web_snippets(payload)
        raw_sources: list[dict[str, Any]] = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_call":
                continue
            action = item.get("action")
            if isinstance(action, dict) and isinstance(action.get("sources"), list):
                raw_sources.extend(
                    source for source in action["sources"] if isinstance(source, dict)
                )
        now = datetime.now(UTC)
        sources: list[SourceRecord] = []
        for item in raw_sources[:max_results]:
            source_url = item.get("url")
            if not isinstance(source_url, str) or not source_url.startswith("https://"):
                continue
            title = _excerpt(
                str(item.get("title") or urlsplit(source_url).hostname or "Web source")
            )
            snippet = cited_snippets.get(source_url)
            if not snippet:
                continue
            sources.append(
                SourceRecord(
                    id=_source_id("openai", source_url),
                    kind="web",
                    url=source_url,
                    title=title,
                    retrieved_at=now,
                    content_hash=_content_hash(snippet),
                    independence_key=publisher_independence_key(source_url),
                    authority=_authority(
                        source_url,
                        hierarchy_rank=3,
                        title=title,
                    ),
                    sanitized_excerpt=snippet,
                )
            )
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(usage.get("total_tokens") or input_tokens + output_tokens),
        )
        emit_telemetry(
            "research.provider",
            provider=self.identity,
            latency_ms=int((time.monotonic() - started) * 1_000),
            retained_count=len(sources),
            token_usage=token_usage.model_dump(mode="json"),
        )
        return ResearchBackendResult(
            tuple(sources),
            response_id=str(payload.get("id")) if payload.get("id") else None,
            token_usage=token_usage,
        )


class UserSourceResearchAdapter:
    identity = "user_source:context:v1"

    def search(self, query: str, *, max_results: int) -> ResearchBackendResult:
        del query, max_results
        raise ProviderExecutionError(
            "invalid_user_source_context",
            "User-supplied research requires the frozen canvas context.",
            retryable=False,
        )

    def search_context(
        self,
        context_snapshot: dict[str, Any],
        *,
        max_results: int,
    ) -> ResearchBackendResult:
        sources: list[SourceRecord] = []
        nodes = [item for item in context_snapshot.get("nodes") or [] if isinstance(item, dict)]
        for node in sorted(nodes, key=lambda item: str(item.get("id") or "")):
            if node.get("kind") != "source" or not node.get("id"):
                continue
            metadata = node.get("metadata")
            if not isinstance(metadata, dict):
                continue
            source_kind = metadata.get("source_kind")
            if source_kind not in {"user_url", "user_text"}:
                continue
            excerpt = _excerpt(str(node.get("sanitized_excerpt") or ""))
            if not excerpt:
                continue
            authority = metadata.get("authority")
            if not isinstance(authority, dict):
                authority = {
                    "domain": None,
                    "publisher": "user supplied",
                    "authoritative": False,
                    "hierarchy_rank": 6,
                }
            try:
                source = SourceRecord.model_validate_json(
                    json.dumps(
                        {
                            "id": str(node["id"]),
                            "kind": source_kind,
                            "url": metadata.get("canonical_url"),
                            "title": str(node.get("title") or "User-supplied source"),
                            "retrieved_at": metadata.get("retrieved_at"),
                            "content_hash": str(metadata.get("content_hash") or ""),
                            "independence_key": str(metadata.get("independence_key") or ""),
                            "authority": authority,
                            "sanitized_excerpt": excerpt,
                        }
                    )
                )
            except ValidationError as error:
                raise ProviderExecutionError(
                    "invalid_user_source_context",
                    "A user-supplied source in the frozen context is malformed.",
                    retryable=False,
                    details={"source_id": str(node["id"])},
                ) from error
            sources.append(source)
            if len(sources) >= max_results:
                break
        return ResearchBackendResult(tuple(sources))


def _planning_output(stage_input: dict[str, Any]) -> PlanningOutput:
    prior = stage_input.get("prior_stage_outputs")
    if not isinstance(prior, dict):
        raise ProviderExecutionError(
            "missing_research_plan",
            "Research requires a completed planning checkpoint.",
            retryable=False,
        )
    candidates = [
        value for key, value in prior.items() if key == "planning" or key.endswith(":planning")
    ]
    if not candidates or not isinstance(candidates[-1], dict):
        raise ProviderExecutionError(
            "missing_research_plan",
            "Research requires a completed planning checkpoint.",
            retryable=False,
        )
    output = candidates[-1].get("output")
    if not isinstance(output, dict):
        raise ProviderExecutionError(
            "missing_research_plan",
            "The research plan checkpoint is malformed.",
            retryable=False,
        )
    return PlanningOutput.model_validate_json(json.dumps(output))


class BoundedResearchProvider:
    def __init__(
        self,
        backends: dict[str, ResearchBackend],
        *,
        cache: ResearchCacheStore | None = None,
    ) -> None:
        self.backends = backends
        self.cache = cache or ResearchCacheStore()

    def research(self, request: ProviderStageRequest) -> StageResultEnvelope:
        planning = _planning_output(request.stage_input)
        query_items = tuple(query for plan in planning.research_plans for query in plan.query_plan)
        if len(query_items) > MAX_RESEARCH_QUERIES:
            raise ProviderExecutionError(
                "research_budget_exceeded",
                "The frozen research plan exceeds the five-query run budget.",
                retryable=False,
                details={"planned_queries": len(query_items), "maximum_queries": 5},
            )
        context_snapshot = request.stage_input.get("context_snapshot")
        if not isinstance(context_snapshot, dict):
            raise ProviderExecutionError(
                "invalid_research_context",
                "Research requires a canvas-scoped semantic context.",
                retryable=False,
            )
        canvas_id = context_snapshot.get("canvas_id")
        canvas = Canvas.objects.get(pk=uuid.UUID(str(canvas_id)))
        context_hash = str(request.stage_input.get("context_hash") or "")
        retained: dict[str, SourceRecord] = {}
        events: list[ProgressEventEnvelope] = []

        def publish(event: ProgressEventEnvelope) -> None:
            events.extend(request.deliver_progress((event,)))

        response_ids: list[str] = []
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        executed: list[QueryPlanItem] = []
        for query_item in query_items:
            backend = self.backends.get(query_item.provider)
            if backend is None:
                raise ProviderExecutionError(
                    "research_provider_unavailable",
                    "The research plan selected an unavailable provider.",
                    retryable=False,
                    details={"provider": query_item.provider},
                )
            executed.append(query_item)
            publish(
                ProgressEventEnvelope(
                    event_type="research.query_created",
                    payload={
                        "query_id": query_item.id,
                        "provider": backend.identity,
                    },
                )
            )
            cached = self.cache.get_query(
                canvas=canvas,
                query=query_item.query,
                provider_identity=backend.identity,
                strategy_version=request.configuration.strategy_version,
                prompt_version=request.configuration.prompt_version,
                context_hash=context_hash,
            )
            if cached is not None:
                result_sources = tuple(
                    SourceRecord.model_validate_json(json.dumps(item))
                    for item in cached.get("sources", [])
                )
                result = ResearchBackendResult(result_sources)
            else:
                if isinstance(backend, UserSourceResearchAdapter):
                    result = backend.search_context(
                        context_snapshot,
                        max_results=MAX_RETAINED_SOURCES,
                    )
                else:
                    result = backend.search(query_item.query, max_results=MAX_RETAINED_SOURCES)
                self.cache.put_query(
                    canvas=canvas,
                    query=query_item.query,
                    provider_identity=backend.identity,
                    strategy_version=request.configuration.strategy_version,
                    prompt_version=request.configuration.prompt_version,
                    context_hash=context_hash,
                    result={
                        "sources": [source.model_dump(mode="json") for source in result.sources]
                    },
                )
            if result.response_id:
                response_ids.append(result.response_id)
            if result.token_usage:
                usage = TokenUsage(
                    input_tokens=usage.input_tokens + result.token_usage.input_tokens,
                    output_tokens=usage.output_tokens + result.token_usage.output_tokens,
                    total_tokens=usage.total_tokens + result.token_usage.total_tokens,
                )
            for source in result.sources:
                deduplication_key = str(source.url or source.content_hash)
                retained.setdefault(deduplication_key, source)
                if len(retained) <= MAX_RETAINED_SOURCES:
                    publish(
                        ProgressEventEnvelope(
                            event_type="research.source_found",
                            payload={
                                "provisional": True,
                                "source_id": source.id,
                                "url": str(source.url) if source.url else None,
                                "content_hash": source.content_hash,
                                "sanitized_excerpt": source.sanitized_excerpt,
                                "cache_hit": source.cache_hit,
                            },
                        )
                    )
        selected = self._normalize_mirror_independence(
            tuple(retained.values())[:MAX_RETAINED_SOURCES]
        )
        if not selected:
            raise ProviderExecutionError(
                "no_useful_search_results",
                "No retained sources matched the bounded research plan.",
                retryable=False,
                details={"queries_executed": len(executed)},
            )
        output = ResearchOutput(
            queries_executed=tuple(executed),
            sources=selected,
            no_results_reason=None,
        )
        return StageResultEnvelope(
            stage_name="researching",
            output=output.model_dump(mode="json"),
            provider_identity=request.configuration.provider_identity,
            model_response_id=response_ids[-1] if response_ids else None,
            token_usage=usage if usage.total_tokens else None,
            progress_events=tuple(events),
        )

    @staticmethod
    def _normalize_mirror_independence(
        sources: tuple[SourceRecord, ...],
    ) -> tuple[SourceRecord, ...]:
        publishers_by_hash: dict[str, set[str]] = {}
        for source in sources:
            publishers_by_hash.setdefault(source.content_hash, set()).add(source.independence_key)
        mirror_hashes = {
            content_hash
            for content_hash, publishers in publishers_by_hash.items()
            if len(publishers) > 1
        }
        if not mirror_hashes:
            return sources
        normalized: list[SourceRecord] = []
        for source in sources:
            if source.content_hash not in mirror_hashes:
                normalized.append(source)
                continue
            normalized.append(
                source.model_copy(
                    update={
                        "independence_key": (
                            f"mirror:{source.content_hash.removeprefix('sha256:')}"
                        )
                    }
                )
            )
        return tuple(normalized)


__all__ = [
    "BoundedResearchProvider",
    "GitHubPublicSearchAdapter",
    "OpenAIHostedWebSearchAdapter",
    "StackExchangeSearchAdapter",
    "UserSourceResearchAdapter",
]
