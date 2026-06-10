from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from app.config import get_feedback_base_url, get_int_env, get_main_model, get_cheap_model
from app.digest import render_html_digest
from app.email_sender import send_email
from app.gmail_reader import fetch_recent_newsletters
from app.item_metadata import preserve_scored_item_metadata
from app.manifests import save_manifest
from app.profiles import load_profile, profile_paths, read_sources
from app.ranking import build_digest_manifest, load_yaml_file, max_digest_items, rank_scored_items
from app.scheduler import scheduler_now


load_dotenv()
client = OpenAI()

EXTRACT_PROMPT = Path("prompts/extract_items.md").read_text(encoding="utf-8")
SCORE_PROMPT = Path("prompts/score_items.md").read_text(encoding="utf-8")


def ask_model(system_prompt: str, user_content: str, model: str) -> str:
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.output_text


def parse_json(text: str) -> dict[str, Any]:
    return json.loads(text)


def extract_items_from_email(email: dict[str, Any]) -> dict[str, Any]:
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


def merge_extracted_items(extracted_batches: list[dict[str, Any]]) -> dict[str, Any]:
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
    merged_items: dict[str, Any],
    base_preferences_text: str,
    base_preferences: dict[str, Any],
    learned_preferences_text: str,
    learned_preferences: dict[str, Any],
) -> dict[str, Any]:
    scored_text = ask_model(
        SCORE_PROMPT,
        f"""
Base preferences:
{base_preferences_text}

Learned preferences from prior feedback:
{learned_preferences_text}

Extracted items:
{json.dumps(merged_items, indent=2)}
""",
        model=get_main_model(),
    )

    scored = parse_json(scored_text)
    scored = preserve_scored_item_metadata(scored, merged_items)
    return rank_scored_items(
        scored,
        learned_preferences=learned_preferences,
        max_items=max_digest_items(base_preferences),
    )


def profile_output_dir(profile_id: str) -> Path:
    path = Path("outputs/users") / profile_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_profile_outputs(
    profile_id: str,
    merged: dict[str, Any],
    ranked: dict[str, Any],
    digest_html: str,
    manifest: dict[str, Any],
) -> dict[str, str]:
    output_dir = profile_output_dir(profile_id)
    paths = {
        "extracted": output_dir / "latest_extracted_items.json",
        "scored": output_dir / "latest_scored_items.json",
        "digest": output_dir / "finn_signal_latest.html",
        "manifest": output_dir / "latest_digest_manifest.json",
    }
    paths["extracted"].write_text(json.dumps(merged, indent=2), encoding="utf-8")
    paths["scored"].write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    paths["digest"].write_text(digest_html, encoding="utf-8")
    paths["manifest"].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    save_manifest(manifest)
    return {key: str(value) for key, value in paths.items()}


def write_empty_profile_outputs(
    profile_id: str,
    digest_html: str,
    manifest: dict[str, Any],
    message: str,
) -> dict[str, str]:
    output_dir = profile_output_dir(profile_id)
    ranked = {
        "status": "no_newsletters",
        "message": message,
        "scored_items": [],
        "digest_sections": {
            "top_signals": [],
            "strange_attractor": None,
            "skipped_but_noted": [],
        },
    }
    paths = {
        "scored": output_dir / "latest_scored_items.json",
        "digest": output_dir / "finn_signal_latest.html",
        "manifest": output_dir / "latest_digest_manifest.json",
    }
    paths["scored"].write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    paths["digest"].write_text(digest_html, encoding="utf-8")
    paths["manifest"].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    save_manifest(manifest)
    return {key: str(value) for key, value in paths.items()}


def render_empty_digest(profile: dict[str, Any], days: int, source_count: int) -> str:
    user_name = escape(str(profile.get("display_name") or "there"))
    source_label = "source" if source_count == 1 else "sources"
    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#f6f2ea;font-family:Arial,sans-serif;color:#1c1b18;">
    <main style="max-width:640px;margin:0 auto;padding:32px 20px;">
      <p style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#7b7468;">Finn-Signal</p>
      <h1 style="font-size:28px;line-height:1.15;margin:0 0 14px;">No new signal today</h1>
      <p style="font-size:16px;line-height:1.55;margin:0 0 16px;">Hi {user_name}, Finn-Signal checked your approved newsletters and did not find any matching emails from the last {days} day(s).</p>
      <p style="font-size:15px;line-height:1.55;margin:0;">It searched {source_count} approved {source_label}. If this looks wrong, open Finn-Signal and rescan or add more newsletter sources.</p>
    </main>
  </body>
</html>"""


def run_signal_for_profile(profile_id: str) -> dict[str, Any]:
    profile = load_profile(profile_id)
    profile_id = profile["id"]
    paths = profile_paths(profile_id)

    if not paths.token.exists():
        raise RuntimeError("Connect Gmail before sending a digest.")

    now = scheduler_now()
    today = now.date().isoformat()
    digest_id = f"{profile_id}-{today}"
    created_at = now.isoformat(timespec="seconds")
    days = get_int_env("FINN_SIGNAL_DAYS", 2)
    max_emails = get_int_env("FINN_SIGNAL_MAX_EMAILS", 30)
    source_count = len([source for source in read_sources(profile_id) if source.get("enabled", True)])

    emails = fetch_recent_newsletters(
        max_results=max_emails,
        days=days,
        sources_path=str(paths.sources),
        token_path=paths.token,
    )

    if not emails:
        digest_html = render_empty_digest(profile, days=days, source_count=source_count)
        manifest = {
            "digest_id": digest_id,
            "created_at": created_at,
            "digest_version": "v3",
            "user_name": str(profile.get("display_name") or "you"),
            "items": [],
            "status": "no_newsletters",
        }
        message = "No approved newsletter emails were found in the current lookback window."
        output_paths = write_empty_profile_outputs(
            profile_id=profile_id,
            digest_html=digest_html,
            manifest=manifest,
            message=message,
        )
        send_result = send_email(
            to=profile["email"],
            subject=f"Finn-Signal - {today}",
            body=digest_html,
            html=True,
            token_path=str(paths.token),
        )
        return {
            "profile_id": profile_id,
            "status": "sent_no_newsletters",
            "message": message,
            "digest_id": digest_id,
            "send_result": send_result,
            "outputs": output_paths,
        }

    extracted_batches = []
    failures = []
    for email in emails:
        try:
            extracted_batches.append(extract_items_from_email(email))
        except Exception as error:
            failures.append({"subject": email.get("subject", ""), "error": str(error)})

    merged = merge_extracted_items(extracted_batches)
    base_preferences_text = paths.preferences.read_text(encoding="utf-8")
    learned_preferences_text = paths.learned_preferences.read_text(encoding="utf-8")
    base_preferences = load_yaml_file(str(paths.preferences))
    learned_preferences = load_yaml_file(str(paths.learned_preferences))

    ranked = score_and_rank_items(
        merged_items=merged,
        base_preferences_text=base_preferences_text,
        base_preferences=base_preferences,
        learned_preferences_text=learned_preferences_text,
        learned_preferences=learned_preferences,
    )
    user_name = str(profile.get("display_name") or "you")
    manifest = build_digest_manifest(
        ranked,
        digest_id=digest_id,
        created_at=created_at,
        user_name=user_name,
    )
    digest_html = render_html_digest(
        ranked_data=ranked,
        digest_id=digest_id,
        feedback_email=profile["email"],
        feedback_base_url=get_feedback_base_url(),
        user_name=user_name,
    )
    output_paths = write_profile_outputs(
        profile_id=profile_id,
        merged=merged,
        ranked=ranked,
        digest_html=digest_html,
        manifest=manifest,
    )

    send_result = send_email(
        to=profile["email"],
        subject=f"Finn-Signal - {today}",
        body=digest_html,
        html=True,
        token_path=str(paths.token),
    )

    return {
        "profile_id": profile_id,
        "status": "sent",
        "email_count": len(emails),
        "item_count": len(merged["items"]),
        "failures": failures,
        "digest_id": digest_id,
        "send_result": send_result,
        "outputs": output_paths,
    }
