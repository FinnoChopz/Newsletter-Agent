import os


def get_main_model() -> str:
    return os.getenv("OPENAI_MAIN_MODEL", "gpt-5.5")


def get_cheap_model() -> str:
    return os.getenv("OPENAI_CHEAP_MODEL", "gpt-5.4-mini")


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def parse_email_list(value: str | None) -> list[str]:
    if not value:
        return []

    return [email.strip() for email in value.split(",") if email.strip()]


def get_recipients() -> list[str]:
    return parse_email_list(os.getenv("FINN_SIGNAL_RECIPIENTS")) or [
        "fmccooe@gmail.com"
    ]


def get_bcc_recipients() -> list[str]:
    return parse_email_list(os.getenv("FINN_SIGNAL_BCC")) or [
        "amccooe@gmail.com"
    ]


def get_feedback_email() -> str:
    configured = os.getenv("FINN_SIGNAL_FEEDBACK_EMAIL")
    if configured:
        return configured

    return get_recipients()[0]


def get_feedback_base_url() -> str:
    return os.getenv("FINN_SIGNAL_FEEDBACK_BASE_URL", "").strip().rstrip("/")
