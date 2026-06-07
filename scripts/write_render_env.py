import json
from pathlib import Path


def main() -> None:
    credentials_path = Path("credentials.json")
    if not credentials_path.exists():
        raise FileNotFoundError("credentials.json not found in the project root.")

    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    output = {
        "FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON": json.dumps(credentials, separators=(",", ":")),
    }

    output_path = Path(".render.env")
    output_path.write_text(
        "\n".join(f"{key}={value}" for key, value in output.items()) + "\n",
        encoding="utf-8",
    )

    print("Wrote .render.env")
    print("Copy this value into Render environment variables.")
    print("Do not commit .render.env.")


if __name__ == "__main__":
    main()
