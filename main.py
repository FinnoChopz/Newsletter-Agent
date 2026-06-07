import json
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

from app.config import get_cheap_model, get_main_model
from app.ranking import load_yaml_file, max_digest_items, rank_scored_items

load_dotenv()
client = OpenAI()

NEWSLETTER_DIR = Path("newsletters")
OUTPUT_DIR = Path("outputs")

EXTRACT_PROMPT = Path("prompts/extract_items.md").read_text(encoding="utf-8")
SCORE_PROMPT = Path("prompts/score_items.md").read_text(encoding="utf-8")
PREFERENCES = Path("data/preferences.yaml").read_text(encoding="utf-8")
LEARNED_PREFERENCES = Path("data/learned_preferences.yaml").read_text(encoding="utf-8")


def parse_json(text: str) -> dict:

    try:

        return json.loads(text)

    except json.JSONDecodeError as e:

        print("Failed to parse JSON:")

        print(text[:1000])

        raise e

def sort_scored_items(scored_text: str) -> str:
    scored_data = parse_json(scored_text)
    ranked_data = rank_scored_items(
        scored_data,
        learned_preferences=load_yaml_file("data/learned_preferences.yaml"),
        max_items=max_digest_items(load_yaml_file("data/preferences.yaml")),
    )

    return json.dumps(ranked_data, indent=2)

def load_newsletters() -> list[dict]:
    return [
        {
            "filename": path.name,
            "text": path.read_text(encoding="utf-8"),
        }
        for path in NEWSLETTER_DIR.glob("*.txt")
    ]


def ask_model(system_prompt: str, user_content: str, model: str) -> str:
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.output_text


def extract_items(newsletter: dict) -> str:
    return ask_model(
        EXTRACT_PROMPT,
        f"""
Filename: {newsletter["filename"]}

Newsletter text:
{newsletter["text"]}
""",
        model=get_cheap_model(),
    )


def score_items(extracted_json: str) -> str:
    return ask_model(
        SCORE_PROMPT,
        f"""


User preferences:
{PREFERENCES}

Learned preferences:
{LEARNED_PREFERENCES}

Extracted items:
{extracted_json}
""",
        model=get_main_model(),
    )

WRITE_DIGEST_PROMPT = Path("prompts/write_digest.md").read_text(encoding="utf-8")


def write_digest(scored_json: str) -> str:
    return ask_model(
        WRITE_DIGEST_PROMPT,
        f"""
Scored items:
{scored_json}
""",
        model=get_main_model(),
    )


def main():
    extracted_dir = OUTPUT_DIR / "extracted"
    scored_dir = OUTPUT_DIR / "scored"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    scored_dir.mkdir(parents=True, exist_ok=True)

    newsletters = load_newsletters()

    for newsletter in newsletters:
        stem = newsletter["filename"].replace(".txt", "")

        print(f"Extracting {newsletter['filename']}...")
        extracted = extract_items(newsletter)

        extracted_path = extracted_dir / f"{stem}.json"
        extracted_path.write_text(extracted, encoding="utf-8")

        print("Scoring items...")

        scored = score_items(extracted)
        scored = sort_scored_items(scored)

        scored_path = scored_dir / f"{stem}.json"

        scored_path.write_text(scored, encoding="utf-8")

        print("Writing digest...")

        digest = write_digest(scored)

        digest_path = OUTPUT_DIR / f"{stem}_digest.md"

        digest_path.write_text(digest, encoding="utf-8")

        print(f"Saved digest to {digest_path}")

if __name__ == "__main__":
    main()
