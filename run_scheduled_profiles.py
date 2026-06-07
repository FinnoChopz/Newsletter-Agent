from __future__ import annotations

from datetime import datetime

from dotenv import load_dotenv

from app.scheduler import due_profiles, mark_sent
from app.signal_runner import run_signal_for_profile


load_dotenv()


def main() -> None:
    now = datetime.now()
    profiles = due_profiles(now=now)

    if not profiles:
        print(f"No profiles due at {now.isoformat(timespec='seconds')}.")
        return

    for profile in profiles:
        profile_id = profile["id"]
        print(f"Running Finn-Signal for {profile_id}...")
        try:
            result = run_signal_for_profile(profile_id)
            print(result)
            if result.get("status") in {"sent", "no_newsletters"}:
                mark_sent(profile_id, now=now)
        except Exception as error:
            print(f"Failed profile {profile_id}: {error}")


if __name__ == "__main__":
    main()
