from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.ranking import normalized_title


PRESERVED_FIELDS = [
    "url",
    "link",
    "newsletter_name",
    "newsletter_date",
    "email_sender",
    "email_subject",
    "email_id",
    "read_time",
    "is_sponsor",
    "entities",
]

EMPTY_VALUES = {None, "", "Unknown", "unknown"}


def source_from_item(item: dict[str, Any]) -> str | None:
    return (
        item.get("source")
        or item.get("newsletter_name")
        or item.get("email_sender")
    )


def should_fill(value: Any) -> bool:
    if value in EMPTY_VALUES:
        return True
    if isinstance(value, list) and not value:
        return True
    return False


def index_items_by_title(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = normalized_title(item)
        if key:
            indexed.setdefault(key, []).append(item)
    return indexed


def preserve_scored_item_metadata(
    scored_data: dict[str, Any],
    merged_items: dict[str, Any],
) -> dict[str, Any]:
    indexed = index_items_by_title(merged_items.get("items", []))
    updated = deepcopy(scored_data)

    for scored_item in updated.get("scored_items", []):
        key = normalized_title(scored_item)
        candidates = indexed.get(key) or []
        if not candidates:
            continue

        original = candidates.pop(0)

        for field in PRESERVED_FIELDS:
            if field in original and should_fill(scored_item.get(field)):
                scored_item[field] = original.get(field)

        original_source = source_from_item(original)
        if original_source and should_fill(scored_item.get("source")):
            scored_item["source"] = original_source

    return updated
