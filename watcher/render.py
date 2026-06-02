"""Render the structured digest payload into human-readable formats.

The agent delivers the digest through the ``submit_digest`` MCP tool as typed
JSON. This module owns every conversion from that payload to something a
human will read: Markdown for the file/email channels, Telegram-HTML for the
chat channel. Keeping renderers separate from the agent means a new channel
costs one function, not a new model call.
"""

from __future__ import annotations

from typing import Any

from watcher.resolver import display_ticker, exchange_label


# --- Variation formatting (shared by both renderers) -----------------------


def _format_variation(variation: dict[str, Any] | None) -> str | None:
    """Format a snapshot variation as a single line ready to insert.

    Returns ``None`` if there's nothing meaningful to show (no variation
    record at all). For new positions (no prior snapshot) we emit a
    "🆕 nouveau" marker so the user can tell apart "no change" from
    "first appearance".

    Examples:
        "🟢 +2,45 %   (+125 €)"
        "🔴 −1,80 %   (−92 €)"
        "🆕 nouveau"
    """
    if variation is None:
        return None
    if variation.get("is_new"):
        return "🆕 nouveau"

    pct = variation.get("pct")
    abs_eur = variation.get("abs_eur")
    if not isinstance(pct, (int, float)) or not isinstance(abs_eur, (int, float)):
        return None

    # Real Unicode minus (U+2212) for visual symmetry with "+", as in
    # the user's example. Standard French formatting: comma decimal,
    # space before unit, no thousands separator below 10k for readability.
    emoji = "🟢" if abs_eur >= 0 else "🔴"
    sign = "+" if abs_eur >= 0 else "−"

    pct_str = f"{sign}{abs(pct):.2f} %".replace(".", ",")

    abs_int = int(round(abs(abs_eur)))
    # French thousands separator: non-breaking space.
    abs_grouped = f"{abs_int:,}".replace(",", " ")
    abs_str = f"({sign}{abs_grouped} €)"

    # Three spaces between % and (€) to echo the user's spec.
    return f"{emoji} {pct_str}   {abs_str}"


def render_markdown(payload: dict[str, Any]) -> str:
    """Render the structured digest as French Markdown.

    Each holding is wrapped in its own fenced code block (```…```) so
    every position is visually isolated. Inline Markdown is *not* parsed
    inside a code fence, so the ticker is plain text (no bold) and source
    URLs are bare strings (clickable in most viewers as plain URLs).
    """
    lines: list[str] = [f"# {payload['date']}", ""]

    for holding in payload.get("holdings", []):
        marker = "" if holding.get("verified", True) else " [?]"
        exchange_code = holding.get("exchange")
        shown_ticker = display_ticker(holding["ticker"], exchange_code)

        lines.append("```")
        lines.append(f"{shown_ticker}{marker}")
        lines.append(holding["name"])
        label = exchange_label(exchange_code)
        if label:
            lines.append(label)

        variation_line = _format_variation(holding.get("variation"))
        if variation_line:
            lines.append("")
            lines.append(variation_line)

        items = holding.get("items", [])
        lines.append("")
        if not items:
            lines.append("Aucune actualité matérielle.")
        else:
            for i, item in enumerate(items):
                if i:
                    lines.append("")
                source_name = item.get("source_name") or "source"
                lines.append(f"- {item['title']}")
                lines.append(
                    f"  Impact : {item['impact']} — {item['rationale']}"
                )
                lines.append(f"  Source : {source_name} — {item['source_url']}")

        lines.append("```")
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
    glyph sequence. Used for *unverified* tickers, where we don't want
    Telegram to invent a link to nowhere.
    """
    return ticker.replace(".", "​.")


def _yahoo_quote_url(ticker: str) -> str:
    """URL of the Yahoo Finance quote page for this instrument.

    Yahoo's path accepts dots, hyphens, and uppercase letters raw — the
    tickers we hand in (already Yahoo-normalised by ``watcher.resolver``)
    don't need URL escaping.
    """
    return f"https://finance.yahoo.com/quote/{ticker}"


def render_telegram(payload: dict[str, Any]) -> str:
    """Render the structured digest for Telegram (``parse_mode=HTML``).

    Each holding is wrapped in its own ``<blockquote>`` so positions are
    visually isolated by Telegram's vertical accent bar on the left
    (introduced with Bot API 7.0). Inline HTML still works inside —
    ``<b>``, ``<i>``, ``<a>`` are all preserved, so we keep the
    Yahoo-quote-page link on verified tickers.

    HTML mode is preferred over MarkdownV2 because the digest content
    (article titles, French punctuation, source URLs) routinely contains
    characters that MarkdownV2 mandates be escaped. HTML mode only
    requires ``&``, ``<``, ``>`` to be escaped.
    """
    e = _esc_html
    lines: list[str] = [f"<b>{e(payload['date'])}</b>", ""]

    for holding in payload.get("holdings", []):
        verified = holding.get("verified", True)
        exchange_code = holding.get("exchange")
        full_ticker = holding["ticker"]
        # Strip the exchange suffix (".PA", ".MI", …) when we have a
        # human-readable label to show on the next line; otherwise keep
        # the full ticker so the suffix info isn't silently lost.
        shown_ticker = display_ticker(full_ticker, exchange_code)
        ticker_text = e(shown_ticker)

        if verified:
            href = e(_yahoo_quote_url(full_ticker))
            ticker_styled = f'<a href="{href}">{ticker_text}</a>'
            marker = ""
        else:
            ticker_styled = _break_autolink(ticker_text)
            marker = " [?]"

        lines.append("<blockquote>")
        lines.append(f"<b>{ticker_styled}{marker}</b>")
        lines.append(e(holding["name"]))

        label = exchange_label(exchange_code)
        if label:
            lines.append(f"<i>{e(label)}</i>")

        variation_line = _format_variation(holding.get("variation"))
        if variation_line:
            lines.append(variation_line)

        items = holding.get("items", [])
        lines.append("")
        if not items:
            lines.append("<i>Aucune actualité matérielle.</i>")
        else:
            for i, item in enumerate(items):
                if i:
                    lines.append("")
                source_name = e(item.get("source_name") or "source")
                # URLs go in href="..." — must escape & < > too since the
                # value lives inside an HTML attribute.
                source_url = e(item["source_url"])
                lines.append(f"• <b>{e(item['title'])}</b>")
                lines.append(
                    f"  Impact : <b>{e(item['impact'])}</b> — {e(item['rationale'])}"
                )
                lines.append(
                    f"  Source : <a href=\"{source_url}\">{source_name}</a>"
                )

        lines.append("</blockquote>")
        lines.append("")

    return "\n".join(lines).rstrip()
