import argparse
from pathlib import Path

from dotenv import load_dotenv

from app.feedback import (
    apply_parsed_feedback,
    clean_reply_text,
    load_json,
    parse_feedback,
    save_json,
)
from app.manifests import extract_digest_id, load_manifest


load_dotenv()

PROCESSED_IDS_PATH = "data/processed_feedback_ids.json"


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


def process_text(raw_feedback: str, use_model: bool, digest_id: str | None = None) -> None:
    cleaned_feedback = clean_reply_text(raw_feedback)
    manifest = load_manifest(digest_id or extract_digest_id(raw_feedback))
    parsed = parse_feedback(cleaned_feedback, use_model=use_model)
    result = apply_parsed_feedback(
        raw_feedback=cleaned_feedback,
        parsed_feedback=parsed,
        manifest=manifest,
    )
    summarize_result("Manual feedback", result)


def process_gmail_feedback(max_results: int, days: int, use_model: bool) -> None:
    from app.gmail_reader import fetch_recent_emails

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
        cleaned_feedback = clean_reply_text(raw_feedback)

        try:
            digest_id = extract_digest_id(
                f"{email.get('subject', '')}\n{raw_feedback}"
            )
            manifest = load_manifest(digest_id)
            parsed = parse_feedback(cleaned_feedback, use_model=use_model)
            result = apply_parsed_feedback(
                raw_feedback=cleaned_feedback,
                parsed_feedback=parsed,
                manifest=manifest,
                message_id=message_id,
            )
            processed_ids.add(message_id)
            summarize_result(f"Feedback email {message_id}", result)
        except Exception as error:
            print(f"Failed to process feedback email {message_id}: {error}")
            print(raw_feedback[:1500])

    save_json(Path(PROCESSED_IDS_PATH), sorted(processed_ids))
    print(f"Processed {new_count} new feedback email(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Finn-Signal feedback.")
    parser.add_argument("--text", help="Process raw feedback text directly.")
    parser.add_argument("--digest-id", help="Use a specific digest manifest.")
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
        process_text(args.text, use_model=use_model, digest_id=args.digest_id)
        return

    process_gmail_feedback(
        max_results=args.max_results,
        days=args.days,
        use_model=use_model,
    )


if __name__ == "__main__":
    main()
