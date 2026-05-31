"""Agent orchestration: drive the Claude Agent SDK with structured output.

Flow:
  1. Build the prompt (delegated to :mod:`watcher.prompt`).
  2. Register an in-process ``submit_digest`` MCP tool whose JSON Schema
     defines the digest shape (per-holding items with impact enum + sources).
  3. Run the async ``query()`` with WebSearch + that tool allowed.
  4. The tool handler captures the model's structured payload into a closure.
  5. Render the payload to Markdown (via :mod:`watcher.render`) — the
     structured form is what later modules (analyst, Telegram delivery,
     plain-text email) will consume.

Authentication is read entirely from the environment (see README). No
credential is ever passed in code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from watcher.prompt import build_prompt
from watcher.render import render_markdown

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "description": "Date du digest au format ISO YYYY-MM-DD.",
        },
        "holdings": {
            "type": "array",
            "description": (
                "Une entrée par position du portefeuille, "
                "dans le même ordre que celui fourni en entrée."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "name": {"type": "string"},
                    "items": {
                        "type": "array",
                        "description": (
                            "Liste des actualités matérielles des dernières "
                            "24h pour cette position. Liste vide si aucune."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Titre de l'actualité, en français.",
                                },
                                "impact": {
                                    "type": "string",
                                    "enum": ["haussier", "baissier", "neutre"],
                                    "description": "Impact probable sur le titre.",
                                },
                                "rationale": {
                                    "type": "string",
                                    "description": (
                                        "Justification d'une phrase, appuyée "
                                        "sur le contenu de l'article."
                                    ),
                                },
                                "source_name": {
                                    "type": "string",
                                    "description": "Nom court de la source (ex : 'Reuters').",
                                },
                                "source_url": {
                                    "type": "string",
                                    "description": "URL canonique de l'article.",
                                },
                            },
                            "required": [
                                "title",
                                "impact",
                                "rationale",
                                "source_url",
                            ],
                        },
                    },
                },
                "required": ["ticker", "name", "items"],
            },
        },
    },
    "required": ["date", "holdings"],
}


@dataclass
class WatchResult:
    """Outcome of one watch run."""

    digest: str
    """The Markdown digest text (rendered from :attr:`structured`)."""

    structured: dict[str, Any] | None
    """The raw structured payload submitted by the model, or ``None`` if the
    tool was never called (degenerate case — kept for debuggability)."""

    model: str
    """Model id actually used for the run."""

    total_cost_usd: float | None = None
    """Cost reported by the SDK's ``ResultMessage``; ``None`` if unavailable."""

    raw_messages: list[object] = field(default_factory=list)
    """Raw SDK messages, kept for debugging."""


def assemble_prompt(
    holdings: Iterable[Mapping[str, str]],
    today: date | None = None,
) -> str:
    """Public helper so ``--dry-run`` can render the prompt without a model call."""
    return build_prompt(holdings, today or date.today())


async def run_watch(
    holdings: Iterable[Mapping[str, str]],
    today: date | None = None,
    model: str | None = None,
) -> WatchResult:
    """Run one daily watch and return the structured + rendered digest.

    Args:
        holdings: Iterable of ``{"ticker", "name"}`` mappings.
        today: Override the reference date (defaults to ``date.today()``).
        model: Override the model id. Falls back to env ``DJOHODO_MODEL`` then
            :data:`DEFAULT_MODEL`.

    Raises:
        RuntimeError: If the SDK cannot be imported or the run fails.
    """
    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            ClaudeAgentOptions,
            create_sdk_mcp_server,
            query,
            tool,
        )
    except ImportError as exc:  # pragma: no cover - environmental
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run: pip install -e ."
        ) from exc

    chosen_model = model or os.environ.get("DJOHODO_MODEL") or DEFAULT_MODEL
    today = today or date.today()
    prompt = assemble_prompt(holdings, today)

    captured: dict[str, Any] = {}

    @tool(
        "submit_digest",
        (
            "Submit the final structured Djohodo digest. "
            "Call this exactly once, after completing all WebSearch research."
        ),
        DIGEST_SCHEMA,
    )
    async def submit_digest(args: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = args
        return {
            "content": [
                {"type": "text", "text": "Digest enregistré."}
            ]
        }

    server = create_sdk_mcp_server(name="djohodo", tools=[submit_digest])

    options = ClaudeAgentOptions(
        model=chosen_model,
        mcp_servers={"djohodo": server},
        allowed_tools=["WebSearch", "mcp__djohodo__submit_digest"],
        system_prompt=(
            "Tu es Djohodo, un agent de veille financière rigoureux. "
            "Tu ne donnes jamais de conseil d'investissement."
        ),
    )

    raw: list[object] = []
    total_cost: float | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            raw.append(message)
            cost = getattr(message, "total_cost_usd", None)
            if isinstance(cost, (int, float)):
                total_cost = float(cost)
    except Exception as exc:  # pragma: no cover - depends on SDK runtime
        raise RuntimeError(f"Agent SDK call failed: {exc}") from exc

    payload = captured.get("payload")
    if payload is None:
        # Degenerate path: the model never called submit_digest. Emit a
        # placeholder so the downstream pipeline still produces *something*,
        # but surface the failure clearly in the file content.
        digest = (
            f"# Veille Djohodo — {today.isoformat()}\n\n"
            "_L'agent n'a pas appelé `submit_digest` ; "
            "payload structuré indisponible. Voir les logs._\n\n"
            "---\n*Ceci n'est pas un conseil financier.*\n"
        )
    else:
        # `date` is required by the schema but the model can be sloppy; fill
        # defensively with today's date if missing.
        payload.setdefault("date", today.isoformat())
        digest = render_markdown(payload)

    return WatchResult(
        digest=digest,
        structured=payload,
        model=chosen_model,
        total_cost_usd=total_cost,
        raw_messages=raw,
    )
