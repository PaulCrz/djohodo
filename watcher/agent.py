"""Agent orchestration: call the Claude Agent SDK and collect the digest text.

This module is deliberately thin. It:
  1. Builds the prompt (delegated to :mod:`watcher.prompt`).
  2. Invokes the async ``query()`` API from ``claude-agent-sdk``.
  3. Streams messages, accumulating text blocks into the digest.
  4. Captures ``total_cost_usd`` from the terminal ``ResultMessage`` so the
     caller can monitor credit consumption.

Auth is read entirely from the environment (see README) — no key is ever
passed in code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Mapping

from watcher.prompt import build_prompt

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class WatchResult:
    """Outcome of one watch run."""

    digest: str
    """The Markdown digest text."""

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
    """Run one daily watch and return the digest.

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
            query,
        )
    except ImportError as exc:  # pragma: no cover - environmental
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run: pip install -e ."
        ) from exc

    chosen_model = model or os.environ.get("DJOHODO_MODEL") or DEFAULT_MODEL
    prompt = assemble_prompt(holdings, today)

    options = ClaudeAgentOptions(
        model=chosen_model,
        allowed_tools=["WebSearch"],
        system_prompt=(
            "Tu es Djohodo, un agent de veille financière rigoureux. "
            "Tu ne donnes jamais de conseil d'investissement."
        ),
    )

    digest_parts: list[str] = []
    raw: list[object] = []
    total_cost: float | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            raw.append(message)
            # AssistantMessage: list of content blocks. We only collect TextBlocks.
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str) and text:
                        digest_parts.append(text)
            # ResultMessage: terminal message carrying cost + usage.
            cost = getattr(message, "total_cost_usd", None)
            if isinstance(cost, (int, float)):
                total_cost = float(cost)
    except Exception as exc:  # pragma: no cover - depends on SDK runtime
        raise RuntimeError(f"Agent SDK call failed: {exc}") from exc

    digest = "\n".join(digest_parts).strip()
    if not digest:
        digest = (
            f"# Veille Djohodo — {(today or date.today()).isoformat()}\n\n"
            "_Aucun contenu retourné par l'agent._\n\n"
            "---\n*Ceci n'est pas un conseil financier.*\n"
        )

    return WatchResult(
        digest=digest,
        model=chosen_model,
        total_cost_usd=total_cost,
        raw_messages=raw,
    )
