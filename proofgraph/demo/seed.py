from __future__ import annotations

from typing import Final

from proofgraph.graph.models import Canvas, Node, NodeKind

SEED_TITLE: Final = "Security questionnaire opportunity"
SEED_NODES: Final = (
    {
        "kind": NodeKind.GOAL,
        "title": "Reduce security questionnaire work",
        "body": (
            "Help a small B2B SaaS team reduce repeated security questionnaire effort and "
            "deal delay."
        ),
        "metadata": {"fixture_role": "goal"},
        "position": {"x": 72, "y": 72},
    },
    {
        "kind": NodeKind.CONSTRAINT,
        "title": "Six-week MVP",
        "body": "A useful MVP must be buildable within six weeks.",
        "metadata": {
            "fixture_role": "constraint_horizon",
            "context_scope": "global",
            "pinned": True,
        },
        "position": {"x": 72, "y": 248},
    },
    {
        "kind": NodeKind.CONSTRAINT,
        "title": "Approved evidence only",
        "body": "Use public or user-approved evidence only.",
        "metadata": {
            "fixture_role": "constraint_sources",
            "context_scope": "global",
            "pinned": True,
        },
        "position": {"x": 358, "y": 248},
    },
    {
        "kind": NodeKind.CONSTRAINT,
        "title": "Small technical team",
        "body": "The builder is a small technical team.",
        "metadata": {
            "fixture_role": "constraint_team",
            "context_scope": "global",
            "pinned": True,
        },
        "position": {"x": 644, "y": 248},
    },
)


def create_seeded_canvas() -> Canvas:
    canvas = Canvas.objects.create(title=SEED_TITLE)
    Node.objects.bulk_create(
        [
            Node(
                canvas=canvas,
                kind=spec["kind"],
                title=spec["title"],
                body=spec["body"],
                metadata=spec["metadata"],
                position=spec["position"],
            )
            for spec in SEED_NODES
        ]
    )
    return canvas
