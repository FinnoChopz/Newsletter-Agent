from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from app.ranking import item_source, to_score


def format_score(item: dict[str, Any]) -> str:
    scores = item.get("scores") or {}
    final_score = to_score(scores.get("final_score"))
    personal = to_score(scores.get("finn_relevance"))
    world = to_score(scores.get("global_importance"))

    return f"{final_score:.1f} overall | Personal {personal:.1f} | World {world:.1f}"


def item_url(item: dict[str, Any]) -> str:
    url = str(item.get("url") or item.get("link") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    return ""


def mailto_link(
    feedback_email: str,
    digest_id: str,
    item_number: int,
    rating: int,
) -> str:
    subject = quote(f"Re: Finn-Signal - {digest_id}")
    body = quote(f"{item_number}:{rating}\n\nOptional note:")

    recipient = quote(feedback_email, safe="@,;")
    return f"mailto:{recipient}?subject={subject}&body={body}"


def feedback_link(
    feedback_email: str,
    digest_id: str,
    item_number: int,
    rating: int,
    feedback_base_url: str | None = None,
) -> str:
    if not feedback_base_url:
        return mailto_link(feedback_email, digest_id, item_number, rating)

    params = {
        "digest_id": digest_id,
        "item": str(item_number),
        "rating": str(rating),
    }

    return f"{feedback_base_url.rstrip('/')}/feedback?{urlencode(params)}"


def review_digest_link(feedback_base_url: str | None, digest_id: str) -> str:
    if not feedback_base_url:
        return ""
    return f"{feedback_base_url.rstrip('/')}/feedback?{urlencode({'digest_id': digest_id})}"


def action_button(
    label: str,
    href: str,
    background: str,
    color: str = "#ffffff",
) -> str:
    return (
        f'<a href="{href}" '
        'style="display:inline-block;text-decoration:none;'
        f'background:{background};color:{color};'
        'font-size:13px;font-weight:700;line-height:1;'
        'padding:10px 12px;border-radius:8px;margin:8px 8px 0 0;">'
        f"{escape(label)}</a>"
    )


def render_item_card(
    item: dict[str, Any],
    digest_id: str,
    feedback_email: str,
    feedback_base_url: str | None = None,
    user_name: str = "you",
    accent: str = "#111827",
) -> str:
    number = int(item.get("item_number") or 0)
    title = escape(str(item.get("title") or "Untitled"))
    source = escape(item_source(item))
    summary = escape(str(item.get("summary") or ""))
    why_user = escape(str(item.get("why_finn_cares") or ""))
    why_world = escape(str(item.get("why_world_cares") or ""))
    score = escape(format_score(item))
    user_label = escape(user_name.strip() or "you")
    url = item_url(item)

    href_more = feedback_link(
        feedback_email,
        digest_id,
        number,
        5,
        feedback_base_url=feedback_base_url,
    )
    href_less = feedback_link(
        feedback_email,
        digest_id,
        number,
        1,
        feedback_base_url=feedback_base_url,
    )

    title_html = (
        f'<a href="{escape(url)}" style="color:#111827;text-decoration:none;">{title}</a>'
        if url
        else title
    )
    read_button = (
        action_button("Read full piece", url, "#1d4ed8")
        if url
        else ""
    )

    return f"""
      <tr>
        <td style="padding:16px 0;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#ffffff;border:1px solid #d8dee8;border-radius:8px;">
            <tr>
              <td style="padding:18px 18px 16px 18px;">
                <div style="font-size:12px;line-height:1.4;font-weight:800;letter-spacing:0;text-transform:uppercase;color:{accent};">#{number} &middot; {source}</div>
                <h2 style="font-family:Arial,Helvetica,sans-serif;font-size:22px;line-height:1.2;margin:8px 0 10px 0;color:#111827;">{title_html}</h2>
                <p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;margin:0 0 12px 0;color:#1f2937;">{summary}</p>
                <p style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;margin:0 0 8px 0;color:#374151;"><strong>Why {user_label} cares:</strong> {why_user}</p>
                <p style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;margin:0 0 12px 0;color:#374151;"><strong>Why the world cares:</strong> {why_world}</p>
                <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.4;color:#64748b;">{score}</div>
                <div>
                  {read_button}
                  {action_button("More like this", href_more, "#166534")}
                  {action_button("Less like this", href_less, "#7f1d1d")}
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_skipped_item(
    item: dict[str, Any],
    digest_id: str,
    feedback_email: str,
    feedback_base_url: str | None = None,
) -> str:
    number = int(item.get("item_number") or 0)
    title = escape(str(item.get("title") or "Untitled"))
    source = escape(item_source(item))
    summary = escape(str(item.get("summary") or ""))
    url = item_url(item)
    href_more = feedback_link(
        feedback_email,
        digest_id,
        number,
        5,
        feedback_base_url=feedback_base_url,
    )
    href_less = feedback_link(
        feedback_email,
        digest_id,
        number,
        1,
        feedback_base_url=feedback_base_url,
    )

    title_html = (
        f'<a href="{escape(url)}" style="color:#1f2937;text-decoration:none;">{title}</a>'
        if url
        else title
    )
    read_link = (
        f'<a href="{escape(url)}" style="color:#1d4ed8;font-weight:700;text-decoration:none;">Read full piece</a>'
        if url
        else ""
    )
    separator = '<span style="color:#cbd5e1;"> &middot; </span>' if read_link else ""

    return f"""
      <tr>
        <td style="padding:10px 0;border-top:1px solid #e5e7eb;">
          <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.45;color:#1f2937;">
            <strong>#{number} &middot; {title_html}</strong>
            <span style="color:#64748b;"> &middot; {source}</span><br>
            <span>{summary}</span><br>
            {read_link}
            {separator}
            <a href="{href_more}" style="color:#166534;font-weight:700;text-decoration:none;">More like this</a>
            <span style="color:#cbd5e1;"> &middot; </span>
            <a href="{href_less}" style="color:#7f1d1d;font-weight:700;text-decoration:none;">Less like this</a>
          </div>
        </td>
      </tr>
    """


def render_html_digest(
    ranked_data: dict[str, Any],
    digest_id: str,
    feedback_email: str,
    feedback_base_url: str | None = None,
    user_name: str = "you",
) -> str:
    sections = ranked_data.get("digest_sections") or {}
    top_signals = sections.get("top_signals") or []
    strange = sections.get("strange_attractor")
    skipped = sections.get("skipped_but_noted") or []
    review_link = review_digest_link(feedback_base_url, digest_id)

    top_html = "\n".join(
        render_item_card(
            item,
            digest_id,
            feedback_email,
            feedback_base_url=feedback_base_url,
            user_name=user_name,
        )
        for item in top_signals
    )

    strange_html = (
        render_item_card(
            strange,
            digest_id,
            feedback_email,
            feedback_base_url=feedback_base_url,
            user_name=user_name,
            accent="#7c2d12",
        )
        if strange
        else '<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#64748b;padding:8px 0 18px 0;">No strong strange-attractor candidate today.</td></tr>'
    )

    skipped_html = "\n".join(
        render_skipped_item(
            item,
            digest_id,
            feedback_email,
            feedback_base_url=feedback_base_url,
        )
        for item in skipped
    )

    if not top_signals:
        top_html = '<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#374151;padding:8px 0 18px 0;">No item cleared the main signal bar today.</td></tr>'

    if not skipped:
        skipped_html = '<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#64748b;padding:8px 0 18px 0;">Nothing else was worth noting.</td></tr>'

    review_button = (
        action_button("Rate + chat about this digest", review_link, "#111827")
        if review_link
        else ""
    )

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f3f5f8;">
    <center style="width:100%;background:#f3f5f8;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#f3f5f8;">
        <tr>
          <td align="center" style="padding:28px 12px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;max-width:720px;">
              <tr>
                <td style="padding:24px 22px;background:#111827;border-radius:8px;">
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:800;letter-spacing:0;text-transform:uppercase;color:#93c5fd;">FINN-SIGNAL</div>
                  <h1 style="font-family:Arial,Helvetica,sans-serif;font-size:30px;line-height:1.15;margin:8px 0;color:#ffffff;">Daily Signal - {escape(digest_id)}</h1>
                  <p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;margin:0;color:#d1d5db;">Ranked by personal relevance, global importance, novelty, actionability, source quality, and learned preferences.</p>
                  <div style="margin-top:14px;">{review_button}</div>
                </td>
              </tr>

              <tr>
                <td style="padding:28px 0 0 0;">
                  <h2 style="font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.2;margin:0;color:#111827;">Top Signals</h2>
                </td>
              </tr>
              {top_html}

              <tr>
                <td style="padding:12px 0 0 0;">
                  <h2 style="font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.2;margin:0;color:#111827;">Strange Attractor</h2>
                </td>
              </tr>
              {strange_html}

              <tr>
                <td style="padding:12px 0 0 0;">
                  <h2 style="font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.2;margin:0;color:#111827;">Skipped but Noted</h2>
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin-top:10px;">
                    {skipped_html}
                  </table>
                </td>
              </tr>

              <tr>
                <td style="padding:24px 18px;margin-top:20px;background:#e8eef7;border:1px solid #cbd5e1;border-radius:8px;">
                  <h2 style="font-family:Arial,Helvetica,sans-serif;font-size:17px;line-height:1.2;margin:0 0 8px 0;color:#111827;">Feedback</h2>
                  <p style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;margin:0 0 8px 0;color:#374151;">Click <strong>Rate + chat about this digest</strong> to rank every article with Like / Not Like / 1-5 controls and ask the article assistant questions.</p>
                  <p style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;margin:0;color:#374151;">Natural language works too: <strong>More AI infra, less routine market noise.</strong></p>
                  <div style="margin-top:10px;">{review_button}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </center>
  </body>
</html>"""
