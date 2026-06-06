import argparse
import os
import subprocess
from pathlib import Path


PLIST_PATH = Path.home() / "Library/LaunchAgents/com.finn.finnsignal.plist"
LABEL = "com.finn.finnsignal"


def parse_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use HH:MM, for example 16:40.") from exc

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("Hour must be 0-23 and minute must be 0-59.")

    return hour, minute


def run(command: list[str], check: bool = True) -> None:
    print(" ".join(command))
    subprocess.run(command, check=check)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set Finn-Signal launchd run time.")
    parser.add_argument("time", type=parse_time, help="24-hour time, for example 16:40.")
    args = parser.parse_args()
    hour, minute = args.time

    if not PLIST_PATH.exists():
        raise FileNotFoundError(f"LaunchAgent plist not found: {PLIST_PATH}")

    run([
        "plutil",
        "-replace",
        "StartCalendarInterval.Hour",
        "-integer",
        str(hour),
        str(PLIST_PATH),
    ])
    run([
        "plutil",
        "-replace",
        "StartCalendarInterval.Minute",
        "-integer",
        str(minute),
        str(PLIST_PATH),
    ])

    uid = os.getuid()
    service = f"gui/{uid}/{LABEL}"
    domain = f"gui/{uid}"

    run(["launchctl", "bootout", domain, str(PLIST_PATH)], check=False)
    run(["launchctl", "bootstrap", domain, str(PLIST_PATH)])
    run(["launchctl", "enable", service], check=False)
    run(["launchctl", "print", service], check=False)

    print(f"Finn-Signal is scheduled for {hour:02d}:{minute:02d}.")


if __name__ == "__main__":
    main()
