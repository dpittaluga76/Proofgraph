import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from proofgraph.generation.models import ResearchQueryCache, SourceContentCache
from proofgraph.generation.research_cache import ResearchCacheStore
from proofgraph.graph.models import Canvas


@pytest.mark.django_db
def test_health_reports_postgresql_ready() -> None:
    response = Client().get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert "csrftoken" in response.cookies


@pytest.mark.django_db
def test_health_cookie_authorizes_browser_canvas_mutations() -> None:
    client = Client(enforce_csrf_checks=True)
    health_response = client.get("/api/health")
    csrf_token = health_response.cookies["csrftoken"].value

    create_response = client.post(
        "/api/canvases",
        data=json.dumps({"title": "Browser canvas"}),
        content_type="application/json",
        HTTP_ORIGIN="http://127.0.0.1:5173",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert create_response.status_code == 201
    assert create_response.json()["canvas"]["title"] == "Browser canvas"


@pytest.mark.django_db
def test_generation_worker_can_start_once() -> None:
    output = StringIO()

    call_command("run_generation_worker", once=True, stdout=output)

    assert "Generation worker connected to PostgreSQL." in output.getvalue()


@pytest.mark.django_db(transaction=True)
def test_generation_worker_physically_removes_expired_caches() -> None:
    canvas = Canvas.objects.create(title="Worker cache cleanup")
    store = ResearchCacheStore()
    store.put_query(
        canvas=canvas,
        query="expired query",
        provider_identity="provider:v1",
        strategy_version="strategy:v1",
        prompt_version="prompt:v1",
        context_hash="context",
        result={"sources": []},
    )
    store.put_source(
        canvas=canvas,
        normalized_url="https://example.com/expired",
        content_hash="sha256:" + ("a" * 64),
        retrieval_metadata={"sanitized_excerpt": "Derived fixture excerpt."},
    )
    expired_at = timezone.now() - timedelta(seconds=1)
    retrieved_at = expired_at - timedelta(hours=2)
    fresh_until = expired_at - timedelta(hours=1)
    ResearchQueryCache.objects.update(
        retrieved_at=retrieved_at,
        fresh_until=fresh_until,
        expires_at=expired_at,
    )
    SourceContentCache.objects.update(
        retrieved_at=retrieved_at,
        fresh_until=fresh_until,
        expires_at=expired_at,
    )

    call_command("run_generation_worker", once=True, stdout=StringIO())

    assert not ResearchQueryCache.objects.exists()
    assert not SourceContentCache.objects.exists()
