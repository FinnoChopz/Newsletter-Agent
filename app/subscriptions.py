from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


CAPTCHA_TERMS = [
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cloudflare turnstile",
    "verify you are human",
]

DEFAULT_HEADERS = {
    "User-Agent": "Finn-Signal subscription assistant/1.0",
}


@dataclass(frozen=True)
class PreparedSubscription:
    method: str
    url: str
    data: dict[str, str]


def page_has_captcha(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in CAPTCHA_TERMS)


def input_name(input_tag) -> str:
    return str(input_tag.get("name") or input_tag.get("id") or "").strip()


def input_value(input_tag) -> str:
    return str(input_tag.get("value") or "").strip()


def looks_like_email_field(input_tag) -> bool:
    field_type = str(input_tag.get("type") or "text").lower()
    name = input_name(input_tag).lower()
    placeholder = str(input_tag.get("placeholder") or "").lower()
    return field_type == "email" or "email" in name or "e-mail" in name or "email" in placeholder


def prepare_subscription_submission(
    page_url: str,
    html: str,
    subscription_email: str,
) -> PreparedSubscription | None:
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")

    for form in forms:
        fields = form.find_all(["input", "select", "textarea"])
        email_fields = [field for field in fields if field.name == "input" and looks_like_email_field(field)]
        if not email_fields:
            continue

        data: dict[str, str] = {}
        for field in fields:
            name = input_name(field)
            if not name:
                continue

            field_type = str(field.get("type") or "text").lower()
            if field.name == "select":
                selected = field.find("option", selected=True) or field.find("option")
                data[name] = input_value(selected) if selected else ""
            elif field.name == "textarea":
                data[name] = str(field.text or "")
            elif looks_like_email_field(field):
                data[name] = subscription_email
            elif field_type in {"submit", "button", "image", "file", "reset"}:
                continue
            elif field_type in {"checkbox", "radio"}:
                if field.has_attr("checked") or field.has_attr("required"):
                    data[name] = input_value(field) or "on"
            else:
                data[name] = input_value(field)

        action = str(form.get("action") or page_url)
        method = str(form.get("method") or "get").lower()
        return PreparedSubscription(
            method="post" if method == "post" else "get",
            url=urljoin(page_url, action),
            data=data,
        )

    return None


def attempt_newsletter_subscription(
    subscription_url: str,
    subscription_email: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    if not subscription_url:
        return {
            "status": "manual_required",
            "reason": "No subscription URL was found for this recommendation.",
        }
    if not subscription_email or "@" not in subscription_email:
        return {
            "status": "manual_required",
            "reason": "No valid subscription email is configured for this profile.",
        }

    try:
        page_request = Request(subscription_url, headers=DEFAULT_HEADERS)
        with urlopen(page_request, timeout=timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="ignore")
            page_url = response.geturl() or subscription_url
    except Exception as exc:
        return {
            "status": "manual_required",
            "reason": f"Could not open the subscription page automatically: {exc}",
        }

    if page_has_captcha(html):
        return {
            "status": "manual_required",
            "reason": "The subscription page appears to require a human verification step.",
        }

    prepared = prepare_subscription_submission(page_url, html, subscription_email)
    if not prepared:
        return {
            "status": "manual_required",
            "reason": "No standard email signup form was found on the subscription page.",
        }

    encoded = urlencode(prepared.data).encode("utf-8")
    request_url = prepared.url
    request_data = encoded if prepared.method == "post" else None
    if prepared.method == "get":
        separator = "&" if "?" in request_url else "?"
        request_url = f"{request_url}{separator}{encoded.decode('utf-8')}"

    try:
        submit_request = Request(
            request_url,
            data=request_data,
            headers={
                **DEFAULT_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST" if prepared.method == "post" else "GET",
        )
        with urlopen(submit_request, timeout=timeout_seconds) as response:
            result_html = response.read().decode("utf-8", errors="ignore")
            final_url = response.geturl() or request_url
    except Exception as exc:
        return {
            "status": "manual_required",
            "reason": f"The signup form could not be submitted automatically: {exc}",
            "form_url": prepared.url,
        }

    if page_has_captcha(result_html):
        return {
            "status": "manual_required",
            "reason": "The signup flow hit a human verification step after submission.",
            "form_url": prepared.url,
            "final_url": final_url,
        }

    return {
        "status": "submitted",
        "reason": "Signup form submitted. Check Gmail for a welcome or confirmation email.",
        "form_url": prepared.url,
        "final_url": final_url,
    }
