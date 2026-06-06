import json
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app.config import (
    get_bcc_recipients,
    get_feedback_base_url,
    get_feedback_email,
    get_feedback_secret,
    get_int_env,
    get_main_model,
    get_cheap_model,
    get_recipients,
)
from app.digest import render_html_digest
from app.email_sender import send_email
from app.gmail_reader import fetch_recent_newsletters
from app.ranking import build_digest_manifest, load_yaml_file, max_digest_items, rank_scored_items


load_dotenv()
client = OpenAI()

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

EXTRACT_PROMPT = Path("prompts/extract_items.md").read_text(encoding="utf-8")
SCORE_PROMPT = Path("prompts/score_items.md").read_text(encoding="utf-8")
BASE_PREFERENCES_PATH = Path("data/preferences.yaml")
LEARNED_PREFERENCES_PATH = Path("data/learned_preferences.yaml")
NEWSLETTER_SOURCES_PATH = "data/newsletter_sources.generated.yaml"


def ask_model(system_prompt: str, user_content: str, model: str) -> str:
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.output_text


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("\nModel returned non-JSON:")
        print(text[:1500])
        raise


def extract_items_from_email(email: dict) -> dict:
    print(f"Extracting: {email['subject']}")

    extracted_text = ask_model(
        EXTRACT_PROMPT,
        f"""
Email sender:
{email["sender"]}

Email subject:
{email["subject"]}

Email text:
{email["text"]}
""",
        model=get_cheap_model(),
    )

    extracted = parse_json(extracted_text)

    extracted["email_sender"] = email["sender"]
    extracted["email_subject"] = email["subject"]
    extracted["email_id"] = email["id"]

    return extracted


def merge_extracted_items(extracted_batches: list[dict]) -> dict:
    all_items = []

    for batch in extracted_batches:
        newsletter_name = batch.get("newsletter_name", "Unknown")
        newsletter_date = batch.get("newsletter_date")

        for item in batch.get("items", []):
            item["newsletter_name"] = newsletter_name
            item["newsletter_date"] = newsletter_date
            item["email_sender"] = batch.get("email_sender")
            item["email_subject"] = batch.get("email_subject")
            item["email_id"] = batch.get("email_id")
            all_items.append(item)

    return {"items": all_items}


def score_and_rank_items(
    merged_items: dict,
    base_preferences_text: str,
    base_preferences: dict,
    learned_preferences_text: str,
    learned_preferences: dict,
) -> dict:
    print(f"Scoring {len(merged_items['items'])} item(s)...")

    scored_text = ask_model(
        SCORE_PROMPT,
        f"""
Base Finn preferences:
{base_preferences_text}

Learned preferences from prior feedback:
{learned_preferences_text}

Extracted items:
{json.dumps(merged_items, indent=2)}
""",
        model=get_main_model(),
    )

    scored = parse_json(scored_text)
    return rank_scored_items(
        scored,
        learned_preferences=learned_preferences,
        max_items=max_digest_items(base_preferences),
    )


def write_outputs(
    merged: dict,
    ranked: dict,
    digest_html: str,
    manifest: dict,
) -> dict[str, Path]:
    extracted_path = OUTPUT_DIR / "latest_extracted_items.json"
    scored_path = OUTPUT_DIR / "latest_scored_items.json"
    digest_path = OUTPUT_DIR / "finn_signal_latest.html"
    manifest_path = OUTPUT_DIR / "latest_digest_manifest.json"

    extracted_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    scored_path.write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    digest_path.write_text(digest_html, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "extracted": extracted_path,
        "scored": scored_path,
        "digest": digest_path,
        "manifest": manifest_path,
    }


def main():
    digest_id = date.today().isoformat()
    created_at = datetime.now().isoformat(timespec="seconds")
    days = get_int_env("FINN_SIGNAL_DAYS", 2)
    max_emails = get_int_env("FINN_SIGNAL_MAX_EMAILS", 30)

    base_preferences_text = BASE_PREFERENCES_PATH.read_text(encoding="utf-8")
    learned_preferences_text = LEARNED_PREFERENCES_PATH.read_text(encoding="utf-8")
    base_preferences = load_yaml_file(str(BASE_PREFERENCES_PATH))
    learned_preferences = load_yaml_file(str(LEARNED_PREFERENCES_PATH))

    print("Fetching approved newsletter emails...")

    emails = fetch_recent_newsletters(
        max_results=max_emails,
        days=days,
        sources_path=NEWSLETTER_SOURCES_PATH,
    )

    print(f"Fetched {len(emails)} approved newsletter email(s).")

    if not emails:
        print("No approved newsletters found. Try increasing FINN_SIGNAL_DAYS.")
        return

    extracted_batches = []

    for email in emails:
        try:
            extracted_batches.append(extract_items_from_email(email))
        except Exception as error:
            print(f"Failed to extract {email['subject']}: {error}")

    merged = merge_extracted_items(extracted_batches)
    ranked = score_and_rank_items(
        merged_items=merged,
        base_preferences_text=base_preferences_text,
        base_preferences=base_preferences,
        learned_preferences_text=learned_preferences_text,
        learned_preferences=learned_preferences,
    )
    manifest = build_digest_manifest(
        ranked,
        digest_id=digest_id,
        created_at=created_at,
    )

    print("Writing digest...")
    digest_html = render_html_digest(
        ranked_data=ranked,
        digest_id=digest_id,
        feedback_email=get_feedback_email(),
        feedback_base_url=get_feedback_base_url(),
        feedback_secret=get_feedback_secret(),
    )

    paths = write_outputs(
        merged=merged,
        ranked=ranked,
        digest_html=digest_html,
        manifest=manifest,
    )

    send_email(
        to=", ".join(get_recipients()),
        bcc=", ".join(get_bcc_recipients()),
        subject=f"Finn-Signal - {digest_id}",
        body=digest_html,
        html=True,
    )
    print("Sent digest email.")

    print("\nDone.")
    print(f"Extracted items: {paths['extracted']}")
    print(f"Scored items: {paths['scored']}")
    print(f"Digest: {paths['digest']}")
    print(f"Manifest: {paths['manifest']}")


if __name__ == "__main__":
    main()
