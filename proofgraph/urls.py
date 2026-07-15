from django.urls import path

from proofgraph.generation.api import (
    generation_run_cancel,
    generation_run_collection,
    generation_run_detail,
    generation_run_retry,
    source_collection,
    source_detail,
    source_ingestion_detail,
)
from proofgraph.generation.sse import canvas_events
from proofgraph.graph.api import canvas_collection, canvas_detail, canvas_operations
from proofgraph.runtime.views import health

urlpatterns = [
    path("api/health", health, name="health"),
    path("api/canvases", canvas_collection, name="canvas-collection"),
    path("api/canvases/<uuid:canvas_id>", canvas_detail, name="canvas-detail"),
    path(
        "api/canvases/<uuid:canvas_id>/generation-runs",
        generation_run_collection,
        name="generation-run-collection",
    ),
    path(
        "api/canvases/<uuid:canvas_id>/sources",
        source_collection,
        name="source-collection",
    ),
    path(
        "api/canvases/<uuid:canvas_id>/events",
        canvas_events,
        name="canvas-events",
    ),
    path(
        "api/generation-runs/<uuid:run_id>",
        generation_run_detail,
        name="generation-run-detail",
    ),
    path(
        "api/generation-runs/<uuid:run_id>/cancel",
        generation_run_cancel,
        name="generation-run-cancel",
    ),
    path(
        "api/generation-runs/<uuid:run_id>/retry",
        generation_run_retry,
        name="generation-run-retry",
    ),
    path(
        "api/source-ingestions/<uuid:ingestion_id>",
        source_ingestion_detail,
        name="source-ingestion-detail",
    ),
    path(
        "api/sources/<uuid:source_id>",
        source_detail,
        name="source-detail",
    ),
    path(
        "api/canvases/<uuid:canvas_id>/operations",
        canvas_operations,
        name="canvas-operations",
    ),
]
