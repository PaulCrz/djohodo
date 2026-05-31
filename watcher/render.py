"""Render the structured digest payload into human-readable formats.

The agent now delivers the digest through the ``submit_digest`` MCP tool as
typed JSON. This module owns every conversion from that payload to something
a human will read: Markdown today, and (later) WhatsApp text, plain-text
email, etc. Keeping renderers separate from the agent means a new channel
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
        lines.append(f"## {holding['ticker']} — {holding['name']}")
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

    lines.append("*Ceci n'est pas un conseil financier.*")
    lines.append("")
    return "\n".join(lines)


def render_whatsapp(payload: dict[str, Any]) -> str:
    """Render the structured digest for WhatsApp text messages.

    WhatsApp accepts a narrow markdown subset: ``*bold*``, ``_italic_``,
    ``~strike~``, triple-backtick monospace. There are no headings, and link
    syntax ``[label](url)`` is not interpreted — URLs must appear bare and
    are auto-linkified by the WhatsApp client. We use bold lines as section
    "headings" and plain-text URLs as sources.
    """
    lines: list[str] = [f"*Veille Djohodo — {payload['date']}*", ""]

    for holding in payload.get("holdings", []):
        lines.append(f"*{holding['ticker']} — {holding['name']}*")
        items = holding.get("items", [])

        if not items:
            lines.append("_Aucune actualité matérielle._")
        else:
            for item in items:
                source_name = item.get("source_name") or "source"
                lines.append("")
                lines.append(f"• *{item['title']}*")
                lines.append(
                    f"  Impact : *{item['impact']}* — {item['rationale']}"
                )
                lines.append(f"  Source : {source_name} — {item['source_url']}")

        lines.append("")  # blank line between holdings

    lines.append("_Ceci n'est pas un conseil financier._")
    return "\n".join(lines)
