import hashlib
import hmac
import json
import os
from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


RESEND_API_URL = "https://api.resend.com/emails"


def feedback_signature(secret: str, digest_id: str, item_number: int, rating: int) -> str:
    message = f"{digest_id}:{item_number}:{rating}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def html_page(title: str, body: str, status: int = 200) -> tuple[int, bytes]:
    document = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title>
  </head>
  <body style="margin:0;background:#f3f5f8;font-family:Arial,Helvetica,sans-serif;color:#111827;">
    <main style="max-width:560px;margin:48px auto;padding:0 18px;">
      <section style="background:#ffffff;border:1px solid #d8dee8;border-radius:8px;padding:24px;">
        {body}
      </section>
    </main>
  </body>
</html>"""
    return status, document.encode("utf-8")


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def first_param(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name) or []
    return values[0] if values else None


def validate_params(params: dict[str, list[str]]) -> tuple[dict, str | None]:
    digest_id = (first_param(params, "digest_id") or "").strip()
    item_number = parse_int(first_param(params, "item"))
    rating = parse_int(first_param(params, "rating"))
    sig = (first_param(params, "sig") or "").strip()

    if not digest_id or len(digest_id) > 100:
        return {}, "Missing or invalid digest id."

    if item_number is None or item_number < 1 or item_number > 500:
        return {}, "Missing or invalid item number."

    if rating is None or rating < 1 or rating > 5:
        return {}, "Missing or invalid rating."

    secret = os.environ.get("FINN_SIGNAL_FEEDBACK_SECRET", "").strip()
    if secret:
        expected = feedback_signature(secret, digest_id, item_number, rating)
        if not sig or not hmac.compare_digest(sig, expected):
            return {}, "Invalid feedback signature."

    return {
        "digest_id": digest_id,
        "item_number": item_number,
        "rating": rating,
        "sig": sig,
    }, None


def rating_label(rating: int) -> str:
    if rating >= 4:
        return "More like this"

    if rating <= 2:
        return "Less like this"

    return "Neutral"


def confirmation_form(event: dict) -> str:
    params = {
        "digest_id": event["digest_id"],
        "item": str(event["item_number"]),
        "rating": str(event["rating"]),
    }

    if event.get("sig"):
        params["sig"] = event["sig"]

    hidden_inputs = "\n".join(
        f'<input type="hidden" name="{escape(key)}" value="{escape(value)}">'
        for key, value in params.items()
    )

    return f"""
      <div style="font-size:12px;font-weight:800;text-transform:uppercase;color:#2563eb;margin-bottom:8px;">Finn-Signal Feedback</div>
      <h1 style="font-size:24px;line-height:1.2;margin:0 0 12px 0;">Confirm feedback</h1>
      <p style="font-size:16px;line-height:1.5;margin:0 0 18px 0;color:#374151;">
        Mark item <strong>#{event["item_number"]}</strong> as <strong>{escape(rating_label(event["rating"]))}</strong>.
      </p>
      <form method="post" action="/api/feedback" style="margin:0;">
        {hidden_inputs}
        <button type="submit" style="border:0;background:#111827;color:#ffffff;font-size:15px;font-weight:700;border-radius:8px;padding:12px 16px;cursor:pointer;">Confirm</button>
      </form>
    """


def feedback_email_body(event: dict) -> str:
    return f"""{event["item_number"]}:{event["rating"]}

source: hosted feedback button
digest_id: {event["digest_id"]}
rating_label: {rating_label(event["rating"])}
"""


def send_feedback_email(event: dict) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    to_email = (
        os.environ.get("FINN_SIGNAL_FEEDBACK_TO", "").strip()
        or os.environ.get("FINN_SIGNAL_FEEDBACK_EMAIL", "").strip()
    )
    from_email = (
        os.environ.get("FINN_SIGNAL_FEEDBACK_FROM", "").strip()
        or "Finn-Signal <onboarding@resend.dev>"
    )

    if not api_key:
        raise RuntimeError("Missing RESEND_API_KEY.")

    if not to_email:
        raise RuntimeError("Missing FINN_SIGNAL_FEEDBACK_TO.")

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": f"Re: Finn-Signal - {event['digest_id']}",
        "text": feedback_email_body(event),
    }
    request = Request(
        RESEND_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"Resend returned status {response.status}.")


def success_body(event: dict) -> str:
    return f"""
      <div style="font-size:12px;font-weight:800;text-transform:uppercase;color:#166534;margin-bottom:8px;">Feedback captured</div>
      <h1 style="font-size:24px;line-height:1.2;margin:0 0 12px 0;">Thanks</h1>
      <p style="font-size:16px;line-height:1.5;margin:0;color:#374151;">
        Item <strong>#{event["item_number"]}</strong> was marked <strong>{escape(rating_label(event["rating"]))}</strong>.
        You can close this tab.
      </p>
    """


class handler(BaseHTTPRequestHandler):
    def send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        event, error = validate_params(params)

        if error:
            status, body = html_page(
                "Feedback Error",
                f'<h1 style="font-size:24px;margin:0 0 12px 0;">Feedback link problem</h1><p>{escape(error)}</p>',
                status=400,
            )
            self.send_html(status, body)
            return

        status, body = html_page("Confirm Feedback", confirmation_form(event))
        self.send_html(status, body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(raw_body)
        event, error = validate_params(params)

        if error:
            status, body = html_page(
                "Feedback Error",
                f'<h1 style="font-size:24px;margin:0 0 12px 0;">Feedback link problem</h1><p>{escape(error)}</p>',
                status=400,
            )
            self.send_html(status, body)
            return

        try:
            send_feedback_email(event)
        except Exception as exc:
            status, body = html_page(
                "Feedback Error",
                f'<h1 style="font-size:24px;margin:0 0 12px 0;">Could not save feedback</h1><p>{escape(str(exc))}</p>',
                status=500,
            )
            self.send_html(status, body)
            return

        status, body = html_page("Feedback Captured", success_body(event))
        self.send_html(status, body)
