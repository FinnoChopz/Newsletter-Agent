import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from app.feedback import (
    apply_parsed_feedback,
    load_json,
    parse_feedback,
    save_json,
)


load_dotenv()

MANIFEST_PATH = Path("outputs/latest_digest_manifest.json")
PROCESSED_IDS_PATH = Path("data/processed_feedback_ids.json")


def load_manifest(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Digest manifest not found at {path}. Run python run_daily_signal.py first."
        )

    return json.loads(path.read_text(encoding="utf-8"))


def summarize_result(label: str, result: dict) -> None:
    updates = [update for update in result["applied_updates"] if update.get("applied")]
    warnings = result["warnings"]

    print(f"{label}: applied {len(updates)} update(s).")

    for update in updates[:12]:
        print(
            f"- {update['type']}: {update['key']} "
            f"{update['old']} -> {update['new']}"
        )

    for warning in warnings:
        print(f"Warning: {warning}")


def process_text(raw_feedback: str, use_model: bool) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    parsed = parse_feedback(raw_feedback, use_model=use_model)
    result = apply_parsed_feedback(
        raw_feedback=raw_feedback,
        parsed_feedback=parsed,
        manifest=manifest,
    )
    summarize_result("Manual feedback", result)


def process_gmail_feedback(max_results: int, days: int, use_model: bool) -> None:
    from app.gmail_reader import fetch_recent_emails

    manifest = load_manifest(MANIFEST_PATH)
    processed_ids = set(load_json(PROCESSED_IDS_PATH, []))
    query = f'subject:"Re: Finn-Signal" newer_than:{days}d -in:spam -in:trash'

    emails = fetch_recent_emails(max_results=max_results, query=query)
    new_count = 0

    for email in emails:
        message_id = email["id"]
        if message_id in processed_ids:
            continue

        new_count += 1
        raw_feedback = email.get("text", "")

        try:
            parsed = parse_feedback(raw_feedback, use_model=use_model)
            result = apply_parsed_feedback(
                raw_feedback=raw_feedback,
                parsed_feedback=parsed,
                manifest=manifest,
                message_id=message_id,
            )
            processed_ids.add(message_id)
            summarize_result(f"Feedback email {message_id}", result)
        except Exception as error:
            print(f"Failed to process feedback email {message_id}: {error}")
            print(raw_feedback[:1500])

    save_json(PROCESSED_IDS_PATH, sorted(processed_ids))
    print(f"Processed {new_count} new feedback email(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Finn-Signal feedback.")
    parser.add_argument("--text", help="Process raw feedback text directly.")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Use only deterministic local parsing.",
    )
    args = parser.parse_args()

    use_model = not args.no_model

    if args.text:
        process_text(args.text, use_model=use_model)
        return

    process_gmail_feedback(
        max_results=args.max_results,
        days=args.days,
        use_model=use_model,
    )


if __name__ == "__main__":
    main()
