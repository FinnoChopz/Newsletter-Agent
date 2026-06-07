from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI

from app.config import get_cheap_model


load_dotenv()
client = OpenAI()


DISCOVERY_SYSTEM = """
You are finding high-signal newsletters and Substacks for a user's personal
daily intelligence digest. Recommend publications, not one-off articles.

Optimize for:
- consistently thoughtful analysis
- useful signal density
- credible author or institution
- likely relevance to the user's query
- discoverable sender address, domain, or subscription URL

Return only JSON that matches the requested schema.
""".strip()


DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "why_relevant": {"type": "string"},
                    "subscription_url": {"type": "string"},
                    "likely_senders": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number"},
                },
                "required": [
                    "name",
                    "description",
                    "why_relevant",
                    "subscription_url",
                    "likely_senders",
                    "topics",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


def parse_json_response(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def normalize_recommendation(raw: dict[str, Any]) -> dict[str, Any]:
    senders = [
        str(sender).strip()
        for sender in raw.get("likely_senders", [])
        if str(sender).strip()
    ]
    topics = [
        str(topic).strip()
        for topic in raw.get("topics", [])
        if str(topic).strip()
    ]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "name": str(raw.get("name") or "Unnamed newsletter").strip(),
        "description": str(raw.get("description") or "").strip(),
        "why_relevant": str(raw.get("why_relevant") or "").strip(),
        "subscription_url": str(raw.get("subscription_url") or "").strip(),
        "likely_senders": senders,
        "topics": topics,
        "confidence": confidence,
    }


def normalize_recommendations(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        normalize_recommendation(item)
        for item in data.get("recommendations", [])
        if isinstance(item, dict)
    ]


def recommendation_to_source(recommendation: dict[str, Any]) -> dict[str, Any]:
    senders = recommendation.get("likely_senders") or []
    if not senders and recommendation.get("subscription_url"):
        domain = urlparse(recommendation["subscription_url"]).netloc
        if domain:
            senders = [domain.removeprefix("www.")]

    return {
        "name": recommendation.get("name", "Discovered newsletter"),
        "senders": senders,
        "enabled": True,
        "source_type": "discovered",
        "status": "needs_subscription",
        "reason": recommendation.get("why_relevant", ""),
        "topics": recommendation.get("topics", []),
        "subscription_url": recommendation.get("subscription_url", ""),
    }


def discover_recommendations(
    query: str,
    user_context: str = "",
    limit: int = 6,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        raise ValueError("Describe what kind of newsletters you want.")

    prompt = f"""
User request:
{query}

User context:
{user_context or "No extra profile context provided."}

Return {limit} or fewer newsletter/publication recommendations. Prefer exact
subscription URLs and likely sender addresses. If you are uncertain about a
sender address, provide the publication domain instead and lower confidence.
""".strip()

    try:
        response = client.responses.create(
            model=get_cheap_model(),
            tools=[{"type": "web_search_preview"}],
            input=[
                {"role": "system", "content": DISCOVERY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "newsletter_recommendations",
                    "schema": DISCOVERY_SCHEMA,
                    "strict": True,
                }
            },
        )
    except Exception:
        response = client.responses.create(
            model=get_cheap_model(),
            input=[
                {"role": "system", "content": DISCOVERY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "newsletter_recommendations",
                    "schema": DISCOVERY_SCHEMA,
                    "strict": True,
                }
            },
        )

    recommendations = normalize_recommendations(parse_json_response(response.output_text))
    timestamp = datetime.now().isoformat(timespec="seconds")
    for recommendation in recommendations:
        recommendation["discovered_at"] = timestamp
        recommendation["query"] = query
    return recommendations[:limit]
