from __future__ import annotations

from datetime import datetime

from dotenv import load_dotenv

from app.profiles import list_profiles
from app.scheduler import (
    is_profile_due,
    mark_scheduler_checked,
    mark_send_failed,
    mark_send_started,
    mark_sent,
)
from app.signal_runner import run_signal_for_profile


load_dotenv()


def main() -> None:
    now = datetime.now()
    profiles = list_profiles()

    if not profiles:
        print(f"No profiles configured at {now.isoformat(timespec='seconds')}.")
        return

    due_profiles = []
    for profile in profiles:
        state = mark_scheduler_checked(profile["id"], now=now)
        if is_profile_due(profile, state=state, now=now):
            due_profiles.append(profile)

    if not due_profiles:
        print(f"No profiles due at {now.isoformat(timespec='seconds')}.")
        return

    for profile in due_profiles:
        profile_id = profile["id"]
        print(f"Running Finn-Signal for {profile_id}...")
        mark_send_started(profile_id, now=now)
        try:
            result = run_signal_for_profile(profile_id)
            print(result)
            if result.get("status") in {"sent", "sent_no_newsletters"}:
                mark_sent(profile_id, now=now)
            else:
                mark_send_failed(profile_id, str(result), now=now)
        except Exception as error:
            print(f"Failed profile {profile_id}: {error}")
            mark_send_failed(profile_id, str(error), now=now)


if __name__ == "__main__":
    main()
