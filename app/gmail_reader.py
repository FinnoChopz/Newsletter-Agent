import base64
import json
import socket
import ssl
import time
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import re

from app.config import get_cheap_model, get_int_env

def extract_email_address(sender: str) -> str:
    """
    Turns:
    'TLDR <dan@tldrnewsletter.com>'
    into:
    'dan@tldrnewsletter.com'
    """
    match = re.search(r"<([^>]+)>", sender)
    if match:
        return match.group(1).strip()

    return sender.strip()


def extract_sender_email(sender: str) -> str:
    value = extract_email_address(sender)
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, re.IGNORECASE)
    return match.group(0).lower() if match else ""


def extract_sender_query_value(sender: str) -> str:
    email = extract_sender_email(sender)
    if email:
        return email

    value = extract_email_address(sender).strip().lower()
    domain = value.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    domain = domain.split("/", 1)[0]
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return domain
    return ""
load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

openai_client = OpenAI()

CLASSIFY_PROMPT = Path("prompts/classify_newsletter_sender.md").read_text(
    encoding="utf-8"
)


def get_gmail_service(
    token_path: str | Path = "token.json",
    credentials_path: str | Path = "credentials.json",
):
    creds = None
    token_path = Path(token_path)
    credentials_path = Path(credentials_path)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            retry_operation(
                "credential refresh",
                lambda: creds.refresh(Request()),
            )
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path),
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def decode_body(data: str) -> str:
    decoded_bytes = base64.urlsafe_b64decode(data.encode("utf-8"))
    return decoded_bytes.decode("utf-8", errors="ignore")


def html_to_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")

    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        label = anchor.get_text(" ", strip=True)

        if not href:
            continue

        if label and href not in label:
            anchor.replace_with(f"{label} [{href}]")
        else:
            anchor.replace_with(href)

    return soup.get_text("\n")


def collect_payload_texts(payload: dict) -> list[tuple[str, str]]:
    body = payload.get("body", {})
    mime_type = payload.get("mimeType", "")
    texts: list[tuple[str, str]] = []

    if body.get("data") and mime_type in ["text/plain", "text/html"]:
        raw = decode_body(body["data"])
        text = html_to_text(raw) if mime_type == "text/html" else raw
        if text.strip():
            texts.append((mime_type, text))

    for part in payload.get("parts", []):
        texts.extend(collect_payload_texts(part))

    return texts


def extract_text_from_payload(payload: dict) -> str:
    texts = collect_payload_texts(payload)

    for mime_type, text in texts:
        if mime_type == "text/html":
            return text

    if texts:
        return texts[0][1]

    return ""


def get_header(headers: list[dict], name: str) -> str:
    return next(
        (h["value"] for h in headers if h["name"].lower() == name.lower()),
        "",
    )


def is_retryable_gmail_error(error: Exception) -> bool:
    if isinstance(
        error,
        (
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            ConnectionError,
            TransportError,
        ),
    ):
        return True

    if isinstance(error, HttpError):
        return error.resp.status in {429, 500, 502, 503, 504}

    return False


def retry_operation(operation: str, action):
    attempts = max(1, get_int_env("FINN_SIGNAL_GMAIL_RETRIES", 5))
    delay_seconds = max(1, get_int_env("FINN_SIGNAL_GMAIL_RETRY_SECONDS", 30))

    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as error:
            if attempt == attempts or not is_retryable_gmail_error(error):
                raise

            print(
                f"Gmail {operation} failed on attempt {attempt}/{attempts}: {error}. "
                f"Retrying in {delay_seconds}s..."
            )
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 300)

    raise RuntimeError(f"Gmail {operation} failed unexpectedly.")


def execute_gmail_request(build_request, operation: str) -> dict:
    return retry_operation(operation, lambda: build_request().execute())


def fetch_recent_emails(
    max_results: int = 100,
    query: str = "newer_than:30d -in:spam -in:trash",
    token_path: str | Path = "token.json",
) -> list[dict]:
    """
    Generic Gmail fetcher.
    It does not decide what is or is not a newsletter.
    """
    service = get_gmail_service(token_path=token_path)

    results = execute_gmail_request(
        lambda: service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ),
        "message list",
    )

    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        full = execute_gmail_request(
            lambda: service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full",
            ),
            f"message fetch {msg['id']}",
        )

        payload = full.get("payload", {})
        headers = payload.get("headers", [])

        subject = get_header(headers, "Subject")
        sender = get_header(headers, "From")
        body = extract_text_from_payload(payload)

        emails.append(
            {
                "id": msg["id"],
                "subject": subject,
                "sender": sender,
                "text": body,
                "headers": {
                    h["name"].lower(): h["value"]
                    for h in headers
                },
            }
        )

    return emails


def is_newsletter_candidate(email: dict) -> bool:
    """
    Cheap candidate detector.

    Goal:
    catch likely newsletter-shaped emails without sending the whole inbox
    to the model.

    This is NOT the final decision.
    The model classifies the candidates afterward.
    """
    sender = email.get("sender", "").lower()
    subject = email.get("subject", "").lower()
    text = email.get("text", "").lower()
    headers = email.get("headers", {})

    header_blob = " ".join(
        f"{k}: {v}".lower()
        for k, v in headers.items()
    )

    strong_header_signals = [
        "list-unsubscribe",
        "list-id",
        "precedence: bulk",
        "precedence: list",
    ]

    newsletter_content_signals = [
        "view online",
        "view in browser",
        "minute read",
        "read more",
        "quick links",
        "top stories",
        "daily digest",
        "weekly digest",
        "newsletter",
        "manage your subscription",
    ]

    obvious_non_newsletter_signals = [
        "verification code",
        "security alert",
        "password reset",
        "login",
        "receipt",
        "order confirmation",
        "your reservation",
        "appointment",
        "invitation",
    ]

    obvious_promo_signals = [
        "sale",
        "% off",
        "discount",
        "coupon",
        "shop now",
        "book now",
        "free shipping",
        "casino",
        "rewards",
        "limited time",
        "offer ends",
    ]

    if subject.startswith("re:") or subject.startswith("fwd:"):
        return False

    if any(signal in subject or signal in text for signal in obvious_non_newsletter_signals):
        return False

    score = 0

    if any(signal in header_blob for signal in strong_header_signals):
        score += 3

    if "unsubscribe" in text or "unsubscribe" in header_blob:
        score += 1

    for signal in newsletter_content_signals:
        if signal in subject or signal in text:
            score += 1

    for signal in obvious_promo_signals:
        if signal in subject or signal in text:
            score -= 1

    # Good newsletter platforms often include these sender patterns.
    if any(platform in sender for platform in ["substack", "beehiiv", "ghost", "convertkit"]):
        score += 2

    return score >= 2


def discover_newsletters(
    days: int = 30,
    max_results: int = 300,
    token_path: str | Path = "token.json",
) -> list[dict]:
    """
    One-time onboarding scan.

    Flow:
    recent non-spam mail
    → cheap candidate detection using headers/body clues
    → grouped by sender
    → model classification later
    """
    emails = fetch_recent_emails(
        max_results=max_results,
        query=f"newer_than:{days}d -in:spam -in:trash",
        token_path=token_path,
    )

    print(f"Fetched {len(emails)} emails for discovery scan.")

    candidates = {}

    for email in emails:
        if not is_newsletter_candidate(email):
            continue

        sender = email.get("sender", "")

        if not sender:
            continue

        if sender not in candidates:
            candidates[sender] = {
                "sender": sender,
                "count": 0,
                "example_subjects": [],
                "snippets": [],
                "header_signals": [],
            }

        candidates[sender]["count"] += 1

        if len(candidates[sender]["example_subjects"]) < 3:
            candidates[sender]["example_subjects"].append(email["subject"])

        if len(candidates[sender]["snippets"]) < 2:
            candidates[sender]["snippets"].append(email["text"][:1000])

        headers = email.get("headers", {})
        useful_headers = [
            "list-unsubscribe",
            "list-id",
            "precedence",
        ]

        for header_name in useful_headers:
            if header_name in headers and header_name not in candidates[sender]["header_signals"]:
                candidates[sender]["header_signals"].append(header_name)

    print(f"Found {len(candidates)} candidate newsletter sender(s).")

    return sorted(
        candidates.values(),
        key=lambda candidate: (
            len(candidate.get("header_signals", [])),
            candidate["count"],
        ),
        reverse=True,
    )


def classify_sender_with_model(candidate: dict) -> dict:
    """
    Uses a cheap model once during onboarding to decide:
    newsletter vs promo vs transactional vs spam etc.
    """
    response = openai_client.responses.create(
        model=get_cheap_model(),
        input=[
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": json.dumps(candidate, indent=2)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "newsletter_sender_classification",
                "schema": {
                    "type": "object",
                    "properties": {
                        "classification": {
                            "type": "string",
                            "enum": [
                                "newsletter",
                                "promotional_marketing",
                                "transactional",
                                "personal",
                                "spam_or_low_value",
                                "unclear",
                            ],
                        },
                        "confidence": {"type": "number"},
                        "suggested_name": {"type": "string"},
                        "reason": {"type": "string"},
                        "should_include": {"type": "boolean"},
                    },
                    "required": [
                        "classification",
                        "confidence",
                        "suggested_name",
                        "reason",
                        "should_include",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)


def build_newsletter_query(
    sources_path: str = "data/newsletter_sources.generated.yaml",
    days: int = 2,
) -> str:
    """
    Builds a Gmail query from approved newsletter sources.

    Daily Finn-Signal should use this instead of scanning the whole inbox.
    """
    data = yaml.safe_load(Path(sources_path).read_text(encoding="utf-8"))

    senders = []
    seen_senders = set()

    for source in data.get("sources", []):
        if not source.get("enabled", True):
            continue
        if source.get("status", "receiving") != "receiving":
            continue

        for sender in source.get("senders", []):
            sender_value = extract_sender_query_value(sender)
            if not sender_value or sender_value in seen_senders:
                continue
            senders.append(sender_value)
            seen_senders.add(sender_value)

    if not senders:
        raise ValueError("No receiving newsletter senders found.")

    sender_query = " OR ".join([f"from:{sender}" for sender in senders])

    return f"newer_than:{days}d -in:spam -in:trash ({sender_query})"


def fetch_recent_newsletters(
    max_results: int = 25,
    days: int = 2,
    sources_path: str = "data/newsletter_sources.generated.yaml",
    token_path: str | Path = "token.json",
) -> list[dict]:
    """
    Fetch only approved newsletter emails.
    """
    query = build_newsletter_query(
        sources_path=sources_path,
        days=days,
    )

    return fetch_recent_emails(
        max_results=max_results,
        query=query,
        token_path=token_path,
    )
