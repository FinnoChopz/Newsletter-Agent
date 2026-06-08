from __future__ import annotations

import plistlib
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.profiles import list_profiles, profile_paths, read_json, resolve_profile_id, write_json


LAUNCH_AGENT_LABEL = "com.finn.finnsignal.profiles"


def local_date_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def days_between(start_date: str, now: datetime) -> int:
    if not start_date:
        return 999999
    try:
        previous = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return 999999
    return (now.date() - previous).days


def minutes_since_midnight(value: datetime) -> int:
    return value.hour * 60 + value.minute


def scheduled_minutes(schedule_time: str) -> int:
    try:
        hour_text, minute_text = schedule_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError):
        hour = 11
        minute = 0

    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    return hour * 60 + minute


def scheduled_time_has_arrived(schedule: dict[str, Any], now: datetime) -> bool:
    return minutes_since_midnight(now) >= scheduled_minutes(schedule.get("time", "11:00"))


def is_profile_due(
    profile: dict[str, Any],
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now()
    state = state or {}
    schedule = profile.get("schedule") or {}

    if not schedule.get("enabled", True):
        return False

    if not scheduled_time_has_arrived(schedule, now):
        return False

    frequency = schedule.get("frequency", "daily")
    last_sent_on = str(state.get("last_sent_on") or "")
    today = local_date_key(now)

    if last_sent_on == today:
        return False

    if frequency == "daily":
        return True

    if frequency == "weekdays":
        return now.weekday() < 5

    if frequency == "every_other_day":
        return days_between(last_sent_on, now) >= 2

    if frequency == "weekly":
        return days_between(last_sent_on, now) >= 7

    return False


def mark_sent(profile_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_sent_on"] = local_date_key(now)
    state["last_sent_at"] = now.isoformat(timespec="seconds")
    state.pop("last_error", None)
    state.pop("last_failed_at", None)
    write_json(paths.state, state)
    return state


def mark_scheduler_checked(profile_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_scheduler_check_at"] = now.isoformat(timespec="seconds")
    write_json(paths.state, state)
    return state


def mark_send_started(profile_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_send_started_at"] = now.isoformat(timespec="seconds")
    write_json(paths.state, state)
    return state


def mark_send_failed(
    profile_id: str,
    error: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_failed_at"] = now.isoformat(timespec="seconds")
    state["last_error"] = error
    write_json(paths.state, state)
    return state


def due_profiles(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now()
    due = []
    for profile in list_profiles():
        state = read_json(profile_paths(profile["id"]).state, {})
        if is_profile_due(profile, state=state, now=now):
            due.append(profile)
    return due


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_agent_plist(project_root: Path) -> dict[str, Any]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(project_root / ".venv" / "bin" / "python"),
            str(project_root / "run_scheduled_profiles.py"),
        ],
        "WorkingDirectory": str(project_root),
        "StartInterval": 60,
        "RunAtLoad": True,
        "StandardOutPath": str(project_root / "logs" / "finn_signal_profiles.out.log"),
        "StandardErrorPath": str(project_root / "logs" / "finn_signal_profiles.err.log"),
    }


def install_launch_agent(project_root: str | Path = ".") -> dict[str, str]:
    project_root = Path(project_root).resolve()
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("wb") as file:
        plistlib.dump(launch_agent_plist(project_root), file)

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True)
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{LAUNCH_AGENT_LABEL}"], check=True)

    return {
        "label": LAUNCH_AGENT_LABEL,
        "path": str(path),
        "status": "installed",
    }
