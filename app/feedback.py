from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from openai import OpenAI

from app.config import get_cheap_model
from app.ranking import clamp, item_source, to_score


load_dotenv()

LEARNING_RATE = 0.03
MIN_WEIGHT = 0.25
MAX_WEIGHT = 2.0
MAX_NATURAL_LANGUAGE_DELTA = 0.25
RATING_TARGETS = {
    1: 2.0,
    2: 4.0,
    3: 6.0,
    4: 8.0,
    5: 10.0,
}

DEFAULT_LEARNED_PREFERENCES = {
    "topic_weights": {},
    "source_weights": {},
    "rules": [],
    "style_notes": [],
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_learned_preferences(path: Path) -> dict[str, Any]:
    if not path.exists():
        return deepcopy(DEFAULT_LEARNED_PREFERENCES)

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    learned = deepcopy(DEFAULT_LEARNED_PREFERENCES)
    learned.update(data)
    learned["topic_weights"] = learned.get("topic_weights") or {}
    learned["source_weights"] = learned.get("source_weights") or {}
    learned["rules"] = learned.get("rules") or []
    learned["style_notes"] = learned.get("style_notes") or []

    return learned


def save_learned_preferences(path: Path, learned: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(learned, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def normalize_key(value: str) -> str:
    return normalize_text(value).lower()


def append_unique(values: list[str], new_values: list[str], max_length: int = 300) -> list[str]:
    seen = {normalize_key(value) for value in values}

    for value in new_values:
        cleaned = normalize_text(str(value))[:max_length]
        if not cleaned:
            continue

        key = normalize_key(cleaned)
        if key in seen:
            continue

        values.append(cleaned)
        seen.add(key)

    return values


def validate_delta(value: Any) -> float:
    try:
        return clamp(float(value), -MAX_NATURAL_LANGUAGE_DELTA, MAX_NATURAL_LANGUAGE_DELTA)
    except (TypeError, ValueError):
        return 0.0


def update_weight(weights: dict[str, Any], key: str, delta: float) -> dict[str, Any]:
    cleaned_key = normalize_text(str(key))
    if not cleaned_key or not delta:
        return {
            "key": cleaned_key,
            "old": None,
            "new": None,
            "delta": 0.0,
            "applied": False,
        }

    old_weight = weights.get(cleaned_key, 1.0)
    try:
        old_weight = float(old_weight)
    except (TypeError, ValueError):
        old_weight = 1.0

    new_weight = round(clamp(old_weight + delta, MIN_WEIGHT, MAX_WEIGHT), 4)
    weights[cleaned_key] = new_weight

    return {
        "key": cleaned_key,
        "old": round(old_weight, 4),
        "new": new_weight,
        "delta": round(delta, 4),
        "applied": True,
    }


def split_feedback_sentences(raw_feedback: str) -> list[str]:
    normalized = raw_feedback.replace("\r", "\n")
    pieces = re.split(r"[\n.;]+|,(?=\s*(?:more|less|i do|i don't|dont|do not)\b)", normalized, flags=re.I)
    return [normalize_text(piece) for piece in pieces if normalize_text(piece)]


def strip_rating_text(raw_feedback: str) -> str:
    return re.sub(r"#?\d+\s*[:=]\s*[1-5]\b", " ", raw_feedback)


def clean_reply_text(raw_feedback: str) -> str:
    lines = raw_feedback.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = []

    quote_patterns = [
        r"^On .+ wrote:$",
        r"^-+ Forwarded message -+$",
        r"^From:\s+",
        r"^Sent from my ",
    ]

    for line in lines:
        stripped = line.strip()
        if any(re.search(pattern, stripped, flags=re.I) for pattern in quote_patterns):
            break

        if stripped.startswith(">"):
            continue

        kept.append(line)

    cleaned = "\n".join(kept).strip()
    return cleaned or raw_feedback.strip()


def clean_topic_phrase(phrase: str) -> str:
    cleaned = normalize_text(phrase)
    cleaned = re.sub(r"^(the|a|an)\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+item\s*#?\d*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+item$", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" .,!?:;\"'")
    return cleaned


def parse_feedback_locally(raw_feedback: str) -> dict[str, Any]:
    item_ratings = []
    seen_ratings = set()

    for match in re.finditer(r"(?:^|[\s,;])#?(\d{1,3})\s*[:=]\s*([1-5])\b", raw_feedback):
        item_number = int(match.group(1))
        rating = int(match.group(2))
        key = (item_number, rating)
        if key in seen_ratings:
            continue

        item_ratings.append(
            {
                "item_number": item_number,
                "rating": rating,
                "reason": "",
            }
        )
        seen_ratings.add(key)

    natural_text = strip_rating_text(raw_feedback)
    topic_adjustments = []
    rules = []
    style_notes = []

    for sentence in split_feedback_sentences(natural_text):
        more_match = re.search(r"\bmore(?:\s+like)?(?:\s+the)?\s+(.+)$", sentence, flags=re.I)
        less_match = re.search(r"\bless(?:\s+like)?(?:\s+the)?\s+(.+)$", sentence, flags=re.I)
        dont_care_match = re.search(
            r"\b(?:i\s+)?(?:don'?t|do\s+not)\s+care\s+about\s+(.+?)(?:\s+unless\s+(.+))?$",
            sentence,
            flags=re.I,
        )

        if more_match:
            topic = clean_topic_phrase(more_match.group(1))
            if topic:
                topic_adjustments.append(
                    {
                        "topic": topic,
                        "delta": 0.15,
                        "reason": f"User asked for more {topic}.",
                    }
                )
            continue

        if less_match:
            topic = clean_topic_phrase(less_match.group(1))
            if topic:
                topic_adjustments.append(
                    {
                        "topic": topic,
                        "delta": -0.15,
                        "reason": f"User asked for less {topic}.",
                    }
                )
            continue

        if dont_care_match:
            topic = clean_topic_phrase(dont_care_match.group(1))
            unless_clause = clean_topic_phrase(dont_care_match.group(2) or "")
            if topic:
                topic_adjustments.append(
                    {
                        "topic": topic,
                        "delta": -0.15,
                        "reason": f"User said they do not care about {topic}.",
                    }
                )
            if topic and unless_clause:
                rules.append(f"Include {topic} only when {unless_clause}.")
            continue

        if "generic" in sentence.lower() or "sharper" in sentence.lower():
            style_notes.append(sentence)

    return {
        "item_ratings": item_ratings,
        "topic_adjustments": topic_adjustments,
        "source_adjustments": [],
        "rules": rules,
        "style_notes": style_notes,
    }


def feedback_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "item_ratings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_number": {"type": "integer"},
                        "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                        "reason": {"type": "string"},
                    },
                    "required": ["item_number", "rating", "reason"],
                    "additionalProperties": False,
                },
            },
            "topic_adjustments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "delta": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["topic", "delta", "reason"],
                    "additionalProperties": False,
                },
            },
            "source_adjustments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "delta": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["source", "delta", "reason"],
                    "additionalProperties": False,
                },
            },
            "rules": {
                "type": "array",
                "items": {"type": "string"},
            },
            "style_notes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "item_ratings",
            "topic_adjustments",
            "source_adjustments",
            "rules",
            "style_notes",
        ],
        "additionalProperties": False,
    }


def parse_feedback_with_model(raw_feedback: str, prompt: str) -> dict[str, Any]:
    client = OpenAI()
    response = client.responses.create(
        model=get_cheap_model(),
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_feedback},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "finn_signal_feedback",
                "schema": feedback_schema(),
                "strict": True,
            }
        },
    )
    return json.loads(response.output_text)


def merge_parsed_feedback(local: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    merged = {
        "item_ratings": local.get("item_ratings", [])[:],
        "topic_adjustments": local.get("topic_adjustments", [])[:],
        "source_adjustments": local.get("source_adjustments", [])[:],
        "rules": local.get("rules", [])[:],
        "style_notes": local.get("style_notes", [])[:],
    }

    seen_ratings = {
        (rating.get("item_number"), rating.get("rating"))
        for rating in merged["item_ratings"]
    }

    for rating in model.get("item_ratings", []):
        key = (rating.get("item_number"), rating.get("rating"))
        if key not in seen_ratings:
            merged["item_ratings"].append(rating)
            seen_ratings.add(key)

    for key in ["topic_adjustments", "source_adjustments", "rules", "style_notes"]:
        merged[key].extend(model.get(key, []))

    return merged


def parse_feedback(
    raw_feedback: str,
    prompt_path: Path = Path("prompts/parse_feedback.md"),
    use_model: bool = True,
) -> dict[str, Any]:
    local = parse_feedback_locally(raw_feedback)

    if not use_model or not prompt_path.exists():
        return local

    try:
        prompt = prompt_path.read_text(encoding="utf-8")
        model = parse_feedback_with_model(raw_feedback, prompt)
        return merge_parsed_feedback(local, model)
    except Exception as error:
        print(f"Model feedback parse failed; using local parser only: {error}")
        return local


def manifest_item_map(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    mapped = {}
    for item in manifest.get("items", []):
        try:
            mapped[int(item["item_number"])] = item
        except (KeyError, TypeError, ValueError):
            continue

    return mapped


def apply_rating_update(
    learned: dict[str, Any],
    item: dict[str, Any],
    rating: int,
) -> list[dict[str, Any]]:
    scores = item.get("scores") or {}
    original_score = to_score(scores.get("final_score"))
    target_score = RATING_TARGETS[rating]
    error = target_score - original_score
    delta = LEARNING_RATE * error
    updates = []

    for topic in item.get("topic_tags") or []:
        updates.append(
            {
                "type": "topic_weight",
                **update_weight(learned["topic_weights"], str(topic), delta),
                "reason": f"Rating {rating} on item #{item.get('item_number')}",
            }
        )

    source = item_source(item)
    if source and source != "Unknown":
        updates.append(
            {
                "type": "source_weight",
                **update_weight(learned["source_weights"], source, delta),
                "reason": f"Rating {rating} on item #{item.get('item_number')}",
            }
        )

    return updates


def apply_parsed_feedback(
    raw_feedback: str,
    parsed_feedback: dict[str, Any],
    manifest: dict[str, Any],
    learned_preferences_path: Path = Path("data/learned_preferences.yaml"),
    feedback_log_path: Path = Path("data/feedback_log.jsonl"),
    message_id: str | None = None,
) -> dict[str, Any]:
    learned = load_learned_preferences(learned_preferences_path)
    items_by_number = manifest_item_map(manifest)
    applied_updates = []
    warnings = []

    for rating in parsed_feedback.get("item_ratings", []):
        try:
            item_number = int(rating.get("item_number"))
            rating_value = int(rating.get("rating"))
        except (TypeError, ValueError):
            warnings.append(f"Ignored invalid rating: {rating}")
            continue

        if rating_value not in RATING_TARGETS:
            warnings.append(f"Ignored out-of-range rating for item #{item_number}.")
            continue

        item = items_by_number.get(item_number)
        if not item:
            warnings.append(f"Ignored rating for unknown item #{item_number}.")
            continue

        applied_updates.extend(apply_rating_update(learned, item, rating_value))

    for adjustment in parsed_feedback.get("topic_adjustments", []):
        topic = normalize_text(str(adjustment.get("topic", "")))
        delta = validate_delta(adjustment.get("delta"))
        if not topic or not delta:
            continue

        applied_updates.append(
            {
                "type": "topic_weight",
                **update_weight(learned["topic_weights"], topic, delta),
                "reason": adjustment.get("reason", ""),
            }
        )

    for adjustment in parsed_feedback.get("source_adjustments", []):
        source = normalize_text(str(adjustment.get("source", "")))
        delta = validate_delta(adjustment.get("delta"))
        if not source or not delta:
            continue

        applied_updates.append(
            {
                "type": "source_weight",
                **update_weight(learned["source_weights"], source, delta),
                "reason": adjustment.get("reason", ""),
            }
        )

    learned["rules"] = append_unique(
        learned.get("rules") or [],
        parsed_feedback.get("rules") or [],
    )
    learned["style_notes"] = append_unique(
        learned.get("style_notes") or [],
        parsed_feedback.get("style_notes") or [],
    )

    save_learned_preferences(learned_preferences_path, learned)

    event = {
        "created_at": now_iso(),
        "digest_id": manifest.get("digest_id"),
        "message_id": message_id,
        "raw_feedback": raw_feedback,
        "parsed_feedback": parsed_feedback,
        "applied_updates": applied_updates,
        "warnings": warnings,
    }

    feedback_log_path.parent.mkdir(parents=True, exist_ok=True)
    with feedback_log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")

    return {
        "applied_updates": applied_updates,
        "warnings": warnings,
        "learned_preferences": learned,
    }
