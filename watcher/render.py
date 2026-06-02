"""Render the structured digest payload into human-readable formats.

The agent delivers the digest through the ``submit_digest`` MCP tool as typed
JSON. This module owns every conversion from that payload to something a
human will read: Markdown for the file/email channels, Telegram-HTML for the
chat channel. Keeping renderers separate from the agent means a new channel
costs one function, not a new model call.
"""

from __future__ import annotations

from typing import Any


def render_markdown(payload: dict[str, Any]) -> str:
    """Render the structured digest as French Markdown.

    The payload is trusted (it has already been validated by the tool's
    JSON Schema), but we still tolerate missing optional fields like
    ``source_name`` so a slightly under-filled payload doesn't crash.
    """
    lines: list[str] = [f"# Veille Djohodo — {payload['date']}", ""]

    for holding in payload.get("holdings", []):
        marker = "" if holding.get("verified", True) else " [?]"
        lines.append(f"## {holding['ticker']}{marker} — {holding['name']}")
        items = holding.get("items", [])

        if not items:
            lines.append("")
            lines.append("Aucune actualité matérielle.")
        else:
            for item in items:
                source_name = item.get("source_name") or "source"
                lines.append("")
                lines.append(
                    f"- **{item['title']}** "
                    f"([{source_name}]({item['source_url']}))"
                )
                lines.append(
                    f"  - *Impact :* **{item['impact']}** — {item['rationale']}"
                )

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _esc_html(s: str) -> str:
    """Minimal escape for Telegram's HTML parse mode.

    Telegram only requires ``&``, ``<``, and ``>`` to be escaped in HTML
    mode. Order matters — escape ``&`` first so the entity introducers we
    write below aren't double-escaped.
    """
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _break_autolink(ticker: str) -> str:
    """Defuse Telegram's auto-linkification of ticker symbols.

    Tickers like ``BNKE.PA`` get rendered as clickable links because
    Telegram's heuristic sees ``word.tld`` and ``.PA`` happens to be a
    real ccTLD (Panama). Inserting a zero-width space (U+200B) right
    before the dot breaks the heuristic without changing the visible
    glyph sequence. Works inside ``<b>`` so the bold styling is
    preserved. Copy-paste from the message will carry the ZWSP — fine
    for human reading, just be aware if scraping back out of the chat.
    """
    return ticker.replace(".", "​.")


def render_telegram(payload: dict[str, Any]) -> str:
    """Render the structured digest for Telegram (``parse_mode=HTML``).

    HTML is preferred over MarkdownV2 because the digest content (article
    titles, French punctuation, source URLs) routinely contains characters
    that MarkdownV2 mandates be escaped (``. - ! * _ ~ ( ) [ ] { } > # +
    = | \\``). HTML mode only requires ``&``, ``<``, ``>`` to be escaped —
    much harder to break by accident.

    Supported tags (Telegram Bot API): ``<b>``, ``<i>``, ``<u>``, ``<s>``,
    ``<code>``, ``<pre>``, ``<a href="">``. Anything else is rejected by
    the API, so we stick to ``<b>``, ``<i>``, ``<a>``.
    """
    e = _esc_html
    lines: list[str] = [f"<b>Veille Djohodo — {e(payload['date'])}</b>", ""]

    for holding in payload.get("holdings", []):
        marker = "" if holding.get("verified", True) else " [?]"
        ticker = _break_autolink(e(holding["ticker"]))
        lines.append(f"<b>{ticker}{marker} — {e(holding['name'])}</b>")
        items = holding.get("items", [])

        if not items:
            lines.append("<i>Aucune actualité matérielle.</i>")
        else:
            for item in items:
                source_name = e(item.get("source_name") or "source")
                # URLs go in href="..." — must escape & < > too, since the
                # value lives inside an HTML attribute.
                source_url = e(item["source_url"])
                lines.append("")
                lines.append(f"• <b>{e(item['title'])}</b>")
                lines.append(
                    f"  Impact : <b>{e(item['impact'])}</b> — {e(item['rationale'])}"
                )
                lines.append(
                    f"  Source : <a href=\"{source_url}\">{source_name}</a>"
                )

        lines.append("")  # blank line between holdings

    return "\n".join(lines).rstrip()
