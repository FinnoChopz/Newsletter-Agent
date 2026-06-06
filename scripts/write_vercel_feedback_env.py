import json
from pathlib import Path

from dotenv import dotenv_values


OUTPUT_PATH = Path(".vercel-feedback.env")


def load_google_client() -> dict:
    data = json.loads(Path("credentials.json").read_text(encoding="utf-8"))
    if "installed" in data:
        return data["installed"]
    if "web" in data:
        return data["web"]
    raise KeyError("credentials.json must contain an installed or web OAuth client.")


def main() -> None:
    client = load_google_client()
    token = json.loads(Path("token.json").read_text(encoding="utf-8"))
    env = dotenv_values(".env")

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "token.json does not contain refresh_token. Re-run Google OAuth onboarding."
        )

    feedback_to = (
        env.get("FINN_SIGNAL_FEEDBACK_TO")
        or env.get("FINN_SIGNAL_FEEDBACK_EMAIL")
        or env.get("FINN_SIGNAL_RECIPIENTS")
    )

    if not feedback_to:
        raise RuntimeError("Set FINN_SIGNAL_FEEDBACK_EMAIL or FINN_SIGNAL_RECIPIENTS in .env.")

    values = {
        "FINN_SIGNAL_FEEDBACK_TO": feedback_to,
        "FINN_SIGNAL_GMAIL_CLIENT_ID": client["client_id"],
        "FINN_SIGNAL_GMAIL_CLIENT_SECRET": client["client_secret"],
        "FINN_SIGNAL_GMAIL_REFRESH_TOKEN": refresh_token,
    }

    OUTPUT_PATH.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {OUTPUT_PATH}")
    print("Add each value in that file to Vercel Project Settings -> Environment Variables.")
    print("Do not commit or share this file.")


if __name__ == "__main__":
    main()
