from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


DEFAULT_USERS_ROOT = Path("data/users")
BASE_PREFERENCES_PATH = Path("data/preferences.yaml")


@dataclass(frozen=True)
class ProfilePaths:
    root: Path
    meta: Path
    token: Path
    preferences: Path
    learned_preferences: Path
    sources: Path
    candidates: Path
    discovery: Path
    state: Path


def users_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root)
    configured_root = os.getenv("FINN_SIGNAL_USERS_DIR")
    return Path(configured_root) if configured_root else DEFAULT_USERS_ROOT


def slugify_user_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "user"


def profile_paths(profile_id: str, root: str | Path | None = None) -> ProfilePaths:
    profile_root = users_root(root) / profile_id
    return ProfilePaths(
        root=profile_root,
        meta=profile_root / "profile.json",
        token=profile_root / "token.json",
        preferences=profile_root / "preferences.yaml",
        learned_preferences=profile_root / "learned_preferences.yaml",
        sources=profile_root / "newsletter_sources.yaml",
        candidates=profile_root / "newsletter_candidates.json",
        discovery=profile_root / "discovery_recommendations.json",
        state=profile_root / "state.json",
    )


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def split_interest_text(raw: str | list[str] | None) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        values = re.split(r"[\n,;]+", raw or "")
    seen = set()
    interests = []
    for value in values:
        interest = str(value).strip()
        key = interest.lower()
        if not interest or key in seen:
            continue
        interests.append(interest)
        seen.add(key)
    return interests


def profile_preferences(display_name: str, email: str, interests: list[str]) -> dict[str, Any]:
    base = read_yaml(BASE_PREFERENCES_PATH, {})
    user_name = display_name.strip() or email
    digest_style = dict(base.get("digest_style") or {})
    digest_style["include_why_user_cares"] = True
    digest_style.pop("include_why_finn_cares", None)

    return {
        "user": {
            "name": user_name,
            "digest_name": base.get("user", {}).get("digest_name", "Finn-Signal"),
        },
        "strong_interests": interests,
        "medium_interests": [],
        "always_include_if_major": base.get("always_include_if_major") or [],
        "digest_style": digest_style,
        "content_rules": base.get("content_rules") or {},
        "onboarding": {
            "raw_interests": interests,
            "created_from": "profile_onboarding",
        },
    }


def default_profile(
    display_name: str,
    email: str,
    profile_id: str,
    interests: list[str] | None = None,
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": profile_id,
        "display_name": display_name.strip() or email,
        "email": email.strip(),
        "interests": interests or [],
        "created_at": now,
        "updated_at": now,
        "schedule": {
            "enabled": True,
            "time": "11:00",
            "frequency": "daily",
        },
    }


def create_profile(
    display_name: str,
    email: str,
    interests: str | list[str] | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    email = email.strip()
    if "@" not in email:
        raise ValueError("Enter a valid email address.")
    interest_list = split_interest_text(interests)

    base_id = slugify_user_id(email)
    profile_id = base_id
    suffix = 2
    while profile_paths(profile_id, root).root.exists():
        profile_id = f"{base_id}-{suffix}"
        suffix += 1

    paths = profile_paths(profile_id, root)
    paths.root.mkdir(parents=True, exist_ok=True)

    profile = default_profile(display_name, email, profile_id, interest_list)
    write_json(paths.meta, profile)

    write_yaml(paths.preferences, profile_preferences(display_name, email, interest_list))

    write_yaml(paths.learned_preferences, {"topic_weights": {}, "source_weights": {}})
    write_yaml(paths.sources, {"sources": []})
    write_json(paths.state, {})

    return profile_with_status(profile, root=root)


def load_profile(profile_id: str, root: str | Path | None = None) -> dict[str, Any]:
    paths = profile_paths(profile_id, root)
    if not paths.meta.exists():
        raise FileNotFoundError(f"No profile named {profile_id}.")
    return profile_with_status(read_json(paths.meta, {}), root=root)


def save_profile(profile: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    profile = {**profile, "updated_at": datetime.now().isoformat(timespec="seconds")}
    write_json(profile_paths(profile["id"], root).meta, profile)
    return profile_with_status(profile, root=root)


def list_profiles(root: str | Path | None = None) -> list[dict[str, Any]]:
    base = users_root(root)
    if not base.exists():
        return []

    profiles = []
    for path in sorted(base.iterdir()):
        meta_path = path / "profile.json"
        if meta_path.exists():
            profiles.append(profile_with_status(read_json(meta_path, {}), root=root))
    return profiles


def profile_with_status(
    profile: dict[str, Any],
    root: str | Path | None = None,
) -> dict[str, Any]:
    paths = profile_paths(profile["id"], root)
    sources = read_sources(profile["id"], root=root)
    return {
        **profile,
        "gmail_connected": paths.token.exists(),
        "source_count": len([source for source in sources if source.get("enabled", True)]),
        "paths": {
            "root": str(paths.root),
            "sources": str(paths.sources),
            "token": str(paths.token),
        },
    }


def read_sources(profile_id: str, root: str | Path | None = None) -> list[dict[str, Any]]:
    data = read_yaml(profile_paths(profile_id, root).sources, {"sources": []})
    return data.get("sources") or []


def write_sources(
    profile_id: str,
    sources: list[dict[str, Any]],
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    write_yaml(profile_paths(profile_id, root).sources, {"sources": sources})
    return sources


def normalize_sender_list(raw: str | list[str]) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        values = re.split(r"[\n,]+", raw or "")
    return [value.strip() for value in values if value and value.strip()]


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    senders = normalize_sender_list(source.get("senders") or source.get("sender") or "")
    if not senders:
        raise ValueError("Add at least one sender email address.")

    name = str(source.get("name") or senders[0]).strip()
    now = datetime.now().isoformat(timespec="seconds")

    return {
        "name": name,
        "senders": senders,
        "enabled": bool(source.get("enabled", True)),
        "source_type": source.get("source_type", "manual"),
        "status": source.get("status", "receiving"),
        "reason": source.get("reason", ""),
        "topics": source.get("topics", []),
        "subscription_url": source.get("subscription_url", ""),
        "created_at": source.get("created_at", now),
        "updated_at": now,
    }


def upsert_source(
    profile_id: str,
    source: dict[str, Any],
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    new_source = normalize_source(source)
    sources = read_sources(profile_id, root=root)
    new_senders = {sender.lower() for sender in new_source["senders"]}

    replaced = False
    updated_sources = []
    for existing in sources:
        existing_senders = {sender.lower() for sender in existing.get("senders", [])}
        if existing_senders & new_senders:
            updated_sources.append({**existing, **new_source})
            replaced = True
        else:
            updated_sources.append(existing)

    if not replaced:
        updated_sources.append(new_source)

    return write_sources(profile_id, updated_sources, root=root)


def set_source_enabled(
    profile_id: str,
    sender: str,
    enabled: bool,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    sender_key = sender.strip().lower()
    sources = read_sources(profile_id, root=root)
    for source in sources:
        if sender_key in {value.lower() for value in source.get("senders", [])}:
            source["enabled"] = enabled
            source["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return write_sources(profile_id, sources, root=root)


def update_schedule(
    profile_id: str,
    time: str,
    frequency: str,
    enabled: bool = True,
    root: str | Path | None = None,
) -> dict[str, Any]:
    if not re.match(r"^\d{2}:\d{2}$", time):
        raise ValueError("Use HH:MM time, for example 11:00.")

    hour, minute = [int(part) for part in time.split(":")]
    if hour > 23 or minute > 59:
        raise ValueError("Use a real 24-hour time.")

    if frequency not in {"daily", "weekdays", "every_other_day", "weekly"}:
        raise ValueError("Unsupported frequency.")

    profile = load_profile(profile_id, root=root)
    profile["schedule"] = {
        "enabled": enabled,
        "time": time,
        "frequency": frequency,
    }
    return save_profile(profile, root=root)
