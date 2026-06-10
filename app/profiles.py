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
RENDER_USERS_ROOT = Path("/var/data/users")
BASE_PREFERENCES_PATH = Path("data/preferences.yaml")
SUBSCRIPTION_ALIAS_TAG = "finnsignal"
PENDING_SOURCE_STATUSES = {
    "needs_subscription",
    "pending_subscription",
    "pending_confirmation",
    "manual_required",
    "failed_signup",
}


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
    if configured_root:
        return Path(configured_root)
    if RENDER_USERS_ROOT.parent.exists():
        return RENDER_USERS_ROOT
    return DEFAULT_USERS_ROOT


def storage_status(root: str | Path | None = None) -> dict[str, Any]:
    base = users_root(root)
    parent = base.parent
    configured_root = os.getenv("FINN_SIGNAL_USERS_DIR", "")
    render_runtime = any(
        os.getenv(name)
        for name in [
            "RENDER",
            "RENDER_SERVICE_ID",
            "RENDER_EXTERNAL_HOSTNAME",
            "RENDER_INSTANCE_ID",
        ]
    )
    expected_render_root = str(base).startswith(str(RENDER_USERS_ROOT.parent))
    parent_exists = parent.exists()
    root_exists = base.exists()
    writable = False
    error = ""

    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write_probe"
        probe.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
        root_exists = True
    except Exception as exc:
        error = str(exc)

    persistent = expected_render_root and parent_exists and writable
    warning = ""
    if render_runtime and not persistent:
        warning = (
            "Render is running without confirmed persistent profile storage. "
            "Attach a Render Disk mounted at /var/data and set FINN_SIGNAL_USERS_DIR=/var/data/users."
        )
    elif not configured_root and not expected_render_root:
        warning = "Using local fallback storage. This is fine locally, but not for hosted Render profiles."

    return {
        "path": str(base),
        "env_value": configured_root,
        "render_runtime": render_runtime,
        "expected_render_root": expected_render_root,
        "parent_exists": parent_exists,
        "root_exists": root_exists,
        "writable": writable,
        "persistent": persistent,
        "warning": warning,
        "error": error,
    }


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


def resolve_profile_id(profile_id_or_email: str, root: str | Path | None = None) -> str:
    value = profile_id_or_email.strip()
    base = users_root(root)
    candidates = [value]
    if "@" in value:
        candidates.append(slugify_user_id(value))

    for candidate in candidates:
        if (base / candidate / "profile.json").exists():
            return candidate

    if base.exists():
        for path in sorted(base.iterdir()):
            meta_path = path / "profile.json"
            if not meta_path.exists():
                continue
            profile = read_json(meta_path, {})
            if str(profile.get("email", "")).lower() == value.lower():
                return path.name

    return value


def find_profile_id_by_email(email: str, root: str | Path | None = None) -> str | None:
    value = email.strip().lower()
    if not value:
        return None

    base = users_root(root)
    slug = slugify_user_id(value)
    if (base / slug / "profile.json").exists():
        return slug

    if not base.exists():
        return None

    for path in sorted(base.iterdir()):
        meta_path = path / "profile.json"
        if not meta_path.exists():
            continue
        profile = read_json(meta_path, {})
        if str(profile.get("email", "")).lower() == value:
            return path.name

    return None


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


def default_subscription_email(email: str) -> str:
    value = email.strip()
    if "@" not in value:
        return value

    local, domain = value.rsplit("@", 1)
    if "+" in local:
        local = local.split("+", 1)[0]

    if domain.lower() in {"gmail.com", "googlemail.com"}:
        return f"{local}+{SUBSCRIPTION_ALIAS_TAG}@{domain}"

    return value


def normalize_subscription_email(email: str, fallback_email: str) -> str:
    value = email.strip()
    if not value:
        return default_subscription_email(fallback_email)
    if "@" not in value:
        raise ValueError("Enter a valid subscription email address.")
    return value


def hydrate_profile_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    email = str(profile.get("email", "")).strip()
    if "subscription_email" not in profile:
        profile = {**profile, "subscription_email": default_subscription_email(email)}
    return profile


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
    subscription_email: str = "",
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    email = email.strip()
    return {
        "id": profile_id,
        "display_name": display_name.strip() or email,
        "email": email,
        "subscription_email": normalize_subscription_email(subscription_email, email),
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
    subscription_email: str = "",
    root: str | Path | None = None,
) -> dict[str, Any]:
    email = email.strip()
    if "@" not in email:
        raise ValueError("Enter a valid email address.")
    provided_subscription_email = subscription_email.strip()
    interest_list = split_interest_text(interests)
    existing_profile_id = find_profile_id_by_email(email, root=root)
    if existing_profile_id:
        paths = profile_paths(existing_profile_id, root)
        profile = hydrate_profile_defaults(read_json(paths.meta, {}))
        if display_name.strip():
            profile["display_name"] = display_name.strip()
        profile["email"] = email
        if provided_subscription_email:
            profile["subscription_email"] = normalize_subscription_email(provided_subscription_email, email)
        if interest_list:
            profile["interests"] = interest_list
            write_yaml(
                paths.preferences,
                profile_preferences(profile["display_name"], email, interest_list),
            )
        return save_profile(profile, root=root)

    base_id = slugify_user_id(email)
    profile_id = base_id
    suffix = 2
    while profile_paths(profile_id, root).root.exists():
        profile_id = f"{base_id}-{suffix}"
        suffix += 1

    paths = profile_paths(profile_id, root)
    paths.root.mkdir(parents=True, exist_ok=True)

    subscription_email = normalize_subscription_email(subscription_email, email)
    profile = default_profile(display_name, email, profile_id, interest_list, subscription_email)
    write_json(paths.meta, profile)

    write_yaml(paths.preferences, profile_preferences(display_name, email, interest_list))

    write_yaml(paths.learned_preferences, {"topic_weights": {}, "source_weights": {}})
    write_yaml(paths.sources, {"sources": []})
    write_json(paths.state, {})

    return profile_with_status(profile, root=root)


def load_profile(profile_id: str, root: str | Path | None = None) -> dict[str, Any]:
    profile_id = resolve_profile_id(profile_id, root=root)
    paths = profile_paths(profile_id, root)
    if not paths.meta.exists():
        raise FileNotFoundError(f"No profile named {profile_id}.")
    profile = hydrate_profile_defaults(read_json(paths.meta, {}))
    return profile_with_status(profile, root=root)


def save_profile(profile: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    profile = hydrate_profile_defaults(profile)
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
            profiles.append(profile_with_status(hydrate_profile_defaults(read_json(meta_path, {})), root=root))
    return profiles


def profile_with_status(
    profile: dict[str, Any],
    root: str | Path | None = None,
) -> dict[str, Any]:
    paths = profile_paths(profile["id"], root)
    sources = read_sources(profile["id"], root=root)
    state = read_json(paths.state, {})
    receiving_sources = [
        source
        for source in sources
        if source.get("enabled", True) and source.get("status", "receiving") == "receiving"
    ]
    pending_sources = [
        source
        for source in sources
        if source.get("enabled", True) and source.get("status", "receiving") in PENDING_SOURCE_STATUSES
    ]
    return {
        **profile,
        "gmail_connected": paths.token.exists(),
        "source_count": len(receiving_sources),
        "pending_source_count": len(pending_sources),
        "total_source_count": len([source for source in sources if source.get("enabled", True)]),
        "state": state,
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
        "subscription_email": source.get("subscription_email", ""),
        "signup_attempted_at": source.get("signup_attempted_at", ""),
        "confirmation_checked_at": source.get("confirmation_checked_at", ""),
        "subscription_result": source.get("subscription_result", {}),
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


def set_source_status(
    profile_id: str,
    sender: str,
    status: str,
    root: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sender_key = sender.strip().lower()
    allowed_statuses = {*PENDING_SOURCE_STATUSES, "receiving"}
    if status not in allowed_statuses:
        raise ValueError(f"Unknown source status: {status}")

    sources = read_sources(profile_id, root=root)
    now = datetime.now().isoformat(timespec="seconds")
    for source in sources:
        if sender_key in {value.lower() for value in source.get("senders", [])}:
            source["status"] = status
            source["updated_at"] = now
            if status == "pending_subscription":
                source["signup_attempted_at"] = source.get("signup_attempted_at") or now
            if status == "receiving":
                source["enabled"] = True
            if extra:
                source.update(extra)
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
