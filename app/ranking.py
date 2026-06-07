from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any

import yaml


SCORE_WEIGHTS = {
    "finn_relevance": 0.40,
    "global_importance": 0.25,
    "novelty": 0.15,
    "actionability": 0.10,
    "source_quality": 0.10,
}

DEFAULT_MAX_DIGEST_ITEMS = 8
INCLUDE_THRESHOLD = 7.0
GLOBAL_IMPORTANCE_OVERRIDE = 9.0
USEFULNESS_OVERRIDE = 9.0


def load_yaml_file(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError:
        return {}


def max_digest_items(preferences: dict[str, Any] | None) -> int:
    if not preferences:
        return DEFAULT_MAX_DIGEST_ITEMS

    digest_style = preferences.get("digest_style") or {}
    try:
        return int(digest_style.get("max_items", DEFAULT_MAX_DIGEST_ITEMS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DIGEST_ITEMS


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def to_score(value: Any, default: float = 0.0) -> float:
    try:
        return clamp(float(value), 0.0, 10.0)
    except (TypeError, ValueError):
        return default


def item_source(item: dict[str, Any]) -> str:
    return (
        item.get("source")
        or item.get("newsletter_name")
        or item.get("email_sender")
        or "Unknown"
    )


def compute_base_score(scores: dict[str, Any]) -> float:
    total = 0.0

    for key, weight in SCORE_WEIGHTS.items():
        total += to_score(scores.get(key)) * weight

    return round(total, 2)


def canonical_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def matching_weight(
    weights: dict[str, Any],
    candidates: list[str],
) -> list[float]:
    if not weights:
        return []

    canonical_weights = {
        canonical_key(str(key)): value
        for key, value in weights.items()
    }

    matches = []
    for candidate in candidates:
        key = canonical_key(str(candidate))
        if key not in canonical_weights:
            continue

        try:
            matches.append(clamp(float(canonical_weights[key]), 0.25, 2.0))
        except (TypeError, ValueError):
            continue

    return matches


def learned_multiplier(
    item: dict[str, Any],
    learned_preferences: dict[str, Any] | None,
) -> float:
    if not learned_preferences:
        return 1.0

    topic_weights = learned_preferences.get("topic_weights") or {}
    source_weights = learned_preferences.get("source_weights") or {}
    topic_tags = [str(tag) for tag in item.get("topic_tags") or []]

    matches = []
    matches.extend(matching_weight(topic_weights, topic_tags))
    matches.extend(matching_weight(source_weights, [item_source(item)]))

    if not matches:
        return 1.0

    return clamp(sum(matches) / len(matches), 0.25, 2.0)


def normalize_scored_item(
    item: dict[str, Any],
    learned_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = deepcopy(item)
    scores = normalized.setdefault("scores", {})

    base_score = compute_base_score(scores)
    multiplier = learned_multiplier(normalized, learned_preferences)
    final_score = round(clamp(base_score * multiplier, 0.0, 10.0), 2)

    scores["base_score"] = base_score
    scores["learned_multiplier"] = round(multiplier, 3)
    scores["final_score"] = final_score

    normalized["source"] = item_source(normalized)
    normalized.setdefault("topic_tags", [])
    normalized["include_in_digest"] = should_include(normalized)

    return normalized


def should_include(item: dict[str, Any]) -> bool:
    scores = item.get("scores") or {}
    final_score = to_score(scores.get("final_score"))
    global_importance = to_score(scores.get("global_importance"))
    actionability = to_score(scores.get("actionability"))
    model_include = item.get("include_in_digest", True)

    if global_importance >= GLOBAL_IMPORTANCE_OVERRIDE:
        return True

    if actionability >= USEFULNESS_OVERRIDE and final_score >= 6.5:
        return True

    return bool(model_include) and final_score >= INCLUDE_THRESHOLD


def normalized_title(item: dict[str, Any]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(item.get("title", "")).lower()).strip()


def canonical_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("link") or "").strip().lower()


def is_duplicate(item: dict[str, Any], seen_titles: set[str], seen_urls: set[str]) -> bool:
    title = normalized_title(item)
    url = canonical_url(item)

    if url and url in seen_urls:
        return True

    if title and title in seen_titles:
        return True

    return False


def remember_item(item: dict[str, Any], seen_titles: set[str], seen_urls: set[str]) -> None:
    title = normalized_title(item)
    url = canonical_url(item)

    if title:
        seen_titles.add(title)

    if url:
        seen_urls.add(url)


def rank_scored_items(
    scored_data: dict[str, Any],
    learned_preferences: dict[str, Any] | None = None,
    max_items: int = DEFAULT_MAX_DIGEST_ITEMS,
) -> dict[str, Any]:
    normalized_items = [
        normalize_scored_item(item, learned_preferences)
        for item in scored_data.get("scored_items", [])
    ]

    normalized_items.sort(
        key=lambda item: (
            to_score((item.get("scores") or {}).get("final_score")),
            to_score((item.get("scores") or {}).get("global_importance")),
            to_score((item.get("scores") or {}).get("novelty")),
            to_score((item.get("scores") or {}).get("actionability")),
        ),
        reverse=True,
    )

    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    ranked_items = []

    for item in normalized_items:
        if is_duplicate(item, seen_titles, seen_urls):
            item["include_in_digest"] = False
            item["ranking_note"] = "Dropped from digest selection as a duplicate."
        else:
            remember_item(item, seen_titles, seen_urls)

        ranked_items.append(item)

    for rank, item in enumerate(ranked_items, start=1):
        item["rank"] = rank

    digest_sections = build_digest_sections(ranked_items, max_items=max_items)

    return {
        **scored_data,
        "scored_items": ranked_items,
        "digest_sections": digest_sections,
        "ranking": {
            "max_items": max_items,
            "include_threshold": INCLUDE_THRESHOLD,
            "global_importance_override": GLOBAL_IMPORTANCE_OVERRIDE,
        },
    }


def build_digest_sections(
    ranked_items: list[dict[str, Any]],
    max_items: int = DEFAULT_MAX_DIGEST_ITEMS,
) -> dict[str, list[dict[str, Any]] | dict[str, Any] | None]:
    included = [
        item for item in ranked_items
        if item.get("include_in_digest") and not item.get("ranking_note")
    ]

    top_count = min(6, max(0, max_items - 1), len(included))
    top_signals = included[:top_count]

    used_ids = {id(item) for item in top_signals}
    strange_candidates = [
        item for item in ranked_items
        if id(item) not in used_ids
        and not item.get("ranking_note")
        and to_score((item.get("scores") or {}).get("novelty")) >= 7.0
        and (
            item.get("include_in_digest")
            or to_score((item.get("scores") or {}).get("final_score")) >= 5.5
        )
    ]
    strange_candidates.sort(
        key=lambda item: (
            to_score((item.get("scores") or {}).get("novelty")),
            to_score((item.get("scores") or {}).get("global_importance")),
            to_score((item.get("scores") or {}).get("final_score")),
        ),
        reverse=True,
    )
    strange_attractor = strange_candidates[0] if strange_candidates else None

    if strange_attractor:
        used_ids.add(id(strange_attractor))

    remaining_slots = max(0, max_items - len(top_signals) - (1 if strange_attractor else 0))
    skipped = [
        item for item in ranked_items
        if id(item) not in used_ids
        and not item.get("ranking_note")
    ][: min(5, remaining_slots)]

    numbered_items = top_signals + ([strange_attractor] if strange_attractor else []) + skipped
    for number, item in enumerate(numbered_items, start=1):
        item["item_number"] = number

    return {
        "top_signals": top_signals,
        "strange_attractor": strange_attractor,
        "skipped_but_noted": skipped,
    }


def manifest_item(item: dict[str, Any]) -> dict[str, Any]:
    scores = item.get("scores") or {}

    return {
        "item_number": item.get("item_number"),
        "rank": item.get("rank"),
        "title": item.get("title"),
        "newsletter_name": item.get("newsletter_name"),
        "source": item_source(item),
        "topic_tags": item.get("topic_tags") or [],
        "scores": {
            "finn_relevance": scores.get("finn_relevance"),
            "global_importance": scores.get("global_importance"),
            "novelty": scores.get("novelty"),
            "actionability": scores.get("actionability"),
            "source_quality": scores.get("source_quality"),
            "base_score": scores.get("base_score"),
            "learned_multiplier": scores.get("learned_multiplier"),
            "final_score": scores.get("final_score"),
        },
        "summary": item.get("summary"),
        "why_finn_cares": item.get("why_finn_cares"),
        "why_world_cares": item.get("why_world_cares"),
        "url": item.get("url") or item.get("link"),
    }


def build_digest_manifest(
    ranked_data: dict[str, Any],
    digest_id: str,
    created_at: str | None = None,
    user_name: str | None = None,
) -> dict[str, Any]:
    sections = ranked_data.get("digest_sections") or {}
    items = []

    for item in sections.get("top_signals") or []:
        items.append(manifest_item(item))

    strange = sections.get("strange_attractor")
    if strange:
        items.append(manifest_item(strange))

    for item in sections.get("skipped_but_noted") or []:
        items.append(manifest_item(item))

    return {
        "digest_id": digest_id,
        "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
        "digest_version": "v3",
        "user_name": user_name or "you",
        "items": items,
    }
