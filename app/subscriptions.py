from __future__ import annotations

import ipaddress
import os
import socket
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


AGENT_VERSION = "subscription_agent_v2"
MAX_STATIC_PAGES = 5
MAX_SUBSCRIBE_LINKS = 4

CAPTCHA_TERMS = [
    "captcha",
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "cloudflare turnstile",
    "verify you are human",
    "human verification",
    "checking your browser",
    "security check",
]

SUBSCRIBE_TERMS = [
    "newsletter",
    "subscribe",
    "subscription",
    "sign up",
    "signup",
    "join",
    "mailing list",
    "email updates",
    "get updates",
]

BAD_FORM_TERMS = [
    "log in",
    "login",
    "sign in",
    "signin",
    "password",
    "search",
    "unsubscribe",
    "comment",
    "contact us",
    "checkout",
    "payment",
    "donation",
]

SUCCESS_TERMS = [
    "already subscribed",
    "check your email",
    "confirm your email",
    "confirmation email",
    "thanks for subscribing",
    "thank you for subscribing",
    "you're subscribed",
    "you are subscribed",
    "welcome",
    "subscription confirmed",
    "successfully subscribed",
]

DEFAULT_HEADERS = {
    "User-Agent": "Finn-Signal subscription assistant/2.0 (+https://newsletter-agent-s96n.onrender.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class PreparedSubscription:
    method: str
    url: str
    data: dict[str, str]
    score: int = 0
    field_name: str = ""


def page_has_captcha(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in CAPTCHA_TERMS)


def env_truthy(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def tag_text(tag) -> str:
    if not tag:
        return ""
    return normalized_text(tag.get_text(" ", strip=True))


def attr_text(tag, *attrs: str) -> str:
    return " ".join(normalized_text(tag.get(attr) or "") for attr in attrs)


def is_private_hostname(hostname: str) -> bool:
    host = hostname.strip().lower().strip("[]")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".localhost") or host.endswith(".local"):
        return True

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False

    return any(
        [
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        ]
    )


def validate_subscription_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Subscription URL must be an http or https URL."
    if parsed.username or parsed.password:
        return "Subscription URL cannot include embedded credentials."
    if not parsed.hostname or is_private_hostname(parsed.hostname):
        return "Subscription URL points to a private or local host."

    if env_truthy("FINN_SIGNAL_SUBSCRIPTION_RESOLVE_HOSTS", True):
        try:
            resolved = socket.getaddrinfo(parsed.hostname, parsed.port or None, proto=socket.IPPROTO_TCP)
        except OSError:
            return "Subscription host could not be resolved."
        for result in resolved:
            address = result[4][0]
            if is_private_hostname(address):
                return "Subscription host resolves to a private or local address."

    return ""


def input_name(input_tag) -> str:
    return str(input_tag.get("name") or input_tag.get("id") or "").strip()


def input_value(input_tag) -> str:
    return str(input_tag.get("value") or "").strip()


def labels_for_field(soup: BeautifulSoup, input_tag) -> str:
    labels = []
    field_id = str(input_tag.get("id") or "")
    if field_id:
        for label in soup.find_all("label", attrs={"for": field_id}):
            labels.append(tag_text(label))
    parent_label = input_tag.find_parent("label")
    if parent_label:
        labels.append(tag_text(parent_label))
    return " ".join(labels)


def field_context(soup: BeautifulSoup, field) -> str:
    return " ".join(
        [
            attr_text(field, "name", "id", "placeholder", "aria-label", "autocomplete"),
            labels_for_field(soup, field),
        ]
    ).lower()


def looks_like_email_field(input_tag, soup: BeautifulSoup | None = None) -> bool:
    field_type = str(input_tag.get("type") or "text").lower()
    context = attr_text(input_tag, "name", "id", "placeholder", "aria-label", "autocomplete").lower()
    if soup is not None:
        context = f"{context} {labels_for_field(soup, input_tag).lower()}"
    return field_type == "email" or "email" in context or "e-mail" in context


def form_context(soup: BeautifulSoup, form) -> str:
    return " ".join(
        [
            tag_text(form),
            attr_text(form, "id", "name", "class", "action", "aria-label"),
            " ".join(field_context(soup, field) for field in form.find_all(["input", "select", "textarea"])),
        ]
    ).lower()


def form_score(soup: BeautifulSoup, form) -> int:
    fields = form.find_all(["input", "select", "textarea"])
    if any(str(field.get("type") or "").lower() == "password" for field in fields):
        return -100

    email_fields = [field for field in fields if field.name == "input" and looks_like_email_field(field, soup)]
    if not email_fields:
        return -100

    context = form_context(soup, form)
    score = 40
    if any(str(field.get("type") or "").lower() == "email" for field in email_fields):
        score += 25
    score += sum(10 for term in SUBSCRIBE_TERMS if term in context)
    score -= sum(25 for term in BAD_FORM_TERMS if term in context)

    action = str(form.get("action") or "").lower()
    if any(term.replace(" ", "") in action.replace("-", "").replace("_", "") for term in ["subscribe", "newsletter", "signup"]):
        score += 20
    if any(term in action for term in ["unsubscribe", "login", "search", "checkout"]):
        score -= 50
    return score


def default_required_value(context: str) -> str:
    if "first" in context and "name" in context:
        return "Finn"
    if "last" in context and "name" in context:
        return "Signal"
    if "full" in context and "name" in context:
        return "Finn Signal"
    if "name" in context or "company" in context or "organization" in context:
        return "Finn-Signal"
    return ""


def should_include_checkbox(field, context: str) -> bool:
    if field.has_attr("checked") or field.has_attr("required"):
        return True
    return any(term in context for term in ["consent", "agree", "terms", "privacy", "subscribe", "newsletter"])


def form_data_for_submission(
    soup: BeautifulSoup,
    form,
    subscription_email: str,
) -> tuple[dict[str, str], str]:
    fields = form.find_all(["input", "select", "textarea"])
    data: dict[str, str] = {}
    email_field_name = ""

    for field in fields:
        if field.has_attr("disabled"):
            continue
        name = input_name(field)
        if not name:
            continue

        field_type = str(field.get("type") or "text").lower()
        context = field_context(soup, field)
        if field.name == "select":
            selected = field.find("option", selected=True) or field.find("option")
            data[name] = input_value(selected) if selected else ""
        elif field.name == "textarea":
            data[name] = str(field.text or "")
        elif looks_like_email_field(field, soup):
            data[name] = subscription_email
            email_field_name = name
        elif field_type in {"submit", "button", "image", "file", "reset", "password"}:
            continue
        elif field_type in {"checkbox", "radio"}:
            if should_include_checkbox(field, context):
                data[name] = input_value(field) or "on"
        elif field_type == "hidden":
            data[name] = input_value(field)
        else:
            current_value = input_value(field)
            data[name] = current_value or default_required_value(context)

    return data, email_field_name


def prepare_subscription_submission(
    page_url: str,
    html: str,
    subscription_email: str,
) -> PreparedSubscription | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for form in soup.find_all("form"):
        score = form_score(soup, form)
        if score < 20:
            continue
        data, email_field_name = form_data_for_submission(soup, form, subscription_email)
        if not email_field_name:
            continue
        action = str(form.get("action") or page_url)
        method = str(form.get("method") or "get").lower()
        candidates.append(
            PreparedSubscription(
                method="post" if method == "post" else "get",
                url=urljoin(page_url, action),
                data=data,
                score=score,
                field_name=email_field_name,
            )
        )

    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.score, reverse=True)[0]


def subscribe_link_score(page_url: str, tag) -> int:
    href = str(tag.get("href") or "").strip()
    if not href:
        return -100
    url = urljoin(page_url, href)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return -100

    text = " ".join([tag_text(tag), href, attr_text(tag, "aria-label", "title", "class", "id")]).lower()
    if any(term in text for term in ["unsubscribe", "login", "signin", "sign-in", "privacy", "terms"]):
        return -100

    score = sum(20 for term in SUBSCRIBE_TERMS if term in text)
    path = parsed.path.lower()
    if any(term in path for term in ["newsletter", "subscribe", "signup", "sign-up", "join"]):
        score += 20
    if parsed.netloc == urlparse(page_url).netloc:
        score += 5
    return score


def discover_subscription_links(page_url: str, html: str, max_links: int = MAX_SUBSCRIBE_LINKS) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    scored = []
    seen = set()
    for link in soup.find_all("a", href=True):
        url = urljoin(page_url, str(link.get("href") or "").strip())
        if url in seen:
            continue
        seen.add(url)
        score = subscribe_link_score(page_url, link)
        if score > 0:
            scored.append((score, url))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in scored[:max_links]]


def response_text(response) -> tuple[str, str]:
    html = response.read().decode("utf-8", errors="ignore")
    page_url = response.geturl()
    return html, page_url


def submit_prepared_subscription(prepared: PreparedSubscription, timeout_seconds: int) -> tuple[str, str]:
    encoded = urlencode(prepared.data).encode("utf-8")
    request_url = prepared.url
    request_data = encoded if prepared.method == "post" else None
    if prepared.method == "get":
        separator = "&" if "?" in request_url else "?"
        request_url = f"{request_url}{separator}{encoded.decode('utf-8')}"

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
        return response_text(response)


def result_has_success_signal(html: str) -> bool:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    return any(term in text for term in SUCCESS_TERMS)


def static_subscription_attempt(
    subscription_url: str,
    subscription_email: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    queue = [subscription_url]
    visited: set[str] = set()
    steps = []
    last_error = ""

    while queue and len(visited) < MAX_STATIC_PAGES:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        steps.append({"action": "fetch", "url": page_url})

        try:
            page_request = Request(page_url, headers=DEFAULT_HEADERS)
            with urlopen(page_request, timeout=timeout_seconds) as response:
                html, resolved_url = response_text(response)
        except Exception as exc:
            last_error = str(exc)
            steps.append({"action": "fetch_failed", "url": page_url, "error": last_error})
            continue

        if page_has_captcha(html):
            return {
                "status": "manual_required",
                "reason": "The subscription page appears to require a human verification step.",
                "method": "http_form",
                "agent_version": AGENT_VERSION,
                "steps": steps,
                "final_url": resolved_url,
            }

        prepared = prepare_subscription_submission(resolved_url, html, subscription_email)
        if prepared:
            steps.append(
                {
                    "action": "submit_form",
                    "url": prepared.url,
                    "method": prepared.method,
                    "field": prepared.field_name,
                    "score": prepared.score,
                }
            )
            try:
                result_html, final_url = submit_prepared_subscription(prepared, timeout_seconds)
            except Exception as exc:
                return {
                    "status": "manual_required",
                    "reason": f"The signup form could not be submitted automatically: {exc}",
                    "method": "http_form",
                    "agent_version": AGENT_VERSION,
                    "form_url": prepared.url,
                    "steps": steps,
                }

            if page_has_captcha(result_html):
                return {
                    "status": "manual_required",
                    "reason": "The signup flow hit a human verification step after submission.",
                    "method": "http_form",
                    "agent_version": AGENT_VERSION,
                    "form_url": prepared.url,
                    "final_url": final_url,
                    "steps": steps,
                }

            return {
                "status": "submitted",
                "reason": (
                    "Signup form submitted and the page showed a success/confirmation signal."
                    if result_has_success_signal(result_html)
                    else "Signup form submitted. Check Gmail for a welcome or confirmation email."
                ),
                "method": "http_form",
                "agent_version": AGENT_VERSION,
                "form_url": prepared.url,
                "final_url": final_url,
                "steps": steps,
            }

        for link in discover_subscription_links(resolved_url, html):
            if link not in visited and link not in queue:
                queue.append(link)
                steps.append({"action": "queue_link", "url": link})

    return {
        "status": "manual_required",
        "reason": (
            "No standard email signup form was found on the subscription page."
            if not last_error
            else f"Could not complete the static subscription pass: {last_error}"
        ),
        "method": "http_form",
        "agent_version": AGENT_VERSION,
        "steps": steps,
    }


def browser_click_entrypoint(frame) -> dict[str, Any]:
    return frame.evaluate(
        """
        () => {
          const terms = ['newsletter', 'subscribe', 'subscription', 'sign up', 'signup', 'join', 'email updates', 'get updates'];
          const bad = ['unsubscribe', 'login', 'log in', 'signin', 'sign in'];
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const textFor = (el) => [
            el.innerText || el.textContent || '',
            el.getAttribute('href') || '',
            el.getAttribute('aria-label') || '',
            el.getAttribute('title') || '',
            el.id || '',
            el.className || ''
          ].join(' ').toLowerCase();
          const candidates = [...document.querySelectorAll('a,button,[role=button]')]
            .filter(visible)
            .map((el) => {
              const text = textFor(el);
              if (bad.some((term) => text.includes(term))) return null;
              const score = terms.reduce((sum, term) => sum + (text.includes(term) ? 1 : 0), 0);
              return score ? { el, score, text: text.slice(0, 120) } : null;
            })
            .filter(Boolean)
            .sort((a, b) => b.score - a.score);
          if (!candidates.length) return { clicked: false };
          candidates[0].el.click();
          return { clicked: true, text: candidates[0].text };
        }
        """
    )


def browser_submit_frame(frame, subscription_email: str) -> dict[str, Any]:
    return frame.evaluate(
        """
        (email) => {
          const subscribeTerms = ['newsletter', 'subscribe', 'subscription', 'sign up', 'signup', 'join', 'mailing list', 'email updates'];
          const badTerms = ['unsubscribe', 'login', 'log in', 'signin', 'sign in', 'password', 'search', 'checkout', 'payment'];
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const fire = (el) => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          };
          const context = (el) => [
            el.name || '',
            el.id || '',
            el.placeholder || '',
            el.getAttribute('aria-label') || '',
            el.getAttribute('autocomplete') || '',
            el.closest('form')?.innerText || ''
          ].join(' ').toLowerCase();
          const scoreForm = (form) => {
            const text = (form?.innerText || '').toLowerCase();
            const attrs = [form?.id || '', form?.className || '', form?.getAttribute('action') || ''].join(' ').toLowerCase();
            const merged = `${text} ${attrs}`;
            let score = 0;
            subscribeTerms.forEach((term) => { if (merged.includes(term)) score += 10; });
            badTerms.forEach((term) => { if (merged.includes(term)) score -= 25; });
            if (form?.querySelector('input[type=password]')) score -= 100;
            return score;
          };
          const emailInputs = [...document.querySelectorAll('input')]
            .filter((input) => !input.disabled && visible(input))
            .filter((input) => {
              const type = (input.getAttribute('type') || 'text').toLowerCase();
              const text = context(input);
              return type === 'email' || text.includes('email') || text.includes('e-mail');
            })
            .map((input) => ({ input, score: scoreForm(input.closest('form')) + ((input.type || '').toLowerCase() === 'email' ? 30 : 0) }))
            .filter((item) => item.score > -20)
            .sort((a, b) => b.score - a.score);
          if (!emailInputs.length) return { submitted: false };

          const input = emailInputs[0].input;
          const form = input.closest('form');
          input.focus();
          input.value = email;
          fire(input);
          const scope = form || document;

          [...scope.querySelectorAll('input[type=text],input:not([type]),input[type=search]')].forEach((field) => {
            if (field === input || field.disabled || field.value || !field.required) return;
            const text = context(field);
            if (text.includes('first') && text.includes('name')) field.value = 'Finn';
            else if (text.includes('last') && text.includes('name')) field.value = 'Signal';
            else if (text.includes('name') || text.includes('company') || text.includes('organization')) field.value = 'Finn-Signal';
            if (field.value) fire(field);
          });
          [...scope.querySelectorAll('input[type=checkbox]')].forEach((box) => {
            const text = context(box);
            if (box.required || ['consent', 'agree', 'terms', 'privacy', 'subscribe', 'newsletter'].some((term) => text.includes(term))) {
              box.checked = true;
              fire(box);
            }
          });

          const buttons = [...scope.querySelectorAll('button,input[type=submit],[role=button]')]
            .filter((button) => !button.disabled && visible(button))
            .map((button) => {
              const text = [button.innerText || button.value || '', button.getAttribute('aria-label') || '', button.id || '', button.className || ''].join(' ').toLowerCase();
              const score = subscribeTerms.reduce((sum, term) => sum + (text.includes(term) ? 1 : 0), 0);
              return { button, score, text: text.slice(0, 120) };
            })
            .sort((a, b) => b.score - a.score);
          if (form && form.requestSubmit) {
            const submitter = buttons[0]?.button || null;
            try {
              form.requestSubmit(submitter);
              return { submitted: true, via: 'requestSubmit', field: input.name || input.id || 'email', button: buttons[0]?.text || '' };
            } catch (error) {}
          }
          if (buttons.length) {
            buttons[0].button.click();
            return { submitted: true, via: 'button', field: input.name || input.id || 'email', button: buttons[0].text };
          }
          input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
          input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
          return { submitted: true, via: 'enter', field: input.name || input.id || 'email' };
        }
        """,
        subscription_email,
    )


def browser_page_text(page) -> str:
    with suppress(Exception):
        return page.locator("body").inner_text(timeout=2000)
    with suppress(Exception):
        return page.content(timeout=2000)
    return ""


def browser_has_human_verification(page) -> bool:
    if page_has_captcha(browser_page_text(page)):
        return True
    for frame in page.frames:
        frame_url = (frame.url or "").lower()
        if any(term in frame_url for term in ["recaptcha", "hcaptcha", "turnstile", "captcha"]):
            return True
    return False


def browser_subscription_attempt(
    subscription_url: str,
    subscription_email: str,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    if not env_truthy("FINN_SIGNAL_SUBSCRIPTION_BROWSER", True):
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "status": "manual_required",
            "reason": "Browser signup agent is not installed in this runtime.",
            "method": "browser_agent",
            "agent_version": AGENT_VERSION,
            "error": str(exc),
        }

    steps = []
    timeout_ms = max(5000, min(timeout_seconds * 1000, 30000))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                viewport={"width": 1365, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            steps.append({"action": "browser_goto", "url": subscription_url})
            page.goto(subscription_url, wait_until="domcontentloaded", timeout=timeout_ms)
            with suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=5000)

            for attempt in range(4):
                if browser_has_human_verification(page):
                    browser.close()
                    return {
                        "status": "manual_required",
                        "reason": "The signup flow requires human verification.",
                        "method": "browser_agent",
                        "agent_version": AGENT_VERSION,
                        "final_url": page.url,
                        "steps": steps,
                    }

                for frame in page.frames:
                    with suppress(Exception):
                        result = browser_submit_frame(frame, subscription_email)
                        if result.get("submitted"):
                            steps.append({"action": "browser_submit", "frame": frame.url, **result})
                            with suppress(Exception):
                                page.wait_for_load_state("networkidle", timeout=7000)
                            text = browser_page_text(page).lower()
                            final_url = page.url
                            browser.close()
                            return {
                                "status": "submitted",
                                "reason": (
                                    "Signup submitted in the browser and the page showed a success/confirmation signal."
                                    if any(term in text for term in SUCCESS_TERMS)
                                    else "Signup submitted in the browser. Check Gmail for a welcome or confirmation email."
                                ),
                                "method": "browser_agent",
                                "agent_version": AGENT_VERSION,
                                "final_url": final_url,
                                "steps": steps,
                            }

                clicked = False
                for frame in page.frames:
                    with suppress(Exception):
                        click_result = browser_click_entrypoint(frame)
                        if click_result.get("clicked"):
                            steps.append({"action": "browser_click_entrypoint", "frame": frame.url, **click_result})
                            clicked = True
                            with suppress(Exception):
                                page.wait_for_load_state("networkidle", timeout=7000)
                            break
                if not clicked:
                    break

            final_url = page.url
            browser.close()
            return {
                "status": "manual_required",
                "reason": "Browser agent could not find a safe newsletter email signup control.",
                "method": "browser_agent",
                "agent_version": AGENT_VERSION,
                "final_url": final_url,
                "steps": steps,
            }
    except Exception as exc:
        return {
            "status": "manual_required",
            "reason": f"Browser signup agent could not complete the flow: {exc}",
            "method": "browser_agent",
            "agent_version": AGENT_VERSION,
            "steps": steps,
        }


def attempt_newsletter_subscription(
    subscription_url: str,
    subscription_email: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    if not subscription_url:
        return {
            "status": "manual_required",
            "reason": "No subscription URL was found for this recommendation.",
            "agent_version": AGENT_VERSION,
        }
    if not subscription_email or "@" not in subscription_email:
        return {
            "status": "manual_required",
            "reason": "No valid subscription email is configured for this profile.",
            "agent_version": AGENT_VERSION,
        }

    url_error = validate_subscription_url(subscription_url)
    if url_error:
        return {
            "status": "manual_required",
            "reason": url_error,
            "agent_version": AGENT_VERSION,
        }

    static_result = static_subscription_attempt(subscription_url, subscription_email, timeout_seconds)
    if static_result.get("status") == "submitted":
        return static_result
    if "human verification" in str(static_result.get("reason", "")).lower():
        return static_result

    browser_result = browser_subscription_attempt(subscription_url, subscription_email, timeout_seconds)
    if browser_result and browser_result.get("status") == "submitted":
        return browser_result
    if browser_result:
        return {
            **browser_result,
            "static_result": static_result,
        }
    return static_result
