from __future__ import annotations

import json
from datetime import timedelta
from email.message import Message
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from proofgraph.generation.clustering import ExactEvidenceClusterer, select_retained_claims
from proofgraph.generation.models import ResearchQueryCache, SourceContentCache
from proofgraph.generation.pipeline_schemas import (
    ClusteringOutput,
    ExtractionOutput,
    ResearchOutput,
    SourceRecord,
)
from proofgraph.generation.ports import ProviderStageRequest
from proofgraph.generation.provider_errors import ProviderExecutionError
from proofgraph.generation.research_adapters import (
    BoundedResearchProvider,
    GitHubPublicSearchAdapter,
    OpenAIHostedWebSearchAdapter,
    ResearchBackendResult,
    StackExchangeSearchAdapter,
    UserSourceResearchAdapter,
    _authority,
)
from proofgraph.generation.research_cache import (
    ResearchCacheStore,
    delete_expired_cache_entries,
)
from proofgraph.generation.schemas import RunExecutionConfiguration
from proofgraph.generation.source_identity import (
    publisher_independence_key,
    registrable_domain,
)
from proofgraph.graph.models import Canvas

pytestmark = pytest.mark.django_db


def configuration() -> RunExecutionConfiguration:
    return RunExecutionConfiguration(
        profile_id="live_v1",
        provider_identity="live:providers:v1",
        pipeline_version="intelligence_pipeline_v1",
        prompt_version="opportunity_pipeline_prompts_v1",
        strategy_version="opportunity_strategies_v1",
    )


def source_payload(
    source_id: str,
    independence_key: str,
    *,
    cache_hit: bool = False,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "kind": "web",
        "url": f"https://example.com/{source_id}",
        "title": f"Source {source_id}",
        "retrieved_at": "2026-07-14T12:00:00Z",
        "content_hash": "sha256:" + hashlib_character(source_id) * 64,
        "independence_key": independence_key,
        "authority": {
            "domain": "example.com",
            "publisher": "Example",
            "authoritative": True,
            "hierarchy_rank": 1,
        },
        "sanitized_excerpt": "Bounded evidence excerpt.",
        "cache_hit": cache_hit,
    }


def hashlib_character(value: str) -> str:
    return "abcdef0123456789"[sum(value.encode()) % 16]


def claim_payload(
    claim_id: str,
    source_ids: list[str],
    *,
    classification: str = "observed",
    contradiction_target_key: str | None = None,
    claim: str | None = None,
) -> dict[str, Any]:
    return {
        "id": claim_id,
        "claim": claim or f"Claim {claim_id}",
        "classification": classification,
        "evidence_type": "customer_pain",
        "topic_keys": ["security_review"],
        "mechanism_tags": ["automate_mandatory_work"],
        "contradiction_target_key": contradiction_target_key,
        "strength": "strong",
        "limitations": ["Bounded fixture"],
        "source_ids": sorted(source_ids),
    }


def extraction_payload() -> dict[str, Any]:
    return {
        "sources": [
            source_payload("source_one", "publisher:one.example"),
            source_payload("source_two", "publisher:two.example"),
            source_payload("source_mirror", "publisher:one.example"),
        ],
        "claims": [
            claim_payload("claim_one", ["source_one"]),
            claim_payload("claim_two", ["source_two"]),
            claim_payload("claim_mirror", ["source_mirror"]),
            claim_payload(
                "claim_contradiction",
                ["source_two"],
                classification="contradicting",
                contradiction_target_key="automate_mandatory_work",
            ),
        ],
        "candidate_claim_ids": [
            "claim_contradiction",
            "claim_mirror",
            "claim_one",
            "claim_two",
        ],
        "rejected": [],
    }


def cluster(payload: dict[str, Any]) -> ClusteringOutput:
    request = ProviderStageRequest(
        stage_input={
            "prior_stage_outputs": {
                "extracting": {"output": payload},
            }
        },
        configuration=configuration(),
    )
    result = ExactEvidenceClusterer().cluster(request)
    return ClusteringOutput.model_validate_json(json.dumps(result.output))


def test_clustering_is_order_stable_and_counts_independent_sources() -> None:
    payload = extraction_payload()
    first = cluster(payload)
    reordered = {
        **payload,
        "sources": list(reversed(payload["sources"])),
        "claims": list(reversed(payload["claims"])),
    }
    second = cluster(reordered)

    assert first == second
    assert len(first.clusters) == 2
    supporting = next(item for item in first.clusters if item.contradiction_target_key is None)
    contradiction = next(
        item for item in first.clusters if item.contradiction_target_key is not None
    )
    assert supporting.independent_support_count == 2
    assert supporting.independence_keys == (
        "publisher:one.example",
        "publisher:two.example",
    )
    assert contradiction.independent_support_count == 1


def test_claim_retention_is_deterministic_bounded_and_deduplicates() -> None:
    payload = extraction_payload()
    payload["claims"] = [claim_payload(f"claim_{index:02}", ["source_one"]) for index in range(14)]
    payload["claims"].append(
        claim_payload(
            "claim_duplicate",
            ["source_two"],
            claim="Claim claim_00",
        )
    )
    payload["candidate_claim_ids"] = sorted(claim["id"] for claim in payload["claims"])
    extraction = ExtractionOutput.model_validate_json(json.dumps(payload))

    retained = select_retained_claims(extraction)

    assert len(retained.claims) == 12
    assert [claim.id for claim in retained.claims] == sorted(claim.id for claim in retained.claims)
    assert any(item.reason == "duplicate" for item in retained.rejected)
    assert sum(item.reason == "rejected" for item in retained.rejected) == 2


def test_bounded_research_returns_a_user_readable_no_results_failure() -> None:
    canvas = Canvas.objects.create(title="No results")

    class EmptyBackend:
        identity = "empty:test:v1"

        @staticmethod
        def search(query: str, *, max_results: int) -> ResearchBackendResult:
            assert query == "narrow evidence query"
            assert max_results > 0
            return ResearchBackendResult(())

    provider = BoundedResearchProvider(
        {"openai_web_search": EmptyBackend()},
        cache=ResearchCacheStore(),
    )
    with pytest.raises(ProviderExecutionError) as captured:
        provider.research(
            ProviderStageRequest(
                stage_input={
                    "context_hash": "no-results-context",
                    "context_snapshot": {"canvas_id": str(canvas.id), "nodes": [], "edges": []},
                    "prior_stage_outputs": {
                        "planning": {
                            "output": {
                                "operation": "research_evidence",
                                "strategies": [],
                                "research_plans": [
                                    {
                                        "selected_strategy_id": "strategy_one",
                                        "research_questions": [
                                            {
                                                "id": "question_one",
                                                "question": "Is there evidence?",
                                                "evidence_type": "demand",
                                            }
                                        ],
                                        "query_plan": [
                                            {
                                                "id": "query_one",
                                                "query": "narrow evidence query",
                                                "provider": "openai_web_search",
                                                "evidence_type": "demand",
                                            }
                                        ],
                                        "required_evidence_types": [
                                            {
                                                "evidence_type": "demand",
                                                "reason": (
                                                    "Demand must be independently observable."
                                                ),
                                                "minimum_independent_sources": 1,
                                            }
                                        ],
                                    }
                                ],
                            }
                        }
                    },
                },
                configuration=configuration(),
            )
        )

    assert captured.value.code == "no_useful_search_results"
    assert captured.value.message == "No retained sources matched the bounded research plan."
    assert not captured.value.retryable


def test_query_and_source_cache_exact_keys_freshness_and_expiry() -> None:
    canvas = Canvas.objects.create(title="Cache")
    store = ResearchCacheStore()
    result = {"sources": [source_payload("source_one", "publisher:one.example")]}
    store.put_query(
        canvas=canvas,
        query="  Security   Review ",
        provider_identity="provider:v1",
        strategy_version="strategy:v1",
        prompt_version="prompt:v1",
        context_hash="context-one",
        result=result,
    )

    hit = store.get_query(
        canvas=canvas,
        query="security review",
        provider_identity="provider:v1",
        strategy_version="strategy:v1",
        prompt_version="prompt:v1",
        context_hash="context-one",
    )
    changed_key = store.get_query(
        canvas=canvas,
        query="security review",
        provider_identity="provider:v1",
        strategy_version="strategy:v1",
        prompt_version="prompt:v1",
        context_hash="context-two",
    )
    assert hit is not None and hit["sources"][0]["cache_hit"] is True
    assert changed_key is None

    now = timezone.now()
    ResearchQueryCache.objects.update(
        retrieved_at=now - timedelta(hours=2),
        fresh_until=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
    )
    assert (
        store.get_query(
            canvas=canvas,
            query="security review",
            provider_identity="provider:v1",
            strategy_version="strategy:v1",
            prompt_version="prompt:v1",
            context_hash="context-one",
        )
        is None
    )

    first = store.put_source(
        canvas=canvas,
        normalized_url="https://example.com/evidence",
        content_hash="sha256:" + ("a" * 64),
        retrieval_metadata={"sanitized_excerpt": "First version"},
    )
    second = store.put_source(
        canvas=canvas,
        normalized_url="https://example.com/evidence",
        content_hash="sha256:" + ("b" * 64),
        retrieval_metadata={"sanitized_excerpt": "Changed version"},
    )
    assert first.pk != second.pk
    assert store.get_source(
        canvas=canvas,
        normalized_url="https://example.com/evidence",
    )["content_hash"] in {first.content_hash, second.content_hash}
    assert not SourceContentCache.objects.exclude(retained_content=None).exists()

    ResearchQueryCache.objects.update(
        retrieved_at=now - timedelta(days=2),
        fresh_until=now - timedelta(days=1, seconds=1),
        expires_at=now - timedelta(days=1),
    )
    SourceContentCache.objects.update(
        retrieved_at=now - timedelta(days=2),
        fresh_until=now - timedelta(days=1, seconds=1),
        expires_at=now - timedelta(days=1),
    )
    assert delete_expired_cache_entries() == (1, 2)


def test_cache_miss_telemetry_distinguishes_invalidation_reasons() -> None:
    canvas = Canvas.objects.create(title="Cache telemetry")
    store = ResearchCacheStore()
    store.put_query(
        canvas=canvas,
        query="security pricing",
        provider_identity="provider:v1",
        strategy_version="strategy:v1",
        prompt_version="prompt:v1",
        context_hash="context-one",
        result={"sources": []},
    )

    with patch("proofgraph.generation.research_cache.emit_telemetry") as telemetry:
        assert (
            store.get_query(
                canvas=canvas,
                query="security pricing",
                provider_identity="provider:v1",
                strategy_version="strategy:v1",
                prompt_version="prompt:v1",
                context_hash="context-two",
            )
            is None
        )
    assert telemetry.call_args.kwargs["invalidation_reason"] == "version_or_context_changed"

    expired_at = timezone.now() - timedelta(seconds=1)
    ResearchQueryCache.objects.update(
        retrieved_at=expired_at - timedelta(seconds=1),
        fresh_until=expired_at,
    )
    with patch("proofgraph.generation.research_cache.emit_telemetry") as telemetry:
        assert (
            store.get_query(
                canvas=canvas,
                query="security pricing",
                provider_identity="provider:v1",
                strategy_version="strategy:v1",
                prompt_version="prompt:v1",
                context_hash="context-one",
            )
            is None
        )
    assert telemetry.call_args.kwargs["invalidation_reason"] == "freshness_expired"


def test_authority_recognizes_official_vendor_docs_and_pricing() -> None:
    pricing = _authority(
        "https://vendor.example/pricing",
        hierarchy_rank=3,
        title="Vendor pricing",
    )
    documentation = _authority(
        "https://docs.vendor.example/getting-started",
        hierarchy_rank=3,
        title="Getting started",
    )
    commentary = _authority(
        "https://analyst.example/vendor-pricing-review",
        hierarchy_rank=3,
        title="Vendor pricing review",
    )
    generic_pricing_page = _authority(
        "https://analyst.example/pricing",
        hierarchy_rank=3,
        title="Vendor pricing review",
    )
    analyst_first_party_pricing = _authority(
        "https://analyst.example/pricing",
        hierarchy_rank=3,
        title="Analyst pricing",
    )
    brandless_pricing = _authority(
        "https://vendor.example/pricing",
        hierarchy_rank=3,
        title="Plans and billing",
    )
    brandless_legal = _authority(
        "https://vendor.example/legal/privacy",
        hierarchy_rank=3,
        title="Privacy policy",
    )
    third_party_docs = _authority(
        "https://docs.analyst.example/vendor-pricing",
        hierarchy_rank=3,
        title="Vendor pricing review",
    )
    third_party_security = _authority(
        "https://security.blog.example/vendor-breach",
        hierarchy_rank=3,
        title="Independent vendor security analysis",
    )
    hosted_discussion = _authority(
        "https://github.com/example/docs/issues/1",
        hierarchy_rank=5,
        title="Official documentation issue",
        allow_first_party=False,
    )

    assert pricing.authoritative is True and pricing.hierarchy_rank == 1
    assert documentation.authoritative is True and documentation.hierarchy_rank == 1
    assert commentary.authoritative is False and commentary.hierarchy_rank == 3
    assert generic_pricing_page.authoritative is False
    assert analyst_first_party_pricing.authoritative is True
    assert brandless_pricing.authoritative is True and brandless_pricing.hierarchy_rank == 1
    assert brandless_legal.authoritative is True and brandless_legal.hierarchy_rank == 1
    assert third_party_docs.authoritative is False
    assert third_party_security.authoritative is False
    assert hosted_discussion.authoritative is False and hosted_discussion.hierarchy_rank == 5


def test_publisher_identity_collapses_subdomains_and_respects_public_suffixes() -> None:
    assert publisher_independence_key(
        "https://www.vendor.example/announcement"
    ) == publisher_independence_key("https://docs.vendor.example/reference")
    assert registrable_domain("https://docs.service.co.uk/reference") == "service.co.uk"


def test_mirrored_content_across_publishers_counts_as_one_independent_source() -> None:
    shared_hash = "sha256:" + ("d" * 64)
    first = SourceRecord.model_validate_json(
        json.dumps(
            {
                **source_payload("mirror-one", "publisher:first.example"),
                "url": "https://first.example/report",
                "content_hash": shared_hash,
            }
        )
    )
    second = SourceRecord.model_validate_json(
        json.dumps(
            {
                **source_payload("mirror-two", "publisher:second.example"),
                "url": "https://second.example/copied-report",
                "content_hash": shared_hash,
            }
        )
    )

    normalized = BoundedResearchProvider._normalize_mirror_independence((first, second))

    assert normalized[0].independence_key == normalized[1].independence_key
    assert normalized[0].independence_key == f"mirror:{'d' * 64}"


def test_source_cache_database_rejects_retained_content() -> None:
    canvas = Canvas.objects.create(title="No retained pages")
    now = timezone.now()

    with pytest.raises(IntegrityError), transaction.atomic():
        SourceContentCache.objects.create(
            canvas=canvas,
            normalized_url="https://example.com/page",
            content_hash="sha256:" + ("c" * 64),
            retained_content="complete page",
            retrieval_metadata={},
            retrieved_at=now,
            fresh_until=now + timedelta(hours=1),
            expires_at=now + timedelta(hours=24),
        )


def test_cache_lookup_indexes_match_freshness_queries() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN (COSTS OFF) "
            "SELECT id FROM research_query_cache "
            "WHERE canvas_id = %s AND normalized_query = %s "
            "AND provider_identity = %s AND strategy_version = %s "
            "AND prompt_version = %s AND context_hash = %s "
            "AND fresh_until > CURRENT_TIMESTAMP LIMIT 1",
            [
                "00000000-0000-0000-0000-000000000001",
                "query",
                "provider",
                "strategy",
                "prompt",
                "context",
            ],
        )
        research_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(
            "EXPLAIN (COSTS OFF) "
            "SELECT id FROM source_content_cache "
            "WHERE canvas_id = %s AND normalized_url = %s "
            "AND fresh_until > CURRENT_TIMESTAMP "
            "ORDER BY retrieved_at DESC LIMIT 1",
            [
                "00000000-0000-0000-0000-000000000001",
                "https://example.com/",
            ],
        )
        source_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'source_content_cache'")
        source_indexes = {row[0] for row in cursor.fetchall()}

    assert any(
        index_name in research_plan
        for index_name in ("research_cache_fresh_idx", "uq_research_query_cache_key")
    )
    assert "Seq Scan" not in source_plan
    assert any(
        index_name in source_plan
        for index_name in ("source_cache_fresh_idx", "uq_source_content_cache_key")
    )
    assert "source_cache_fresh_idx" in source_indexes


def test_github_and_stack_adapters_surface_rate_limits_and_source_identity() -> None:
    github_payload = {
        "items": [
            {
                "html_url": "https://github.com/example/project/issues/1",
                "repository_url": "https://api.github.com/repos/example/project",
                "title": "Repeated security review work",
                "body": "Teams repeat questionnaire answers.",
            }
        ]
    }
    with patch(
        "proofgraph.generation.research_adapters._json_request",
        return_value=(github_payload, {"X-RateLimit-Remaining": "10"}),
    ):
        result = GitHubPublicSearchAdapter(token="token").search("questionnaire", max_results=3)
    assert result.sources[0].independence_key == "publisher:github.com"

    headers = Message()
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "123"
    rate_error = HTTPError(
        "https://api.github.com/search/issues",
        403,
        "rate limit",
        headers,
        None,
    )
    with (
        patch(
            "proofgraph.generation.research_adapters._json_request",
            side_effect=rate_error,
        ),
        pytest.raises(ProviderExecutionError) as github_failure,
    ):
        GitHubPublicSearchAdapter(token="token").search("questionnaire", max_results=3)
    assert github_failure.value.code == "github_rate_limited"
    assert github_failure.value.retryable

    with (
        patch(
            "proofgraph.generation.research_adapters._json_request",
            return_value=({"items": [], "backoff": 30, "quota_remaining": 9}, {}),
        ),
        pytest.raises(ProviderExecutionError) as stack_failure,
    ):
        StackExchangeSearchAdapter().search("questionnaire", max_results=3)
    assert stack_failure.value.code == "stack_exchange_rate_limited"
    assert stack_failure.value.details["backoff_seconds"] == 30


def test_stack_adapter_requires_and_retains_derived_body_excerpts() -> None:
    payload = {
        "items": [
            {
                "question_id": 42,
                "link": "https://stackoverflow.com/questions/42/example",
                "title": "Title is not evidence",
                "body": "<p>Teams repeatedly reconcile questionnaire answers by hand.</p>",
            },
            {
                "question_id": 43,
                "link": "https://stackoverflow.com/questions/43/title-only",
                "title": "This title-only result must be skipped",
            },
        ],
        "quota_remaining": 100,
    }
    with patch(
        "proofgraph.generation.research_adapters._json_request",
        return_value=(payload, {}),
    ) as request_mock:
        result = StackExchangeSearchAdapter().search("questionnaire", max_results=3)

    assert len(result.sources) == 1
    assert result.sources[0].independence_key == "publisher:stackoverflow.com"
    assert result.sources[0].sanitized_excerpt == (
        "Teams repeatedly reconcile questionnaire answers by hand."
    )
    assert "filter=withbody" in request_mock.call_args.args[0]


class FakeResponses:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.arguments = kwargs

        class Response:
            @staticmethod
            def model_dump(*, mode: str) -> dict[str, Any]:
                assert mode == "json"
                return {
                    "id": "response_1",
                    "output": [
                        {
                            "type": "web_search_call",
                            "action": {
                                "sources": [
                                    {
                                        "url": "https://official.example/pricing",
                                        "title": "Official pricing",
                                    }
                                ]
                            },
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "The official pricing page lists a recurring team plan."
                                    ),
                                    "annotations": [
                                        {
                                            "type": "url_citation",
                                            "url": "https://official.example/pricing",
                                            "start_index": 4,
                                            "end_index": 25,
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 4,
                        "total_tokens": 14,
                    },
                }

        return Response()


def test_openai_hosted_search_requests_full_sources_and_records_usage() -> None:
    responses = FakeResponses()

    class Client:
        pass

    client = Client()
    client.responses = responses
    result = OpenAIHostedWebSearchAdapter(client).search("pricing", max_results=3)

    assert responses.arguments["model"] == "gpt-5.6"
    assert responses.arguments["tools"] == [{"type": "web_search"}]
    assert responses.arguments["include"] == ["web_search_call.action.sources"]
    assert result.response_id == "response_1"
    assert result.token_usage is not None and result.token_usage.total_tokens == 14
    assert str(result.sources[0].url) == "https://official.example/pricing"
    assert result.sources[0].sanitized_excerpt != result.sources[0].title


class CountingBackend:
    identity = "github:test"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, max_results: int) -> ResearchBackendResult:
        del query, max_results
        self.calls += 1
        return ResearchBackendResult(
            (
                SourceRecord.model_validate_json(
                    json.dumps(source_payload("source_one", "publisher:one.example"))
                ),
            )
        )


def test_bounded_research_provider_reuses_cache_and_emits_provisional_sources() -> None:
    canvas = Canvas.objects.create(title="Research provider")
    planning = {
        "operation": "research_evidence",
        "research_plans": [
            {
                "selected_strategy_id": "strategy_one",
                "research_questions": [
                    {
                        "id": "question_one",
                        "question": "Do teams repeat this work?",
                        "evidence_type": "customer_pain",
                    }
                ],
                "query_plan": [
                    {
                        "id": "query_one",
                        "query": "security questionnaire repeated work",
                        "provider": "github",
                        "evidence_type": "customer_pain",
                    }
                ],
                "required_evidence_types": [
                    {
                        "evidence_type": "customer_pain",
                        "reason": "Validate recurrence.",
                        "minimum_independent_sources": 1,
                    }
                ],
            }
        ],
    }
    backend = CountingBackend()
    provider = BoundedResearchProvider({"github": backend})
    request = ProviderStageRequest(
        stage_input={
            "context_snapshot": {"canvas_id": str(canvas.id)},
            "context_manifest": {},
            "context_hash": "context-hash",
            "prior_stage_outputs": {"planning": {"output": planning}},
        },
        configuration=configuration(),
    )

    first = provider.research(request)
    second = provider.research(request)
    first_output = ResearchOutput.model_validate_json(json.dumps(first.output))
    second_output = ResearchOutput.model_validate_json(json.dumps(second.output))

    assert backend.calls == 1
    assert len(first_output.queries_executed) == 1
    assert first_output.sources[0].cache_hit is False
    assert second_output.sources[0].cache_hit is True
    source_events = [
        event for event in first.progress_events if event.event_type == "research.source_found"
    ]
    assert source_events and source_events[0].payload["provisional"] is True


def test_user_source_query_uses_only_the_frozen_sanitized_context() -> None:
    canvas = Canvas.objects.create(title="User-source research")
    source_id = "source_user_text"
    planning = {
        "operation": "research_evidence",
        "research_plans": [
            {
                "selected_strategy_id": "strategy_one",
                "research_questions": [
                    {
                        "id": "question_one",
                        "question": "What did the user supply?",
                        "evidence_type": "customer_pain",
                    }
                ],
                "query_plan": [
                    {
                        "id": "query_one",
                        "query": "user supplied evidence",
                        "provider": "user_source",
                        "evidence_type": "customer_pain",
                    }
                ],
                "required_evidence_types": [
                    {
                        "evidence_type": "customer_pain",
                        "reason": "Use the explicit source.",
                        "minimum_independent_sources": 1,
                    }
                ],
            }
        ],
    }
    provider = BoundedResearchProvider({"user_source": UserSourceResearchAdapter()})
    stage_request = ProviderStageRequest(
        stage_input={
            "context_snapshot": {
                "canvas_id": str(canvas.id),
                "nodes": [
                    {
                        "id": source_id,
                        "kind": "source",
                        "title": "User interview notes",
                        "sanitized_excerpt": "Teams repeat this review every quarter.",
                        "metadata": {
                            "source_kind": "user_text",
                            "retrieved_at": "2026-07-14T12:00:00Z",
                            "content_hash": f"sha256:{'a' * 64}",
                            "independence_key": "user_text:interview-notes",
                            "authority": {
                                "domain": None,
                                "publisher": "user supplied",
                                "authoritative": False,
                                "hierarchy_rank": 6,
                            },
                        },
                    }
                ],
                "edges": [],
            },
            "context_manifest": {},
            "context_hash": "user-source-context",
            "prior_stage_outputs": {"planning": {"output": planning}},
        },
        configuration=configuration(),
    )

    result = provider.research(stage_request)
    output = ResearchOutput.model_validate_json(json.dumps(result.output))

    assert [source.id for source in output.sources] == [source_id]
    assert output.sources[0].kind == "user_text"
    assert output.sources[0].url is None
    assert output.sources[0].sanitized_excerpt == "Teams repeat this review every quarter."
