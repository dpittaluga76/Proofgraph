import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.conf import settings
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone

from proofgraph.demo.cleanup import cleanup_expired_demo_sessions
from proofgraph.demo.models import DemoSession
from proofgraph.generation.models import (
    GenerationRun,
    GraphPatch,
    RunStatus,
    SourceIngestionRequest,
    SourceIngestionStatus,
)
from proofgraph.graph.models import Canvas, Node, NodeKind

pytestmark = pytest.mark.django_db(transaction=True)


def bootstrap(client: Client) -> tuple[DemoSession, dict[str, object]]:
    response = client.get("/api/demo/bootstrap")
    assert response.status_code == 200, response.content
    payload = response.json()
    session = DemoSession.objects.get(active_canvas_id=payload["canvas"]["id"])
    return session, payload


def generation_body(
    session: DemoSession,
    *,
    profile: str = "replay_v1",
    key: str = "demo-run",
) -> dict[str, object]:
    nodes = list(Node.objects.filter(canvas_id=session.active_canvas_id).order_by("id"))
    selected = [node for node in nodes if node.kind in {NodeKind.GOAL, NodeKind.CONSTRAINT}]
    return {
        "operation": "generate_strategies",
        "selected_node_ids": [str(node.id) for node in selected],
        "expected_node_versions": {str(node.id): node.version for node in selected},
        "instruction": "Generate distinct, evidence-aware strategies.",
        "execution_profile_id": profile,
        "idempotency_key": key,
        "regeneration_scope": None,
    }


def create_run(
    client: Client,
    session: DemoSession,
    *,
    profile: str = "replay_v1",
    key: str = "demo-run",
):
    return client.post(
        f"/api/canvases/{session.active_canvas_id}/generation-runs",
        data=json.dumps(generation_body(session, profile=profile, key=key)),
        content_type="application/json",
    )


@override_settings(DEMO_PUBLIC_MODE=True)
def test_bootstrap_uses_signed_cookie_exact_seed_and_isolated_canvas() -> None:
    first_client = Client()
    second_client = Client()

    first, first_payload = bootstrap(first_client)
    second, second_payload = bootstrap(second_client)

    assert first.id != second.id
    assert first.active_canvas_id != second.active_canvas_id
    assert first_payload["canvas"]["title"] == "Security questionnaire opportunity"
    assert {node["title"] for node in first_payload["canvas"]["nodes"]} == {
        "Reduce security questionnaire work",
        "Six-week MVP",
        "Approved evidence only",
        "Small technical team",
    }
    assert first_payload["session"] == {
        "expires_at": first.expires_at.isoformat(),
        "hybrid_run_count": 0,
        "hybrid_run_limit": 12,
        "primary_profile": "demo_hybrid_v1",
        "fallback_profile": "replay_v1",
    }
    assert second_payload["canvas"]["nodes"] != []
    cookie = first_client.cookies[settings.DEMO_COOKIE_NAME]
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["expires"]

    Canvas.objects.filter(pk=first.active_canvas_id).update(title="First visitor only")
    assert Canvas.objects.get(pk=second.active_canvas_id).title == (
        "Security questionnaire opportunity"
    )


@override_settings(DEMO_PUBLIC_MODE=True)
def test_active_canvas_has_unique_session_ownership() -> None:
    session, _payload = bootstrap(Client())

    with pytest.raises(IntegrityError), transaction.atomic():
        DemoSession.objects.create(
            active_canvas_id=session.active_canvas_id,
            expires_at=timezone.now() + timedelta(hours=1),
        )


@override_settings(DEMO_PUBLIC_MODE=True)
def test_invalid_cookie_is_not_authority_and_expired_bootstrap_rotates_session() -> None:
    client = Client()
    session, _payload = bootstrap(client)
    canvas_id = session.active_canvas_id
    signed_cookie = client.cookies[settings.DEMO_COOKIE_NAME].value

    client.cookies[settings.DEMO_COOKIE_NAME] = f"{signed_cookie}tampered"
    forged = client.get(f"/api/canvases/{canvas_id}")
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "demo_session_required"

    client.cookies[settings.DEMO_COOKIE_NAME] = signed_cookie
    session.expires_at = timezone.now() - timedelta(seconds=1)
    session.save(update_fields=["expires_at"])
    expired = client.get(f"/api/canvases/{canvas_id}")
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "demo_session_expired"
    expired_mutation = client.patch(
        f"/api/canvases/{canvas_id}",
        data=json.dumps({"title": "Must not change"}),
        content_type="application/json",
    )
    assert expired_mutation.status_code == 401
    assert Canvas.objects.get(pk=canvas_id).title == "Security questionnaire opportunity"

    replacement, replacement_payload = bootstrap(client)
    assert replacement.id != session.id
    assert replacement.active_canvas_id != canvas_id
    assert replacement_payload["canvas"]["title"] == "Security questionnaire opportunity"
    assert DemoSession.objects.filter(pk=session.id).exists()


@override_settings(DEMO_PUBLIC_MODE=True)
def test_reset_preserves_expiry_and_quota_and_fences_nonterminal_work() -> None:
    client = Client()
    session, _payload = bootstrap(client)
    created = create_run(client, session)
    assert created.status_code == 202, created.content
    run_id = created.json()["run_id"]
    old_canvas_id = session.active_canvas_id
    original_expiry = session.expires_at
    DemoSession.objects.filter(pk=session.id).update(hybrid_run_count=7)

    response = client.post(
        "/api/demo/reset",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    session.refresh_from_db()
    assert session.active_canvas_id != old_canvas_id
    assert session.expires_at == original_expiry
    assert session.hybrid_run_count == 7
    assert not Canvas.objects.filter(pk=old_canvas_id).exists()
    assert not GenerationRun.objects.filter(pk=run_id).exists()
    assert response.json()["canvas"]["title"] == "Security questionnaire opportunity"


@override_settings(DEMO_PUBLIC_MODE=True)
def test_cross_session_resource_reads_and_mutations_are_non_enumerating() -> None:
    owner = Client()
    stranger = Client()
    owner_session, _payload = bootstrap(owner)
    bootstrap(stranger)
    created = create_run(owner, owner_session)
    assert created.status_code == 202, created.content
    run = GenerationRun.objects.get(pk=created.json()["run_id"])
    patch = GraphPatch.objects.create(
        run=run,
        canvas_id=owner_session.active_canvas_id,
        base_canvas_revision=run.base_canvas_revision,
        operations=[],
    )
    source = Node.objects.create(
        canvas_id=owner_session.active_canvas_id,
        kind=NodeKind.SOURCE,
        title="Approved source",
        body="Safe retained excerpt.",
    )
    ingestion = SourceIngestionRequest.objects.create(
        canvas_id=owner_session.active_canvas_id,
        operation_key="completed-source",
        request_fingerprint="fixture-fingerprint",
        status=SourceIngestionStatus.COMPLETED,
        result_source_node=source,
    )

    canvas_id = owner_session.active_canvas_id
    endpoints = [
        ("get", f"/api/canvases/{canvas_id}", None),
        ("patch", f"/api/canvases/{canvas_id}", {"title": "Stolen"}),
        ("delete", f"/api/canvases/{canvas_id}", None),
        ("get", f"/api/canvases/{canvas_id}/operations?after=0", None),
        ("post", f"/api/canvases/{canvas_id}/operations", {}),
        ("post", f"/api/canvases/{canvas_id}/generation-runs", {}),
        ("get", f"/api/canvases/{canvas_id}/events?after=0", None),
        ("post", f"/api/canvases/{canvas_id}/sources", {}),
        ("get", f"/api/generation-runs/{run.id}", None),
        ("post", f"/api/generation-runs/{run.id}/cancel", {}),
        ("post", f"/api/generation-runs/{run.id}/retry", {}),
        ("get", f"/api/graph-patches/{patch.id}", None),
        ("post", f"/api/graph-patches/{patch.id}/apply", {}),
        ("post", f"/api/graph-patches/{patch.id}/reject", {}),
        ("post", f"/api/graph-patches/{patch.id}/regenerate", {}),
        ("get", f"/api/sources/{source.id}", None),
        ("get", f"/api/source-ingestions/{ingestion.id}", None),
    ]

    for method, path, body in endpoints:
        request = getattr(stranger, method)
        kwargs = (
            {"data": json.dumps(body), "content_type": "application/json"}
            if body is not None
            else {}
        )
        response = request(path, **kwargs)
        assert response.status_code == 404, (method, path, response.content)
        assert response.json()["error"]["code"] == "resource_not_found"

    run.refresh_from_db()
    patch.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    assert patch.status == "pending"
    assert Canvas.objects.get(pk=canvas_id).title == "Security questionnaire opportunity"


@override_settings(
    DEMO_PUBLIC_MODE=True,
    OPENAI_API_KEY="test-key",
    DEMO_SESSION_HYBRID_RUN_LIMIT=1,
    DEMO_SESSION_CONCURRENT_RUN_LIMIT=99,
)
def test_profile_allowlist_session_quota_and_reset_cannot_evade_quota() -> None:
    client = Client()
    session, _payload = bootstrap(client)

    live = create_run(client, session, profile="live_v1", key="live")
    unknown = create_run(client, session, profile="unregistered", key="unknown")
    first = create_run(client, session, profile="demo_hybrid_v1", key="hybrid-1")
    second = create_run(client, session, profile="demo_hybrid_v1", key="hybrid-2")

    assert live.status_code == unknown.status_code == 403
    assert live.json()["error"]["code"] == "demo_profile_not_allowed"
    assert unknown.json()["error"]["code"] == "demo_profile_not_allowed"
    assert first.status_code == 202, first.content
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "demo_session_quota_exhausted"
    assert second.json()["error"]["details"]["fallback_profile"] == "replay_v1"

    reset = client.post(
        "/api/demo/reset",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert reset.status_code == 200
    session.refresh_from_db()
    still_limited = create_run(
        client,
        session,
        profile="demo_hybrid_v1",
        key="hybrid-after-reset",
    )
    replay = create_run(client, session, profile="replay_v1", key="replay-after-limit")
    assert still_limited.status_code == 429
    assert replay.status_code == 202, replay.content
    assert GenerationRun.objects.get(pk=replay.json()["run_id"]).demo_session_id == session.id


@override_settings(
    DEMO_PUBLIC_MODE=True,
    OPENAI_API_KEY="test-key",
    DEMO_GLOBAL_HYBRID_RUN_LIMIT=1,
    DEMO_SESSION_CONCURRENT_RUN_LIMIT=99,
)
def test_global_hybrid_circuit_breaker_offers_replay() -> None:
    first_client = Client()
    second_client = Client()
    first_session, _payload = bootstrap(first_client)
    second_session, _payload = bootstrap(second_client)

    first = create_run(first_client, first_session, profile="demo_hybrid_v1", key="global-1")
    blocked = create_run(
        second_client,
        second_session,
        profile="demo_hybrid_v1",
        key="global-2",
    )
    replay = create_run(second_client, second_session, profile="replay_v1", key="global-replay")

    assert first.status_code == 202
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "demo_global_quota_exhausted"
    assert blocked.json()["error"]["details"]["fallback_profile"] == "replay_v1"
    assert replay.status_code == 202


@override_settings(
    DEMO_PUBLIC_MODE=True,
    OPENAI_API_KEY="test-key",
    DEMO_SESSION_CONCURRENT_RUN_LIMIT=1,
    DEMO_SESSION_HYBRID_RUN_LIMIT=99,
    DEMO_GLOBAL_HYBRID_RUN_LIMIT=99,
)
def test_concurrent_hybrid_requests_are_serialized_by_session_lock() -> None:
    client = Client()
    session, _payload = bootstrap(client)
    cookie = client.cookies[settings.DEMO_COOKIE_NAME].value

    def submit(key: str) -> int:
        close_old_connections()
        threaded_client = Client()
        threaded_client.cookies[settings.DEMO_COOKIE_NAME] = cookie
        try:
            return create_run(
                threaded_client,
                DemoSession.objects.get(pk=session.id),
                profile="demo_hybrid_v1",
                key=key,
            ).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(submit, ["race-1", "race-2"]))

    assert statuses == [202, 429]
    session.refresh_from_db()
    assert session.hybrid_run_count == 1
    assert GenerationRun.objects.filter(demo_session=session).count() == 1


@override_settings(DEMO_PUBLIC_MODE=True)
def test_csrf_is_required_for_demo_reset() -> None:
    client = Client(enforce_csrf_checks=True)
    session, _payload = bootstrap(client)
    old_canvas_id = session.active_canvas_id

    denied = client.post(
        "/api/demo/reset",
        data=json.dumps({}),
        content_type="application/json",
    )
    allowed = client.post(
        "/api/demo/reset",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    session.refresh_from_db()
    assert session.active_canvas_id != old_canvas_id


@override_settings(DEMO_PUBLIC_MODE=True)
def test_cleanup_waits_for_live_lease_then_fences_and_deletes_expired_session() -> None:
    client = Client()
    session, _payload = bootstrap(client)
    created = create_run(client, session)
    run = GenerationRun.objects.get(pk=created.json()["run_id"])
    now = timezone.now()
    lease_token = uuid.uuid4()
    GenerationRun.objects.filter(pk=run.id).update(
        status=RunStatus.RUNNING,
        worker_id="active-worker",
        lease_token=lease_token,
        lease_epoch=1,
        attempt=1,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=5),
        started_at=now,
    )
    DemoSession.objects.filter(pk=session.id).update(expires_at=now - timedelta(seconds=1))

    assert cleanup_expired_demo_sessions() == 0
    run.refresh_from_db()
    assert run.cancel_requested_at is not None
    assert DemoSession.objects.filter(pk=session.id).exists()

    GenerationRun.objects.filter(pk=run.id).update(lease_expires_at=now - timedelta(seconds=1))
    assert cleanup_expired_demo_sessions() == 1
    assert not DemoSession.objects.filter(pk=session.id).exists()
    assert not Canvas.objects.filter(pk=session.active_canvas_id).exists()
    assert not GenerationRun.objects.filter(pk=run.id).exists()


@override_settings(DEMO_PUBLIC_MODE=True)
def test_concurrent_cleanup_workers_claim_distinct_expired_sessions() -> None:
    first, _payload = bootstrap(Client())
    second, _payload = bootstrap(Client())
    DemoSession.objects.filter(pk__in=[first.id, second.id]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    def clean_one(_index: int) -> int:
        close_old_connections()
        try:
            return cleanup_expired_demo_sessions(limit=1)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(clean_one, range(2)))

    assert results == [1, 1]
    assert not DemoSession.objects.filter(pk__in=[first.id, second.id]).exists()


def test_demo_active_run_and_expiry_queries_use_phase_five_indexes() -> None:
    now = timezone.now()
    session = DemoSession.objects.create(
        expires_at=now + timedelta(hours=1),
    )
    canvas = Canvas.objects.create(title="Demo index plans")
    active_runs = [
        GenerationRun(
            canvas=canvas,
            demo_session=session,
            operation="generate_strategies",
            idempotency_key=f"active-{index}",
            request_fingerprint=f"active-{index}",
            base_canvas_revision=0,
            context_snapshot={},
            context_manifest={},
            context_hash="hash",
            selected_node_ids=[],
            expected_node_versions={},
            execution_configuration={"profile_id": "demo_hybrid_v1"},
        )
        for index in range(100)
    ]
    completed_runs = [
        GenerationRun(
            canvas=canvas,
            demo_session=session,
            operation="generate_strategies",
            idempotency_key=f"completed-{index}",
            request_fingerprint=f"completed-{index}",
            status=RunStatus.COMPLETED,
            completed_at=now,
            base_canvas_revision=0,
            context_snapshot={},
            context_manifest={},
            context_hash="hash",
            selected_node_ids=[],
            expected_node_versions={},
            execution_configuration={"profile_id": "demo_hybrid_v1"},
        )
        for index in range(4_000)
    ]
    GenerationRun.objects.bulk_create([*active_runs, *completed_runs], batch_size=500)
    DemoSession.objects.bulk_create(
        [
            DemoSession(
                expires_at=(
                    now - timedelta(seconds=index + 1)
                    if index < 100
                    else now + timedelta(days=index)
                )
            )
            for index in range(4_000)
        ],
        batch_size=500,
    )

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE generation_run")
        cursor.execute("ANALYZE demo_session")
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM generation_run
            WHERE demo_session_id = %s
              AND status IN ('queued', 'running')
              AND execution_configuration ->> 'profile_id' = 'demo_hybrid_v1'
            """,
            [str(session.id)],
        )
        active_plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id
            FROM demo_session
            WHERE expires_at <= now()
            ORDER BY expires_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        expiry_plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "run_demo_active_idx" in active_plan
    assert "Seq Scan" not in active_plan
    assert "demo_session_expiry_idx" in expiry_plan
    assert "Seq Scan" not in expiry_plan
