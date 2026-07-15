import asyncio
import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client, override_settings

from proofgraph.generation.models import GenerationEvent
from proofgraph.generation.sse import canvas_event_stream
from proofgraph.graph.models import Canvas, Node, NodeKind

pytestmark = pytest.mark.django_db(transaction=True)

TEST_COMPOSITION = "proofgraph.generation.testing.phase2_test_composition"


async def replay_all(canvas_id, count: int) -> list[bytes]:  # type: ignore[no-untyped-def]
    stream = canvas_event_stream(canvas_id, after=0, poll_seconds=0.001)
    chunks = []
    try:
        for _ in range(count):
            chunks.append(await asyncio.wait_for(anext(stream), timeout=1))
    finally:
        await stream.aclose()
    return chunks


@override_settings(
    GENERATION_COMPOSITION_FACTORY=TEST_COMPOSITION,
    GENERATION_MAX_JOBS_PER_WORKER=1,
)
def test_phase_two_api_worker_status_and_replay_flow() -> None:
    canvas = Canvas.objects.create(title="Phase 2 end to end")
    goal = Node.objects.create(canvas=canvas, kind=NodeKind.GOAL, title="Goal")
    constraint = Node.objects.create(
        canvas=canvas,
        kind=NodeKind.CONSTRAINT,
        title="Constraint",
        metadata={"context_scope": "global", "pinned": True},
    )
    client = Client()
    created = client.post(
        f"/api/canvases/{canvas.id}/generation-runs",
        data=json.dumps(
            {
                "operation": "generate_strategies",
                "selected_node_ids": [str(goal.id), str(constraint.id)],
                "expected_node_versions": {str(goal.id): 1, str(constraint.id): 1},
                "execution_profile_id": "phase2_test_v1",
                "idempotency_key": "phase-two-flow",
            }
        ),
        content_type="application/json",
    )
    assert created.status_code == 202

    output = StringIO()
    call_command(
        "run_generation_worker",
        poll_interval=0.001,
        worker_id="phase-two-worker",
        stdout=output,
    )
    status = client.get(f"/api/generation-runs/{created.json()['run_id']}")

    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["ready_patch_id"] is not None
    assert "stopped after 1 job(s)" in output.getvalue()

    event_count = GenerationEvent.objects.filter(canvas=canvas).count()
    replay = b"".join(asyncio.run(replay_all(canvas.id, event_count))).decode()
    assert replay.count("id: ") == event_count
    assert "event: run.completed" in replay
