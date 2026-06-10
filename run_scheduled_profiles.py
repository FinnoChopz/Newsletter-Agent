from __future__ import annotations

from dotenv import load_dotenv

from app.profiles import list_profiles
from app.scheduler import (
    is_profile_due,
    mark_scheduler_checked,
    mark_send_failed,
    mark_send_started,
    mark_sent,
    mark_stale_send_if_needed,
    profile_due_decision,
    scheduler_now,
)
from app.signal_runner import run_signal_for_profile


load_dotenv()


def main() -> dict:
    now = scheduler_now()
    profiles = list_profiles()
    summary = {
        "checked_at": now.isoformat(timespec="seconds"),
        "timezone": now.tzname(),
        "profile_count": len(profiles),
        "due_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "profiles": [],
    }

    if not profiles:
        print(f"No profiles configured at {now.isoformat(timespec='seconds')}.")
        return summary

    due_profiles = []
    for profile in profiles:
        profile_id = profile["id"]
        state = profile.get("state") or {}
        stale_state = mark_stale_send_if_needed(profile_id, state, now=now)
        if stale_state is not None:
            state = stale_state
            profile["state"] = stale_state
        decision = profile_due_decision(profile, state=state, now=now)
        mark_scheduler_checked(profile_id, now=now, decision=decision)
        profile_summary = {
            "profile_id": profile_id,
            "email": profile.get("email", ""),
            "decision": decision.get("reason", ""),
            "due": bool(decision.get("due")),
        }
        summary["profiles"].append(profile_summary)
        if is_profile_due(profile, state=profile.get("state") or {}, now=now):
            due_profiles.append(profile)
            summary["due_count"] += 1
        else:
            summary["skipped_count"] += 1

    if not due_profiles:
        print(f"No profiles due at {now.isoformat(timespec='seconds')}.")
        return summary

    for profile in due_profiles:
        profile_id = profile["id"]
        print(f"Running Finn-Signal for {profile_id}...")
        mark_send_started(profile_id, now=now)
        profile_summary = next(
            item for item in summary["profiles"] if item["profile_id"] == profile_id
        )
        try:
            result = run_signal_for_profile(profile_id)
            print(result)
            if result.get("status") in {"sent", "sent_no_newsletters"}:
                mark_sent(profile_id, now=now, result=result)
                profile_summary["status"] = result.get("status")
                profile_summary["message_id"] = (result.get("send_result") or {}).get("id", "")
                summary["sent_count"] += 1
            else:
                mark_send_failed(profile_id, str(result), now=now)
                profile_summary["status"] = "failed"
                profile_summary["error"] = str(result)
                summary["failed_count"] += 1
        except Exception as error:
            print(f"Failed profile {profile_id}: {error}")
            mark_send_failed(profile_id, str(error), now=now)
            profile_summary["status"] = "failed"
            profile_summary["error"] = str(error)
            summary["failed_count"] += 1

    return summary


if __name__ == "__main__":
    main()
