import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from app.config import (
    get_bcc_recipients,
    get_feedback_base_url,
    get_feedback_email,
    get_recipients,
)
from app.digest import render_html_digest
from app.manifests import save_manifest
from app.ranking import build_digest_manifest, load_yaml_file, max_digest_items, rank_scored_items


load_dotenv()

OUTPUT_DIR = Path("outputs")
TRAINING_DIR = OUTPUT_DIR / "training"


def item(
    title: str,
    summary: str,
    tags: list[str],
    finn: float,
    world: float,
    novelty: float,
    actionability: float,
    source_quality: float,
    source: str,
    why_finn: str,
    why_world: str,
) -> dict:
    return {
        "title": title,
        "summary": summary,
        "topic_tags": tags,
        "source": source,
        "newsletter_name": source,
        "scores": {
            "finn_relevance": finn,
            "global_importance": world,
            "novelty": novelty,
            "actionability": actionability,
            "source_quality": source_quality,
            "final_score": 0,
        },
        "why_finn_cares": why_finn,
        "why_world_cares": why_world,
        "include_in_digest": True,
    }


def broad_calibration_items() -> list[dict]:
    return [
        item(
            "Open-weight model matches last year's frontier coding benchmark",
            "A new local model can run on a high-end laptop while matching older frontier systems on practical coding tasks.",
            ["local models", "AI models", "developer tools"],
            9,
            7,
            8,
            9,
            7,
            "Training Set",
            "This tests how strongly local models and practical AI tools should be favored.",
            "Cheap capable local models change access, privacy, and software workflows.",
        ),
        item(
            "Routine market recap: indexes drift after mixed earnings",
            "Major indexes moved less than one percent after a mixed earnings day and no major macro surprise.",
            ["routine market updates", "markets", "investing"],
            4,
            3,
            2,
            2,
            6,
            "Training Set",
            "This tests whether ordinary market noise should be suppressed.",
            "Low-volatility market recaps rarely matter beyond short-term trading.",
        ),
        item(
            "Congress advances AI liability framework",
            "A bipartisan proposal would define liability for AI agents that make financial, medical, or employment decisions.",
            ["AI regulation", "politics", "AI agents"],
            8,
            8,
            7,
            6,
            7,
            "Training Set",
            "This tests interest in policy that affects agent deployment.",
            "Liability rules can shape how AI agents are adopted across the economy.",
        ),
        item(
            "New study links sleep timing to working memory",
            "A large longitudinal study finds consistent sleep timing predicts working-memory gains more than total sleep duration.",
            ["cognitive science", "neuroscience", "health"],
            7,
            5,
            6,
            5,
            7,
            "Training Set",
            "This tests appetite for brain and behavior science with practical implications.",
            "If replicated, it changes how people think about sleep optimization.",
        ),
        item(
            "Border clash raises risk of regional escalation",
            "Two neighboring states exchanged strikes near a disputed border after weeks of failed talks.",
            ["geopolitics", "war", "regional escalation"],
            7,
            9,
            6,
            4,
            7,
            "Training Set",
            "This tests the global-importance override for geopolitics.",
            "Escalation risk can reshape alliances, energy markets, and security policy.",
        ),
        item(
            "Apple files patent for health-sensing AirPods gesture system",
            "A patent describes earbud sensors that combine health signals with gesture controls, but no product launch is confirmed.",
            ["Apple", "wearable AI", "patents"],
            6,
            4,
            5,
            3,
            5,
            "Training Set",
            "This tests how much speculative Apple/wearables material should be included.",
            "Patents can hint at direction, but most never become products.",
        ),
        item(
            "Data center power deal locks up small modular reactor output",
            "A cloud provider signed a long-term deal to buy output from planned small modular reactors for AI data centers.",
            ["AI infrastructure", "data centers", "energy", "frontier technology"],
            8,
            8,
            7,
            6,
            7,
            "Training Set",
            "This tests interest in AI infrastructure and energy bottlenecks.",
            "AI compute demand is increasingly shaping power markets and industrial policy.",
        ),
        item(
            "Celebrity founder launches another productivity app",
            "A well-known founder announced a lightweight productivity app with vague AI features and no clear technical novelty.",
            ["generic product hype", "startups", "productivity"],
            4,
            3,
            2,
            3,
            5,
            "Training Set",
            "This tests whether startup/productivity hype should be filtered out.",
            "Most vague AI productivity launches are not strategically important.",
        ),
    ]


def ai_agents_items() -> list[dict]:
    return [
        item(
            "Browser agent completes multi-step refund workflow",
            "A new agent benchmark shows reliable completion of support workflows across email, forms, and payment portals.",
            ["AI agents", "browser agents", "automation"],
            9,
            8,
            8,
            9,
            7,
            "Training Set",
            "This tests whether practical agent capability shifts should be near the top.",
            "Reliable browser agents would change labor, software, and customer operations.",
        ),
        item(
            "Memory system improves assistant continuity over 90 days",
            "A consumer AI assistant update claims better long-horizon personalization from structured memory summaries.",
            ["personalized AI memory systems", "AI assistants", "consumer AI"],
            10,
            7,
            7,
            8,
            7,
            "Training Set",
            "This tests Finn's interest in persistent AI memory.",
            "Persistent memory can make assistants more useful and more locked-in.",
        ),
        item(
            "AI sales agent startup raises large round on thin demo",
            "The company says it automates outbound sales, but the demo mostly shows scripted email generation.",
            ["AI agents", "generic product hype", "startups"],
            5,
            4,
            2,
            3,
            5,
            "Training Set",
            "This tests whether AI-agent branding alone should be penalized.",
            "Thin demos can distort the signal around genuinely capable agents.",
        ),
    ] + broad_calibration_items()


def market_noise_items() -> list[dict]:
    return [
        item(
            "Fed official repeats cautious rate-cut language",
            "A policymaker repeated prior guidance that rates may come down later if inflation continues cooling.",
            ["routine market updates", "monetary policy", "markets"],
            4,
            4,
            2,
            2,
            7,
            "Training Set",
            "This tests whether repeated macro commentary should be skipped.",
            "Repeated guidance is low-signal unless it changes market expectations.",
        ),
        item(
            "Regional bank shares fall after surprise liquidity warning",
            "A midsize bank warned deposits fell faster than expected, reviving concern about funding stress.",
            ["financial crisis", "banks", "markets", "systemic risk"],
            7,
            8,
            7,
            6,
            7,
            "Training Set",
            "This tests the distinction between routine markets and systemic-risk markets.",
            "Bank funding stress can spread quickly if confidence breaks.",
        ),
    ] + broad_calibration_items()


SCENARIOS = {
    "broad": broad_calibration_items,
    "ai-agents": ai_agents_items,
    "markets": market_noise_items,
}


def load_latest_items() -> list[dict]:
    path = OUTPUT_DIR / "latest_scored_items.json"
    if not path.exists():
        raise FileNotFoundError("outputs/latest_scored_items.json does not exist yet.")

    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("scored_items", [])


def choose_scenario() -> str:
    names = list(SCENARIOS) + ["latest"]
    print("Choose a training digest:")
    for index, name in enumerate(names, start=1):
        print(f"{index}. {name}")

    answer = input("Scenario number: ").strip()
    try:
        selected = names[int(answer) - 1]
    except (ValueError, IndexError):
        selected = "broad"

    return selected


def build_training_digest(scenario: str) -> tuple[str, dict, str]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    digest_id = f"training-{scenario}-{timestamp}"
    base_preferences = load_yaml_file("data/preferences.yaml")
    learned_preferences = load_yaml_file("data/learned_preferences.yaml")

    if scenario == "latest":
        scored_items = load_latest_items()
    else:
        scored_items = SCENARIOS[scenario]()

    ranked = rank_scored_items(
        {"scored_items": scored_items},
        learned_preferences=learned_preferences,
        max_items=max_digest_items(base_preferences),
    )
    manifest = build_digest_manifest(
        ranked,
        digest_id=digest_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    manifest["training"] = True
    manifest["scenario"] = scenario

    digest_html = render_html_digest(
        ranked_data=ranked,
        digest_id=digest_id,
        feedback_email=get_feedback_email(),
        feedback_base_url=get_feedback_base_url(),
    )

    return digest_id, manifest, digest_html


def write_training_outputs(digest_id: str, manifest: dict, digest_html: str) -> Path:
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    digest_path = TRAINING_DIR / f"{digest_id}.html"
    digest_path.write_text(digest_html, encoding="utf-8")
    save_manifest(manifest)
    return digest_path


def send_training_email(digest_id: str, digest_html: str) -> None:
    from app.email_sender import send_email

    send_email(
        to=", ".join(get_recipients()),
        bcc=", ".join(get_bcc_recipients()),
        subject=f"Finn-Signal Training - {digest_id}",
        body=digest_html,
        html=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Finn-Signal training digest.")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS) + ["latest"],
        help="Training digest to generate.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Email the training digest after writing it.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available training scenarios.",
    )
    args = parser.parse_args()

    if args.list:
        for name in list(SCENARIOS) + ["latest"]:
            print(name)
        return

    scenario = args.scenario or choose_scenario()
    digest_id, manifest, digest_html = build_training_digest(scenario)
    digest_path = write_training_outputs(digest_id, manifest, digest_html)

    print(f"Training digest: {digest_id}")
    print(f"Items: {len(manifest['items'])}")
    print(f"Saved preview: {digest_path}")
    print(f"Saved manifest: outputs/manifests/{digest_id}.json")

    if args.send:
        send_training_email(digest_id, digest_html)
        print("Sent training email.")
    else:
        print("Preview only. Add --send to email it.")

    print("\nReply to the email with feedback like:")
    print("1:5, 2:1. More local models. Less routine market updates.")
    print("\nThen run:")
    print("python process_feedback.py")


if __name__ == "__main__":
    main()
