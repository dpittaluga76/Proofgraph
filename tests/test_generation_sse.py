import asyncio
import http.client
import socket
import time
import uuid
from threading import Thread

import pytest
import uvicorn
from django.db import transaction
from django.test import Client, override_settings

from proofgraph.asgi import application
from proofgraph.generation.events import append_event_locked
from proofgraph.generation.models import GenerationEventType, GenerationRun, RunOperation
from proofgraph.generation.sse import canvas_event_stream
from proofgraph.graph.models import Canvas

pytestmark = pytest.mark.django_db(transaction=True)


def make_run(canvas: Canvas) -> GenerationRun:
    return GenerationRun.objects.create(
        canvas=canvas,
        operation=RunOperation.GENERATE_STRATEGIES,
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint=str(uuid.uuid4()),
        base_canvas_revision=0,
        context_snapshot={},
        context_manifest={},
        context_hash="hash",
        selected_node_ids=[],
        expected_node_versions={},
        execution_configuration={},
    )


def append(run: GenerationRun, event_type: str) -> None:
    with transaction.atomic():
        locked = GenerationRun.objects.select_for_update().get(pk=run.pk)
        append_event_locked(locked, event_type, {"label": event_type})


async def take_chunks(canvas_id: uuid.UUID, after: int, count: int) -> list[bytes]:
    stream = canvas_event_stream(canvas_id, after=after, poll_seconds=0.001)
    chunks = []
    try:
        for _ in range(count):
            chunks.append(await asyncio.wait_for(anext(stream), timeout=1))
    finally:
        await stream.aclose()
    return chunks


def test_interleaved_runs_replay_in_one_gapless_canvas_sequence() -> None:
    canvas = Canvas.objects.create(title="SSE")
    first = make_run(canvas)
    second = make_run(canvas)
    append(first, GenerationEventType.RUN_STARTED)
    append(second, GenerationEventType.RUN_STARTED)
    append(first, GenerationEventType.STAGE_STARTED)
    append(second, GenerationEventType.STAGE_PROGRESS)

    chunks = asyncio.run(take_chunks(canvas.id, 0, 4))
    replay = b"".join(chunks).decode()

    assert [line for line in replay.splitlines() if line.startswith("id: ")] == [
        "id: 1",
        "id: 2",
        "id: 3",
        "id: 4",
    ]
    assert replay.count(str(first.id)) == 2
    assert replay.count(str(second.id)) == 2


def test_reconnect_cursor_skips_already_delivered_events() -> None:
    canvas = Canvas.objects.create(title="Reconnect")
    run = make_run(canvas)
    append(run, GenerationEventType.RUN_STARTED)
    append(run, GenerationEventType.STAGE_STARTED)
    append(run, GenerationEventType.STAGE_PROGRESS)

    chunks = asyncio.run(take_chunks(canvas.id, 2, 1))

    assert b"id: 3\n" in chunks[0]
    assert b"id: 1\n" not in chunks[0]
    assert b"id: 2\n" not in chunks[0]


def test_idle_stream_emits_keepalive_comment() -> None:
    canvas = Canvas.objects.create(title="Heartbeat")

    async def receive() -> bytes:
        stream = canvas_event_stream(
            canvas.id,
            after=0,
            poll_seconds=0.001,
            heartbeat_seconds=0.002,
        )
        try:
            return await asyncio.wait_for(anext(stream), timeout=1)
        finally:
            await stream.aclose()

    assert asyncio.run(receive()) == b": keepalive\n\n"


def test_sse_response_headers_disable_buffering_and_caching() -> None:
    canvas = Canvas.objects.create(title="Headers")

    response = Client().get(f"/api/canvases/{canvas.id}/events?after=0")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["Cache-Control"] == "no-cache, no-transform"
    assert response["X-Accel-Buffering"] == "no"
    assert "Content-Length" not in response.headers
    response.close()


def _available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _read_sse_message(response: http.client.HTTPResponse) -> bytes:
    lines: list[bytes] = []
    while True:
        line = response.readline()
        if not line:
            raise AssertionError("The live ASGI stream closed before the next SSE message.")
        if line in {b"\n", b"\r\n"}:
            return b"".join(lines)
        lines.append(line)


@override_settings(
    GENERATION_SSE_POLL_SECONDS=0.01,
    GENERATION_SSE_HEARTBEAT_SECONDS=5.0,
)
def test_live_uvicorn_connection_delivers_new_commits_incrementally() -> None:
    canvas = Canvas.objects.create(title="Live ASGI SSE")
    run = make_run(canvas)
    append(run, GenerationEventType.RUN_STARTED)
    port = _available_port()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="error",
        )
    )
    server_thread = Thread(target=server.run, daemon=True)
    connection: http.client.HTTPConnection | None = None
    server_thread.start()
    deadline = time.monotonic() + 5
    while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        assert server.started
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request(
            "GET",
            f"/api/canvases/{canvas.id}/events?after=0",
            headers={"Accept": "text/event-stream"},
        )
        response = connection.getresponse()

        assert response.status == 200
        assert b"event: run.started" in _read_sse_message(response)

        append(run, GenerationEventType.STAGE_STARTED)
        incremental = _read_sse_message(response)

        assert b"id: 2" in incremental
        assert b"event: stage.started" in incremental
    finally:
        if connection is not None:
            connection.close()
        server.should_exit = True
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()
