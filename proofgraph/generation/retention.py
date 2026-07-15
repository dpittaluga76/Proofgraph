from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_RETAINED_EXCERPT_CHARS = 500

_RAW_SOURCE_FIELDS = {
    "document_text",
    "full_content",
    "full_text",
    "html",
    "page_content",
    "raw_content",
    "raw_document",
    "raw_html",
    "source_document",
}
_EXCERPT_FIELDS = {"excerpt", "sanitized_excerpt", "snippet"}
_SOURCE_CONTAINER_FIELDS = {
    "document",
    "page",
    "retrieved_content",
    "retrieved_contents",
    "retrieved_document",
    "retrieved_documents",
    "retrieved_page",
    "retrieved_pages",
    "source",
    "source_content",
    "source_material",
    "sources",
}
_SOURCE_TEXT_FIELDS = {"body", "content", "html", "markdown", "text"}
_SOURCE_METADATA_EXCERPT_FIELDS = {"description", "notes", "summary"}


class RetentionPolicyError(ValueError):
    """Raised before a payload that violates DQ-003 can be persisted."""


def _validate_excerpt(value: Any, *, path: str) -> None:
    if not isinstance(value, str):
        raise RetentionPolicyError(f"{path} must be a string")
    if len(value) > MAX_RETAINED_EXCERPT_CHARS:
        raise RetentionPolicyError(
            f"{path} exceeds {MAX_RETAINED_EXCERPT_CHARS} Unicode characters"
        )


def validate_retained_payload(
    value: Any,
    *,
    path: str = "payload",
    source_context: bool = False,
) -> None:
    if isinstance(value, Mapping):
        source_node = str(value.get("kind", "")).casefold() == "source"
        mapping_is_source = source_context or source_node
        for key, child in value.items():
            normalized = str(key).casefold()
            child_path = f"{path}.{key}"
            if normalized in _RAW_SOURCE_FIELDS:
                raise RetentionPolicyError(f"{child_path} may not retain raw source content")
            if normalized == "retained_content" and child is not None:
                raise RetentionPolicyError(f"{child_path} must remain null")
            if normalized in _EXCERPT_FIELDS:
                _validate_excerpt(child, path=child_path)
            if mapping_is_source and normalized in _SOURCE_TEXT_FIELDS:
                if source_node and normalized == "body":
                    _validate_excerpt(child, path=child_path)
                else:
                    raise RetentionPolicyError(
                        f"{child_path} may not retain raw source content; use sanitized_excerpt"
                    )
            if mapping_is_source and normalized in _SOURCE_METADATA_EXCERPT_FIELDS:
                _validate_excerpt(child, path=child_path)
            if mapping_is_source and normalized == "tags":
                if (
                    not isinstance(child, Sequence)
                    or isinstance(child, (str, bytes, bytearray))
                    or not all(isinstance(tag, str) for tag in child)
                ):
                    raise RetentionPolicyError(f"{child_path} must be a list of strings")
                if sum(len(tag) for tag in child) > MAX_RETAINED_EXCERPT_CHARS:
                    raise RetentionPolicyError(
                        f"{child_path} exceeds {MAX_RETAINED_EXCERPT_CHARS} Unicode characters"
                    )
            child_is_source = normalized in _SOURCE_CONTAINER_FIELDS
            if child_is_source and isinstance(child, str):
                raise RetentionPolicyError(
                    f"{child_path} may not retain raw source content; use sanitized_excerpt"
                )
            validate_retained_payload(
                child,
                path=child_path,
                source_context=mapping_is_source or child_is_source,
            )
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            validate_retained_payload(
                child,
                path=f"{path}[{index}]",
                source_context=source_context,
            )


def validate_progress_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    validate_retained_payload(payload)
    if (
        event_type in {"research.source_found", "evidence.extracted"}
        and payload.get("provisional") is not True
    ):
        raise RetentionPolicyError(f"{event_type} payloads must be explicitly provisional")
