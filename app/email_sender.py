import base64
from email.mime.text import MIMEText

from app.gmail_reader import get_gmail_service


def send_email(to: str, subject: str, body: str, html: bool = False, cc: str | None = None, bcc: str | None = None):
    service = get_gmail_service()

    subtype = "html" if html else "plain"

    message = MIMEText(body, subtype, "utf-8")
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc

    if bcc:
        message["bcc"] = bcc

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    result = service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    return result
