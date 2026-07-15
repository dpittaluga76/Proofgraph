import uuid

from django.db import transaction

from proofgraph.graph.exceptions import GraphAPIError
from proofgraph.graph.models import Canvas


def delete_canvas(canvas_id: uuid.UUID) -> None:
    with transaction.atomic():
        canvas = Canvas.objects.select_for_update().filter(pk=canvas_id).first()
        if canvas is None:
            raise GraphAPIError(
                status=404,
                code="canvas_not_found",
                message="Canvas not found.",
            )

        canvas.delete()
