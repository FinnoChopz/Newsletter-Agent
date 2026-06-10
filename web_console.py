from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from openai import OpenAI

from app.config import get_cheap_model
from app.feedback import apply_parsed_feedback
from app.gmail_reader import (
    SCOPES,
    classify_sender_with_model,
    discover_newsletters,
    extract_sender_query_value,
    fetch_recent_emails,
)
from app.manifests import load_manifest
from app.newsletter_discovery import discover_recommendations, recommendation_to_source
from app.profiles import (
    create_profile,
    list_profiles,
    load_profile,
    profile_paths,
    read_json,
    read_yaml,
    read_sources,
    resolve_profile_id,
    set_source_enabled,
    set_source_status,
    storage_status,
    update_schedule,
    upsert_source,
    write_json,
)
from app.ranking import item_source
from app.scheduler import (
    install_launch_agent,
    launch_agent_status,
    mark_hosted_scheduler_loop_failed,
    mark_hosted_scheduler_loop_finished,
    mark_hosted_scheduler_loop_started,
    mark_hosted_scheduler_started,
    mark_send_failed,
    mark_send_started,
    mark_sent,
    mark_stale_send_if_needed,
    read_scheduler_state,
    scheduler_now,
    scheduler_timezone_name,
)
from run_scheduled_profiles import main as run_scheduled_profiles
from app.signal_runner import run_signal_for_profile
from app.subscriptions import attempt_newsletter_subscription


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PROJECT_ROOT / "web"
oauth_sessions: dict[str, dict[str, Any]] = {}
profile_send_threads: dict[str, threading.Thread] = {}
profile_send_lock = threading.Lock()
openai_client = OpenAI()

SITE_GUIDE_TARGETS = {
    "profile_select": "#profileSelect",
    "onboarding_tab": '[data-tab="onboarding"]',
    "rankings_tab": '[data-tab="rankings"]',
    "sources_tab": '[data-tab="sources"]',
    "discover_tab": '[data-tab="discover"]',
    "schedule_tab": '[data-tab="schedule"]',
    "runs_tab": '[data-tab="runs"]',
    "create_profile": "#profileForm",
    "connect_gmail": "#connectGmail",
    "scan_gmail": "#scanInbox",
    "approve_sources": "#importCandidates",
    "rankings_refresh": "#refreshRankings",
    "rankings_send": "#rankingSendTest",
    "ranking_list": "#rankingList",
    "manual_source": "#manualSourceForm",
    "approved_sources": "#sourceList",
    "discover_agent": "#runDiscovery",
    "schedule_form": "#scheduleForm",
    "send_test": "#sendTest",
}


def clean_profile_id(value: str) -> str:
    return unquote(value).strip()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, indent=2).encode("utf-8")


def console_port() -> int:
    return int(os.getenv("PORT", "8787"))


def console_host() -> str:
    configured = os.getenv("FINN_SIGNAL_CONSOLE_HOST", "").strip()
    if configured:
        return configured
    if os.getenv("PORT"):
        return "0.0.0.0"
    return "127.0.0.1"


def public_base_url(port: int) -> str:
    configured = os.getenv("FINN_SIGNAL_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://localhost:{port}"


def hosted_scheduler_enabled() -> bool:
    return bool_env("FINN_SIGNAL_ENABLE_HOSTED_SCHEDULER", False)


def scheduler_state() -> dict[str, Any]:
    if hosted_scheduler_enabled():
        return {
            **read_scheduler_state(),
            "hosted": True,
            "timezone": scheduler_timezone_name(),
        }

    return {
        **launch_agent_status(PROJECT_ROOT),
        "hosted": False,
        "timezone": scheduler_timezone_name(),
    }


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_since(value: str) -> float | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return max(0.0, (now - parsed).total_seconds())


def hosted_scheduler_health() -> dict[str, Any]:
    state = read_scheduler_state()
    interval_seconds = max(60, int(os.getenv("FINN_SIGNAL_SCHEDULER_INTERVAL_SECONDS", "300")))
    stale_after_seconds = max(
        interval_seconds * 3,
        int(os.getenv("FINN_SIGNAL_SCHEDULER_STALE_SECONDS", "1200")),
    )
    active_run_limit_seconds = int(os.getenv("FINN_SIGNAL_SCHEDULER_MAX_RUN_SECONDS", "3600"))
    heartbeat_age = seconds_since(str(state.get("last_heartbeat_at") or ""))
    active_age = seconds_since(str(state.get("active_run_started_at") or ""))
    problems = []

    if not state:
        problems.append("Hosted scheduler has not written a heartbeat yet.")
    elif state.get("active"):
        if active_age is not None and active_age > active_run_limit_seconds:
            problems.append("Hosted scheduler run has been active too long.")
    elif heartbeat_age is None:
        problems.append("Hosted scheduler heartbeat is missing.")
    elif heartbeat_age > stale_after_seconds:
        problems.append("Hosted scheduler heartbeat is stale.")

    if state.get("status") == "error":
        problems.append(str(state.get("last_error") or "Hosted scheduler reported an error."))

    return {
        "ok": not problems,
        "hosted": True,
        "timezone": scheduler_timezone_name(),
        "interval_seconds": interval_seconds,
        "stale_after_seconds": stale_after_seconds,
        "heartbeat_age_seconds": heartbeat_age,
        "active_run_age_seconds": active_age if state.get("active") else None,
        "problems": problems,
        "state": state,
    }


def health_payload() -> tuple[dict[str, Any], int]:
    storage = storage_status()
    scheduler = hosted_scheduler_health() if hosted_scheduler_enabled() else {"ok": True, "hosted": False}
    problems = []
    if not storage.get("writable"):
        problems.append("Profile storage is not writable.")
    if storage.get("render_runtime") and not storage.get("persistent"):
        problems.append("Render persistent storage is not confirmed.")
    problems.extend(scheduler.get("problems") or [])
    payload = {
        "ok": not problems,
        "storage": storage,
        "scheduler": scheduler,
        "problems": problems,
    }
    return payload, 200 if payload["ok"] else 503


def ensure_scheduler_for_profile(profile: dict[str, Any]) -> dict[str, Any]:
    schedule = profile.get("schedule") or {}
    if hosted_scheduler_enabled() or not schedule.get("enabled", True):
        return scheduler_state()

    try:
        return {
            **install_launch_agent(PROJECT_ROOT),
            "hosted": False,
        }
    except Exception as exc:
        return {
            **scheduler_state(),
            "error": str(exc),
            "status": "install_failed",
        }


def send_profile_now(profile_id: str) -> dict[str, Any]:
    now = scheduler_now()
    mark_send_started(profile_id, now=now)
    try:
        result = run_signal_for_profile(profile_id)
    except Exception as exc:
        mark_send_failed(profile_id, str(exc), now=now)
        raise

    if result.get("status") in {"sent", "sent_no_newsletters"}:
        mark_sent(profile_id, now=now, result=result)
    else:
        mark_send_failed(profile_id, str(result), now=now)
    return result


def profile_send_running(profile_id: str) -> dict[str, Any] | None:
    state = read_json(profile_paths(profile_id).state, {})
    if mark_stale_send_if_needed(profile_id, state, now=scheduler_now()) is not None:
        return None

    if state.get("last_run_status") != "running":
        return None

    started_at = str(state.get("last_send_started_at") or "")
    return {
        "status": "already_running",
        "profile_id": profile_id,
        "started_at": started_at,
        "age_seconds": seconds_since(started_at),
        "message": "A digest send is already running for this profile.",
    }


def start_profile_send(profile_id: str) -> dict[str, Any]:
    profile_id = resolve_profile_id(profile_id)
    running = profile_send_running(profile_id)
    if running:
        return running

    with profile_send_lock:
        thread = profile_send_threads.get(profile_id)
        if thread and thread.is_alive():
            return {
                "status": "already_running",
                "profile_id": profile_id,
                "message": "A digest send is already running for this profile.",
            }

        def worker() -> None:
            try:
                send_profile_now(profile_id)
            except Exception as exc:
                print(f"Manual send failed for {profile_id}: {exc}")
            finally:
                with profile_send_lock:
                    profile_send_threads.pop(profile_id, None)

        thread = threading.Thread(
            target=worker,
            name=f"finn-signal-send-{profile_id}",
            daemon=True,
        )
        profile_send_threads[profile_id] = thread
        thread.start()

    return {
        "status": "started",
        "profile_id": profile_id,
        "message": "Digest send started. Refresh status to see completion.",
    }


def profile_id_from_digest_id(digest_id: str) -> str | None:
    parts = digest_id.rsplit("-", 3)
    if len(parts) == 4 and all(part.isdigit() for part in parts[-3:]):
        return parts[0]
    return None


def feedback_paths_for_digest(digest_id: str) -> tuple[Path, Path]:
    profile_id = profile_id_from_digest_id(digest_id)
    if profile_id:
        paths = profile_paths(profile_id)
        if paths.root.exists():
            return paths.learned_preferences, paths.root / "feedback_log.jsonl"

    return Path("data/learned_preferences.yaml"), Path("data/feedback_log.jsonl")


def manifest_items_context(manifest: dict[str, Any]) -> str:
    rows = []
    for item in manifest.get("items", []):
        rows.append(
            {
                "item_number": item.get("item_number"),
                "title": item.get("title"),
                "source": item_source(item),
                "summary": item.get("summary"),
                "why_finn_cares": item.get("why_finn_cares"),
                "why_world_cares": item.get("why_world_cares"),
                "url": item.get("url"),
                "topic_tags": item.get("topic_tags") or [],
            }
        )
    return json.dumps(rows, indent=2)


def rating_options(selected: str) -> str:
    options = []
    labels = {
        "1": "1 - no",
        "2": "2",
        "3": "3",
        "4": "4",
        "5": "5 - yes",
    }
    for value, label in labels.items():
        selected_attr = " selected" if value == selected else ""
        options.append(f'<option value="{value}"{selected_attr}>{label}</option>')
    return "\n".join(options)


def render_feedback_app(manifest: dict[str, Any], selected_item: str = "", selected_rating: str = "") -> str:
    digest_id = str(manifest.get("digest_id", ""))
    item_cards = []

    for item in manifest.get("items", []):
        item_number = str(item.get("item_number", ""))
        selected = selected_rating if item_number == selected_item else ""
        score_buttons = "\n".join(
            f'<button type="button" data-score="{value}" class="{"is-selected" if selected == str(value) else ""}">{value}</button>'
            for value in range(1, 6)
        )
        title = str(item.get("title") or "Untitled")
        source = item_source(item)
        summary = str(item.get("summary") or "")
        url = str(item.get("url") or "").strip()
        url_link = (
            f'<a class="read-link" href="{escape_attr(url)}" target="_blank" rel="noreferrer">Read article</a>'
            if url
            else ""
        )
        item_cards.append(
            f"""
            <article class="article-card" data-item="{escape_attr(item_number)}">
              <div>
                <p class="kicker">#{escape_attr(item_number)} · {escape_html(source)}</p>
                <h2>{escape_html(title)}</h2>
                <p>{escape_html(summary)}</p>
                {url_link}
              </div>
              <div class="rating-box">
                <div class="quick">
                  <button type="button" data-rate="5">Like</button>
                  <button type="button" data-rate="1">Not like</button>
                </div>
                <div class="score-label">1-5 rating</div>
                <div class="score-buttons" data-score-buttons>
                  {score_buttons}
                </div>
                <label>
                  Saved rating
                  <select data-rating class="rating-select">
                    <option value="">Skip</option>
                    {rating_options(selected)}
                  </select>
                </label>
              </div>
            </article>
            """
        )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Finn-Signal Feedback</title>
    <style>
      body {{ margin:0; background:#f7f8f3; color:#17201b; font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      main {{ width:min(1120px, calc(100vw - 28px)); margin:28px auto 60px; }}
      header {{ display:grid; gap:10px; margin-bottom:20px; background:#10231c; color:#fff; border-radius:8px; padding:24px; }}
      .eyebrow,.kicker {{ color:#c8573d; font-size:12px; font-weight:850; margin:0; text-transform:uppercase; }}
      header .eyebrow {{ color:#f1b36d; }}
      h1 {{ font-size:clamp(40px, 6vw, 72px); line-height:.98; margin:0; }}
      h2 {{ font-size:22px; line-height:1.2; margin:0 0 10px; }}
      p {{ line-height:1.48; }}
      header p:not(.eyebrow) {{ color:#d9e6de; margin:0; max-width:820px; }}
      .layout {{ display:grid; grid-template-columns:1.2fr .8fr; gap:18px; align-items:start; }}
      .stack {{ display:grid; gap:12px; }}
      .article-card,.chat,.submit-panel {{ background:#fff; border:1px solid #d9dfd8; border-radius:8px; box-shadow:0 18px 55px rgba(25,35,30,.08); padding:18px; }}
      .article-card {{ display:grid; grid-template-columns:1fr 230px; gap:18px; }}
      .article-card p {{ color:#68756d; margin:0; }}
      .read-link {{ display:inline-block; margin-top:10px; color:#1d4ed8; font-weight:800; text-decoration:none; }}
      .rating-box {{ display:grid; gap:12px; align-content:start; }}
      .quick {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
      .score-label {{ color:#68756d; font-size:13px; font-weight:850; text-transform:uppercase; }}
      .score-buttons {{ display:grid; grid-template-columns:repeat(5, 1fr); gap:7px; }}
      .score-buttons button {{ background:#eef3ed; color:#17201b; min-height:42px; padding:0; }}
      .score-buttons button:hover,.score-buttons button.is-selected {{ background:#10231c; color:#fff; }}
      button {{ min-height:46px; border:0; border-radius:7px; background:#1d6b52; color:#fff; font-weight:800; cursor:pointer; padding:0 14px; }}
      button.secondary,.quick button:last-child {{ background:#e7ece6; color:#17201b; }}
      label {{ display:grid; gap:7px; color:#68756d; font-size:14px; font-weight:800; }}
      select,textarea,input {{ width:100%; border:1px solid #d9dfd8; border-radius:7px; font:inherit; min-height:46px; padding:10px; box-sizing:border-box; }}
      .rating-select {{ height:1px; min-height:1px; opacity:0; padding:0; position:absolute; width:1px; }}
      textarea {{ min-height:110px; resize:vertical; }}
      .chat {{ position:sticky; top:18px; display:grid; gap:12px; }}
      .answer {{ background:#f2f5ee; border:1px solid #d9dfd8; border-radius:8px; min-height:80px; padding:12px; white-space:pre-wrap; }}
      .result {{ color:#1d6b52; font-weight:800; min-height:24px; }}
      @media (max-width: 860px) {{ .layout,.article-card {{ grid-template-columns:1fr; }} .chat {{ position:static; }} }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <p class="eyebrow">Finn-Signal Feedback</p>
        <h1>Rate this digest.</h1>
        <p>Use Like / Not Like or tap a 1-5 score beside each article. Then ask the article assistant anything about the pieces in this email.</p>
      </header>
      <div class="layout">
        <section class="stack" id="articles" data-digest-id="{escape_attr(digest_id)}">
          {''.join(item_cards)}
          <div class="submit-panel">
            <label>Optional note<textarea id="note" placeholder="More AI infra, less generic market noise."></textarea></label>
            <button id="submitFeedback">Save feedback</button>
            <div class="result" id="feedbackResult"></div>
          </div>
        </section>
        <aside class="chat">
          <h2>Ask about articles</h2>
          <p>Ask what matters, what to read first, or how two items connect.</p>
          <textarea id="question" placeholder="Which article is most worth reading in full?"></textarea>
          <button id="askButton">Ask</button>
          <div class="answer" id="answer"></div>
        </aside>
      </div>
    </main>
    <script>
      const digestId = document.querySelector('#articles').dataset.digestId;
      function setRating(card, value) {{
        card.querySelector('[data-rating]').value = value;
        card.querySelectorAll('[data-score]').forEach((button) => {{
          button.classList.toggle('is-selected', button.dataset.score === value);
        }});
      }}
      document.querySelectorAll('[data-rate]').forEach((button) => {{
        button.addEventListener('click', () => {{
          const card = button.closest('[data-item]');
          setRating(card, button.dataset.rate);
        }});
      }});
      document.querySelectorAll('[data-score]').forEach((button) => {{
        button.addEventListener('click', () => {{
          setRating(button.closest('[data-item]'), button.dataset.score);
        }});
      }});
      document.querySelectorAll('[data-item]').forEach((card) => {{
        const selected = card.querySelector('[data-rating]').value;
        if (selected) setRating(card, selected);
      }});
      document.querySelector('#submitFeedback').addEventListener('click', async () => {{
        const ratings = Array.from(document.querySelectorAll('[data-item]')).map((card) => ({{
          item_number: Number(card.dataset.item),
          rating: card.querySelector('[data-rating]').value,
        }})).filter((item) => item.rating);
        const response = await fetch('/api/feedback/bulk', {{
          method:'POST',
          headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{digest_id:digestId, ratings, note:document.querySelector('#note').value}})
        }});
        const data = await response.json();
        document.querySelector('#feedbackResult').textContent = response.ok ? 'Saved.' : (data.error || 'Could not save.');
      }});
      document.querySelector('#askButton').addEventListener('click', async () => {{
        const answer = document.querySelector('#answer');
        answer.textContent = 'Thinking...';
        const response = await fetch('/api/articles/chat', {{
          method:'POST',
          headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{digest_id:digestId, question:document.querySelector('#question').value}})
        }});
        const data = await response.json();
        answer.textContent = response.ok ? data.answer : (data.error || 'Could not answer.');
      }});
    </script>
  </body>
</html>"""


def escape_html(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def escape_attr(value: Any) -> str:
    return escape_html(value)


def build_oauth_flow(port: int, state: str | None = None) -> Flow:
    base_url = public_base_url(port)
    if base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1"):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    client_config = os.getenv("FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON", "").strip()
    if client_config:
        flow = Flow.from_client_config(
            json.loads(client_config),
            scopes=SCOPES,
            state=state,
        )
    else:
        flow = Flow.from_client_secrets_file(
            str(PROJECT_ROOT / "credentials.json"),
            scopes=SCOPES,
            state=state,
        )
    flow.redirect_uri = f"{base_url}/oauth2callback"
    return flow


def classify_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classified = []
    for candidate in candidates:
        try:
            result = classify_sender_with_model(candidate)
            classified.append(
                {
                    **candidate,
                    "name": result["suggested_name"],
                    "classification": result["classification"],
                    "confidence": result["confidence"],
                    "reason": result["reason"],
                    "should_include": result["should_include"],
                }
            )
        except Exception as error:
            classified.append(
                {
                    **candidate,
                    "name": candidate["sender"],
                    "classification": "unclear",
                    "confidence": 0,
                    "reason": str(error),
                    "should_include": False,
                }
            )
    return classified


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def profile_output_dir(profile_id: str) -> Path:
    return PROJECT_ROOT / "outputs" / "users" / profile_id


def compact_ranked_item(item: dict[str, Any]) -> dict[str, Any]:
    scores = item.get("scores") or {}
    return {
        "rank": item.get("rank"),
        "item_number": item.get("item_number"),
        "title": item.get("title") or "Untitled",
        "source": item_source(item),
        "newsletter_name": item.get("newsletter_name"),
        "summary": item.get("summary") or "",
        "why_finn_cares": item.get("why_finn_cares") or "",
        "why_world_cares": item.get("why_world_cares") or "",
        "ranking_note": item.get("ranking_note") or "",
        "include_in_digest": bool(item.get("include_in_digest")),
        "url": item.get("url") or item.get("link") or "",
        "topic_tags": item.get("topic_tags") or [],
        "read_time": item.get("read_time"),
        "scores": {
            "final_score": to_float(scores.get("final_score")),
            "base_score": to_float(scores.get("base_score")),
            "learned_multiplier": to_float(scores.get("learned_multiplier"), 1.0),
            "finn_relevance": to_float(scores.get("finn_relevance")),
            "global_importance": to_float(scores.get("global_importance")),
            "novelty": to_float(scores.get("novelty")),
            "actionability": to_float(scores.get("actionability")),
            "source_quality": to_float(scores.get("source_quality")),
        },
    }


def ranking_summary(items: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    digest_items = [item for item in items if item.get("include_in_digest")]
    scored_items = [to_float((item.get("scores") or {}).get("final_score")) for item in items]
    source_counts: dict[str, int] = {}
    for item in items:
        source = item_source(item)
        source_counts[source] = source_counts.get(source, 0) + 1

    top_source = ""
    if source_counts:
        top_source = sorted(source_counts.items(), key=lambda row: (-row[1], row[0]))[0][0]

    return {
        "digest_id": manifest.get("digest_id", ""),
        "created_at": manifest.get("created_at", ""),
        "total_ranked": len(items),
        "sent_in_digest": len(digest_items),
        "average_score": round(sum(scored_items) / len(scored_items), 2) if scored_items else 0,
        "top_source": top_source,
    }


def profile_rankings(profile_id: str) -> dict[str, Any]:
    profile = load_profile(profile_id)
    profile_id = profile["id"]
    output_dir = profile_output_dir(profile_id)
    scored_path = output_dir / "latest_scored_items.json"
    manifest_path = output_dir / "latest_digest_manifest.json"
    digest_path = output_dir / "finn_signal_latest.html"

    if not scored_path.exists():
        state = profile.get("state") or {}
        if state.get("last_run_status") == "sent_no_newsletters":
            result = state.get("last_run_result") or {}
            return {
                "status": "no_newsletters",
                "profile_id": profile_id,
                "message": result.get(
                    "message",
                    "The latest run sent an empty digest because no approved newsletter emails were found.",
                ),
                "items": [],
                "summary": {
                    "digest_id": result.get("digest_id", ""),
                    "created_at": state.get("last_sent_at", ""),
                    "total_ranked": 0,
                    "sent_in_digest": 0,
                    "average_score": 0,
                    "top_source": "None",
                },
                "learned_preferences": read_yaml(profile_paths(profile_id).learned_preferences, {}),
            }
        return {
            "status": "empty",
            "profile_id": profile_id,
            "message": "No ranked digest exists yet for this profile.",
            "items": [],
            "summary": {},
            "learned_preferences": read_yaml(profile_paths(profile_id).learned_preferences, {}),
        }

    ranked = read_json(scored_path, {})
    manifest = read_json(manifest_path, {}) if manifest_path.exists() else {}
    if ranked.get("status") == "no_newsletters":
        return {
            "status": "no_newsletters",
            "profile_id": profile_id,
            "message": ranked.get(
                "message",
                "The latest run sent an empty digest because no approved newsletter emails were found.",
            ),
            "summary": ranking_summary([], manifest),
            "items": [],
            "digest_sections": ranked.get("digest_sections") or {},
            "learned_preferences": read_yaml(profile_paths(profile_id).learned_preferences, {}),
            "review_url": "",
            "digest_path": str(digest_path) if digest_path.exists() else "",
        }
    items = [compact_ranked_item(item) for item in ranked.get("scored_items", [])]
    items.sort(
        key=lambda item: (
            int(item.get("rank") or 9999),
            -to_float((item.get("scores") or {}).get("final_score")),
        )
    )

    return {
        "status": "ready",
        "profile_id": profile_id,
        "summary": ranking_summary(ranked.get("scored_items", []), manifest),
        "items": items,
        "digest_sections": ranked.get("digest_sections") or {},
        "learned_preferences": read_yaml(profile_paths(profile_id).learned_preferences, {}),
        "review_url": f"/feedback?digest_id={manifest.get('digest_id')}" if manifest.get("digest_id") else "",
        "digest_path": str(digest_path) if digest_path.exists() else "",
    }


def find_source_by_sender(profile_id: str, sender: str) -> dict[str, Any]:
    sender_key = sender.strip().lower()
    for source in read_sources(profile_id):
        if sender_key in {value.lower() for value in source.get("senders", [])}:
            return source
    raise FileNotFoundError(f"No source found for {sender}.")


def source_confirmation_query(profile: dict[str, Any], source: dict[str, Any], days: int = 30) -> str:
    subscription_email = str(source.get("subscription_email") or profile.get("subscription_email") or profile.get("email"))
    sender_values = [
        value
        for value in (extract_sender_query_value(sender) for sender in source.get("senders", []))
        if value
    ]
    to_query = f"(to:{subscription_email} OR deliveredto:{subscription_email})"
    if sender_values:
        sender_query = " OR ".join([f"from:{value}" for value in sorted(set(sender_values))])
        return f"newer_than:{days}d -in:spam -in:trash {to_query} ({sender_query})"
    return f"newer_than:{days}d -in:spam -in:trash {to_query}"


def check_source_confirmation(profile_id: str, sender: str, days: int = 30) -> dict[str, Any]:
    profile = load_profile(profile_id)
    source = find_source_by_sender(profile_id, sender)
    query = source_confirmation_query(profile, source, days=days)
    emails = fetch_recent_emails(
        max_results=10,
        query=query,
        token_path=profile_paths(profile_id).token,
    )
    now = datetime.now().isoformat(timespec="seconds")
    if emails:
        sources = set_source_status(
            profile_id,
            sender=sender,
            status="receiving",
            extra={
                "confirmation_checked_at": now,
                "confirmed_from_email_id": emails[0].get("id", ""),
            },
        )
        return {"status": "receiving", "matched": True, "emails": emails[:3], "sources": sources}

    sources = set_source_status(
        profile_id,
        sender=sender,
        status="pending_confirmation",
        extra={"confirmation_checked_at": now},
    )
    return {"status": "pending_confirmation", "matched": False, "emails": [], "sources": sources}


def site_guide_context() -> str:
    return f"""
Finn-Signal is a web console with these tabs:
- Onboarding: create a profile, connect Gmail, scan recent mail, approve newsletter candidates.
- Rankings: inspect the latest ranked digest, score breakdowns, model reasoning, and article links.
- Sources: manually add newsletter senders, inspect subscription status, check Gmail for first emails, and turn tracked senders on or off.
- Discover: type a natural-language topic request, then use Try subscribe so Finn-Signal attempts the signup and falls back to manual signup when needed.
- Schedule: change delivery time/frequency for the hosted Render sender.
- Runs: send exactly one digest immediately using receiving tracked sources.

Visual map:
- The profile selector is in the top-right header.
- The tab bar sits under the header.
- The status strip shows Gmail, Sources, Delivery, and Scheduler.
- The bottom-right Help drawer is available across tabs.

Highlightable targets, by target key:
{json.dumps(SITE_GUIDE_TARGETS, indent=2)}

When the user asks where to do something, answer in plain language and include target keys for the controls to highlight.
Return only JSON with this shape:
{{"answer":"short helpful answer","targets":["one_or_more_target_keys"]}}
""".strip()


def parse_site_guide_output(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"answer": text.strip(), "targets": []}

    answer = str(parsed.get("answer") or "").strip()
    targets = [
        str(target)
        for target in parsed.get("targets", [])
        if str(target) in SITE_GUIDE_TARGETS
    ]
    return {"answer": answer or "I can help you find the right control.", "targets": targets}


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "FinnSignalConsole/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                payload, status = health_payload()
                self.send_json(payload, status=status)
                return

            if parsed.path == "/api/state":
                self.send_json(
                    {
                        "profiles": list_profiles(),
                        "storage": storage_status(),
                        "scheduler": scheduler_state(),
                    }
                )
                return

            if parsed.path == "/api/profiles":
                self.send_json({"profiles": list_profiles()})
                return

            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "rankings":
                profile_id = resolve_profile_id(clean_profile_id(parts[2]))
                self.send_json(profile_rankings(profile_id))
                return

            if parsed.path.startswith("/api/profiles/") and parsed.path.endswith("/sources"):
                profile_id = resolve_profile_id(clean_profile_id(parsed.path.split("/")[3]))
                self.send_json({"sources": read_sources(profile_id)})
                return

            if parsed.path == "/oauth2callback":
                self.handle_oauth_callback(parsed)
                return

            if parsed.path == "/feedback":
                self.handle_feedback_page(parsed)
                return

            self.serve_static(parsed.path)
        except Exception as error:
            traceback.print_exc()
            self.send_error_json(500, str(error))

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            body = self.read_body()

            if parsed.path == "/api/profiles":
                profile = create_profile(
                    display_name=str(body.get("display_name", "")),
                    email=str(body.get("email", "")),
                    subscription_email=str(body.get("subscription_email", "")),
                    interests=str(body.get("interests", "")),
                )
                self.send_json({"profile": profile}, status=201)
                return

            if parsed.path == "/api/scheduler/install":
                if hosted_scheduler_enabled():
                    self.send_json(
                        {
                            "scheduler": {
                                **scheduler_state(),
                                "hosted": True,
                                "message": "Hosted scheduler is enabled on Render. No local install is needed.",
                            }
                        }
                    )
                    return
                self.send_json({"scheduler": install_launch_agent(PROJECT_ROOT)})
                return

            if parsed.path == "/api/feedback/bulk":
                self.handle_bulk_feedback(body)
                return

            if parsed.path == "/api/articles/chat":
                self.handle_article_chat(body)
                return

            if parsed.path == "/api/site-guide/chat":
                self.handle_site_guide_chat(body)
                return

            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "profiles":
                profile_id = clean_profile_id(parts[2])
                action = parts[3:] if len(parts) > 3 else []
                self.handle_profile_post(profile_id, action, body)
                return

            self.send_error_json(404, "Unknown endpoint.")
        except Exception as error:
            traceback.print_exc()
            self.send_error_json(500, str(error))

    def handle_profile_post(
        self,
        profile_id: str,
        action: list[str],
        body: dict[str, Any],
    ) -> None:
        profile = load_profile(profile_id)
        profile_id = profile["id"]

        if action == ["oauth", "start"]:
            flow = build_oauth_flow(self.port)
            auth_url, state = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
            oauth_sessions[state] = {
                "profile_id": profile_id,
                "flow": flow,
            }
            self.send_json({"auth_url": auth_url})
            return

        if action == ["scan"]:
            paths = profile_paths(profile_id)
            candidates = discover_newsletters(
                days=int(body.get("days", 30)),
                max_results=int(body.get("max_results", 300)),
                token_path=paths.token,
            )
            classified = classify_candidates(candidates)
            write_json(paths.candidates, classified)
            self.send_json({"candidates": classified})
            return

        if action == ["sources"]:
            sources = upsert_source(profile_id, body)
            self.send_json({"sources": sources})
            return

        if action == ["sources", "toggle"]:
            sources = set_source_enabled(
                profile_id,
                sender=str(body.get("sender", "")),
                enabled=bool(body.get("enabled", True)),
            )
            self.send_json({"sources": sources})
            return

        if action == ["sources", "status"]:
            sources = set_source_status(
                profile_id,
                sender=str(body.get("sender", "")),
                status=str(body.get("status", "needs_subscription")),
            )
            self.send_json({"sources": sources})
            return

        if action == ["sources", "check"]:
            self.send_json(
                check_source_confirmation(
                    profile_id,
                    sender=str(body.get("sender", "")),
                    days=int(body.get("days", 30)),
                )
            )
            return

        if action == ["sources", "import-candidates"]:
            selected = body.get("candidates", [])
            for candidate in selected:
                upsert_source(
                    profile_id,
                    {
                        "name": candidate.get("name") or candidate.get("sender"),
                        "senders": [candidate.get("sender", "")],
                        "enabled": True,
                        "source_type": "gmail_scan",
                        "status": "receiving",
                        "reason": candidate.get("reason", ""),
                    },
                )
            self.send_json({"sources": read_sources(profile_id)})
            return

        if action == ["discover"]:
            profile = load_profile(profile_id)
            user_context = f"{profile.get('display_name')} <{profile.get('email')}>"
            recommendations = discover_recommendations(
                query=str(body.get("query", "")),
                user_context=user_context,
                limit=int(body.get("limit", 6)),
            )
            write_json(profile_paths(profile_id).discovery, recommendations)
            if body.get("auto_add", True):
                for recommendation in recommendations:
                    source = recommendation_to_source(
                        recommendation,
                        subscription_email=str(profile.get("subscription_email", "")),
                        status="needs_subscription",
                    )
                    if source.get("senders"):
                        upsert_source(profile_id, source)
            self.send_json(
                {
                    "recommendations": recommendations,
                    "sources": read_sources(profile_id),
                }
            )
            return

        if action == ["recommendations", "add"]:
            profile = load_profile(profile_id)
            mode = str(body.get("mode", "track"))
            status = "needs_subscription"
            subscription_result = {}
            if mode == "subscribe":
                subscription_result = attempt_newsletter_subscription(
                    subscription_url=str((body.get("recommendation") or body).get("subscription_url", "")),
                    subscription_email=str(profile.get("subscription_email", "")),
                )
                status = (
                    "pending_confirmation"
                    if subscription_result.get("status") == "submitted"
                    else "manual_required"
                )
            source = recommendation_to_source(
                body.get("recommendation", body),
                subscription_email=str(profile.get("subscription_email", "")),
                status=status,
            )
            if subscription_result:
                source["subscription_result"] = subscription_result
                source["signup_attempted_at"] = datetime.now().isoformat(timespec="seconds")
            sources = upsert_source(profile_id, source)
            self.send_json({"sources": sources, "source": source, "subscription": subscription_result})
            return

        if action == ["schedule"]:
            profile = update_schedule(
                profile_id,
                time=str(body.get("time", "11:00")),
                frequency=str(body.get("frequency", "daily")),
                enabled=bool(body.get("enabled", True)),
            )
            self.send_json({"profile": profile, "scheduler": ensure_scheduler_for_profile(profile)})
            return

        if action == ["send-test"]:
            self.send_json({"result": start_profile_send(profile_id)})
            return

        self.send_error_json(404, "Unknown profile action.")

    def handle_feedback_page(self, parsed) -> None:
        params = parse_qs(parsed.query)
        digest_id = (params.get("digest_id") or [""])[0]
        selected_item = (params.get("item") or [""])[0]
        selected_rating = (params.get("rating") or [""])[0]

        try:
            manifest = load_manifest(digest_id)
        except Exception as error:
            self.send_html(
                f"<h1>Digest not found</h1><p>{str(error)}</p>",
                status=404,
            )
            return

        body = render_feedback_app(manifest, selected_item, selected_rating)
        self.send_html(body)

    def handle_bulk_feedback(self, body: dict[str, Any]) -> None:
        digest_id = str(body.get("digest_id", "")).strip()
        manifest = load_manifest(digest_id)
        ratings = []

        for rating in body.get("ratings", []):
            try:
                item_number = int(rating.get("item_number"))
                rating_value = int(rating.get("rating"))
            except (TypeError, ValueError):
                continue
            if 1 <= rating_value <= 5:
                ratings.append({"item_number": item_number, "rating": rating_value})

        note = str(body.get("note", "")).strip()
        raw_feedback = ", ".join(
            f"{rating['item_number']}:{rating['rating']}"
            for rating in ratings
        )
        if note:
            raw_feedback = f"{raw_feedback}\n{note}".strip()

        learned_path, log_path = feedback_paths_for_digest(digest_id)
        result = apply_parsed_feedback(
            raw_feedback=raw_feedback,
            parsed_feedback={
                "item_ratings": ratings,
                "topic_adjustments": [],
                "source_adjustments": [],
                "rules": [],
                "style_notes": [note] if note else [],
            },
            manifest=manifest,
            learned_preferences_path=learned_path,
            feedback_log_path=log_path,
        )
        self.send_json({"ok": True, "result": result})

    def handle_article_chat(self, body: dict[str, Any]) -> None:
        digest_id = str(body.get("digest_id", "")).strip()
        question = str(body.get("question", "")).strip()
        if not question:
            self.send_error_json(400, "Ask a question first.")
            return

        manifest = load_manifest(digest_id)
        user_name = str(manifest.get("user_name") or "the user")
        response = openai_client.responses.create(
            model=get_cheap_model(),
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are Finn-Signal's article assistant. Answer only from the provided digest articles. "
                        f"This digest was personalized for {user_name}. "
                        "Be concise, cite article numbers and titles, and say when the digest does not contain enough information."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Digest articles:\n{manifest_items_context(manifest)}\n\nQuestion:\n{question}",
                },
            ],
        )
        self.send_json({"answer": response.output_text})

    def handle_site_guide_chat(self, body: dict[str, Any]) -> None:
        question = str(body.get("question", "")).strip()
        if not question:
            self.send_error_json(400, "Ask a question first.")
            return

        page_state = {
            "active_tab": body.get("active_tab"),
            "profile": body.get("profile"),
            "visible_counts": body.get("visible_counts") or {},
        }
        response = openai_client.responses.create(
            model=get_cheap_model(),
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are Finn-Signal's site guide for non-technical users. "
                        "Use the UI map to tell the user exactly where to click next. "
                        "Do not claim you clicked anything yourself."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"UI map and instructions:\n{site_guide_context()}\n\n"
                        f"Current page state:\n{json.dumps(page_state, indent=2)}\n\n"
                        f"User question:\n{question}"
                    ),
                },
            ],
        )
        result = parse_site_guide_output(response.output_text)
        result["highlights"] = [SITE_GUIDE_TARGETS[target] for target in result["targets"]]
        self.send_json(result)

    def handle_oauth_callback(self, parsed) -> None:
        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        session = oauth_sessions.pop(state, None)
        if not session:
            self.send_html("<h1>Finn-Signal</h1><p>OAuth session expired. Start again.</p>", 400)
            return

        profile_id = session["profile_id"]
        flow = session["flow"]
        callback_url = f"{public_base_url(self.port)}{self.path}"
        flow.fetch_token(authorization_response=callback_url)

        paths = profile_paths(profile_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.token.write_text(flow.credentials.to_json(), encoding="utf-8")

        self.send_html(
            """
<!doctype html>
<html>
  <head><title>Finn-Signal connected</title></head>
  <body style="font-family:Arial,sans-serif;margin:40px;color:#111827;">
    <h1>Gmail connected</h1>
    <p>You can close this tab and return to Finn-Signal.</p>
  </body>
</html>
""".strip()
        )

    def serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            file_path = WEB_ROOT / "index.html"
        else:
            safe_path = request_path.lstrip("/")
            file_path = WEB_ROOT / safe_path

        try:
            file_path.resolve().relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error_json(403, "Forbidden.")
            return

        if not file_path.exists() or not file_path.is_file():
            self.send_error_json(404, "Not found.")
            return

        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = console_port()
    host = console_host()
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    print(f"Finn-Signal console running at {public_base_url(port)}")
    start_hosted_scheduler_if_enabled()
    server.serve_forever()


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def start_hosted_scheduler_if_enabled() -> None:
    if not bool_env("FINN_SIGNAL_ENABLE_HOSTED_SCHEDULER", False):
        return

    interval_seconds = max(60, int(os.getenv("FINN_SIGNAL_SCHEDULER_INTERVAL_SECONDS", "300")))
    mark_hosted_scheduler_started(interval_seconds)

    def loop() -> None:
        print(f"Hosted scheduler enabled. Checking every {interval_seconds}s.")
        while True:
            try:
                mark_hosted_scheduler_loop_started()
                summary = run_scheduled_profiles()
                mark_hosted_scheduler_loop_finished(summary)
            except Exception as error:
                mark_hosted_scheduler_loop_failed(str(error))
                print(f"Hosted scheduler error: {error}")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=loop, name="finn-signal-scheduler", daemon=True)
    thread.start()


if __name__ == "__main__":
    main()
