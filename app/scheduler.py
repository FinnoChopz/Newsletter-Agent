from __future__ import annotations

import plistlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.profiles import list_profiles, profile_paths, read_json, resolve_profile_id, users_root, write_json


LAUNCH_AGENT_LABEL = "com.finn.finnsignal.profiles"
DEFAULT_TIMEZONE = "America/New_York"
SCHEDULER_STATE_FILE = "_scheduler_state.json"


def scheduler_timezone_name() -> str:
    return os.getenv("FINN_SIGNAL_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def scheduler_timezone() -> ZoneInfo:
    name = scheduler_timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def scheduler_now() -> datetime:
    return datetime.now(scheduler_timezone())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_since(value: str, now: datetime | None = None) -> float | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    if now is None:
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else scheduler_now()
    if parsed.tzinfo and now.tzinfo is None:
        now = now.replace(tzinfo=parsed.tzinfo)
    elif parsed.tzinfo is None and now.tzinfo is not None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - parsed).total_seconds())


def send_stale_seconds() -> int:
    try:
        return int(os.getenv("FINN_SIGNAL_SEND_STALE_SECONDS", "900"))
    except ValueError:
        return 900


def scheduler_state_path(root: str | Path | None = None) -> Path:
    return users_root(root) / SCHEDULER_STATE_FILE


def read_scheduler_state(root: str | Path | None = None) -> dict[str, Any]:
    return read_json(scheduler_state_path(root), {})


def write_scheduler_state(data: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    write_json(scheduler_state_path(root), data)
    return data


def update_scheduler_state(fields: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    state = read_scheduler_state(root)
    state.update(fields)
    state.setdefault("timezone", scheduler_timezone_name())
    return write_scheduler_state(state, root)


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


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def profile_due_decision(
    profile: dict[str, Any],
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or scheduler_now()
    state = state or {}
    schedule = profile.get("schedule") or {}
    scheduled_for = schedule.get("time", "11:00")
    last_sent_on = str(state.get("last_sent_on") or "")
    today = local_date_key(now)

    if not schedule.get("enabled", True):
        reason = "schedule_disabled"
    elif profile.get("gmail_connected") is False:
        reason = "gmail_not_connected"
    elif safe_int(profile.get("source_count", 1), default=1) <= 0:
        reason = "no_receiving_sources"
    elif not scheduled_time_has_arrived(schedule, now):
        reason = "waiting_for_time"
    elif last_sent_on == today:
        reason = "already_sent_today"
    else:
        frequency = schedule.get("frequency", "daily")
        if frequency == "daily":
            reason = "due"
        elif frequency == "weekdays":
            reason = "due" if now.weekday() < 5 else "weekend"
        elif frequency == "every_other_day":
            reason = "due" if days_between(last_sent_on, now) >= 2 else "waiting_every_other_day"
        elif frequency == "weekly":
            reason = "due" if days_between(last_sent_on, now) >= 7 else "waiting_weekly"
        else:
            reason = "unsupported_frequency"

    return {
        "due": reason == "due",
        "reason": reason,
        "checked_at": now.isoformat(timespec="seconds"),
        "scheduled_for": scheduled_for,
        "today": today,
        "last_sent_on": last_sent_on,
        "timezone": scheduler_timezone_name(),
    }


def is_profile_due(
    profile: dict[str, Any],
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    return profile_due_decision(profile, state=state, now=now).get("due") is True


def mark_sent(
    profile_id: str,
    now: datetime | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or scheduler_now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_sent_on"] = local_date_key(now)
    state["last_sent_at"] = now.isoformat(timespec="seconds")
    if result is not None:
        state["last_run_status"] = result.get("status", "sent")
        state["last_run_result"] = result
        send_result = result.get("send_result") or {}
        if send_result.get("id"):
            state["last_send_message_id"] = send_result["id"]
    state.pop("last_error", None)
    state.pop("last_failed_at", None)
    write_json(paths.state, state)
    return state


def mark_scheduler_checked(
    profile_id: str,
    now: datetime | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or scheduler_now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_scheduler_check_at"] = now.isoformat(timespec="seconds")
    if decision is not None:
        state["last_scheduler_decision"] = decision.get("reason", "")
        state["last_scheduler_due"] = bool(decision.get("due"))
        state["last_scheduler_timezone"] = decision.get("timezone", scheduler_timezone_name())
    write_json(paths.state, state)
    return state


def mark_send_started(profile_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or scheduler_now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_send_started_at"] = now.isoformat(timespec="seconds")
    state["last_run_status"] = "running"
    write_json(paths.state, state)
    return state


def mark_send_failed(
    profile_id: str,
    error: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or scheduler_now()
    profile_id = resolve_profile_id(profile_id)
    paths = profile_paths(profile_id)
    state = read_json(paths.state, {})
    state["last_failed_at"] = now.isoformat(timespec="seconds")
    state["last_error"] = error
    state["last_run_status"] = "failed"
    write_json(paths.state, state)
    return state


def mark_stale_send_if_needed(
    profile_id: str,
    state: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if state.get("last_run_status") != "running":
        return None

    started_at = str(state.get("last_send_started_at") or "")
    age = seconds_since(started_at, now=now)
    if age is None or age < send_stale_seconds():
        return None

    return mark_send_failed(
        profile_id,
        f"Previous send was still marked running after {int(age)} seconds.",
        now=now,
    )


def due_profiles(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or scheduler_now()
    due = []
    for profile in list_profiles():
        state = read_json(profile_paths(profile["id"]).state, {})
        if is_profile_due(profile, state=state, now=now):
            due.append(profile)
    return due


def mark_hosted_scheduler_started(interval_seconds: int, root: str | Path | None = None) -> dict[str, Any]:
    now = scheduler_now()
    now_utc = utc_now()
    return update_scheduler_state(
        {
            "enabled": True,
            "status": "starting",
            "interval_seconds": interval_seconds,
            "timezone": scheduler_timezone_name(),
            "started_at": now.isoformat(timespec="seconds"),
            "started_at_utc": now_utc.isoformat(timespec="seconds"),
            "last_heartbeat_at": now.isoformat(timespec="seconds"),
            "last_heartbeat_at_utc": now_utc.isoformat(timespec="seconds"),
            "last_error": "",
        },
        root=root,
    )


def mark_hosted_scheduler_loop_started(root: str | Path | None = None) -> dict[str, Any]:
    now = scheduler_now()
    return update_scheduler_state(
        {
            "status": "running",
            "active": True,
            "active_run_started_at": now.isoformat(timespec="seconds"),
            "active_run_started_at_utc": utc_now().isoformat(timespec="seconds"),
            "last_heartbeat_at": now.isoformat(timespec="seconds"),
            "last_heartbeat_at_utc": utc_now().isoformat(timespec="seconds"),
        },
        root=root,
    )


def mark_hosted_scheduler_loop_finished(
    summary: dict[str, Any],
    root: str | Path | None = None,
) -> dict[str, Any]:
    now = scheduler_now()
    return update_scheduler_state(
        {
            "status": "idle",
            "active": False,
            "last_finished_at": now.isoformat(timespec="seconds"),
            "last_finished_at_utc": utc_now().isoformat(timespec="seconds"),
            "last_summary": summary,
            "last_error": "",
            "consecutive_errors": 0,
        },
        root=root,
    )


def mark_hosted_scheduler_loop_failed(
    error: str,
    root: str | Path | None = None,
) -> dict[str, Any]:
    previous = read_scheduler_state(root)
    now = scheduler_now()
    return update_scheduler_state(
        {
            "status": "error",
            "active": False,
            "last_failed_at": now.isoformat(timespec="seconds"),
            "last_failed_at_utc": utc_now().isoformat(timespec="seconds"),
            "last_error": error,
            "consecutive_errors": int(previous.get("consecutive_errors") or 0) + 1,
        },
        root=root,
    )


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def scheduler_python(project_root: Path) -> Path:
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def launch_agent_plist(project_root: Path) -> dict[str, Any]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(scheduler_python(project_root)),
            str(project_root / "run_scheduled_profiles.py"),
        ],
        "WorkingDirectory": str(project_root),
        "StartInterval": 60,
        "RunAtLoad": True,
        "StandardOutPath": str(project_root / "logs" / "finn_signal_profiles.out.log"),
        "StandardErrorPath": str(project_root / "logs" / "finn_signal_profiles.err.log"),
    }


def launch_agent_status(project_root: str | Path = ".") -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    path = launch_agent_path()
    service = f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"
    result: dict[str, Any] = {
        "label": LAUNCH_AGENT_LABEL,
        "path": str(path),
        "service": service,
        "project_root": str(project_root),
        "python": str(scheduler_python(project_root)),
        "installed": path.exists(),
        "loaded": False,
        "status": "not_installed",
    }

    try:
        completed = subprocess.run(
            ["launchctl", "print", service],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        result["status"] = "unknown"
        result["error"] = str(exc)
        return result

    result["loaded"] = completed.returncode == 0
    if completed.returncode == 0:
        result["status"] = "loaded"
    elif path.exists():
        result["status"] = "installed_not_loaded"
    result["launchctl_returncode"] = completed.returncode
    if completed.stderr.strip():
        result["launchctl_error"] = completed.stderr.strip()
    return result


def install_launch_agent(project_root: str | Path = ".") -> dict[str, Any]:
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
        **launch_agent_status(project_root),
        "label": LAUNCH_AGENT_LABEL,
        "path": str(path),
        "status": "installed",
    }
