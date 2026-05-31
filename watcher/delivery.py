"""Delivery layer: where the digest goes once it has been produced.

Three channels:
  * **console** — always on; prints the Markdown digest to stdout.
  * **file** — always on; writes ``digests/YYYY-MM-DD.md``.
  * **Telegram** — opt-in via ``DJOHODO_TELEGRAM_ENABLED=1``; sends a single
    HTML-formatted message through the Bot API. Uses the Telegram-flavoured
    renderer from :mod:`watcher.render`. See :func:`_send_telegram` for the
    env vars.

The function returns the path of the written file so the caller (or CI) can
publish it as an artifact.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from watcher.render import render_telegram


DIGESTS_DIR = Path("digests")

# Telegram Bot API hard cap on a single ``sendMessage`` body.
TELEGRAM_TEXT_LIMIT = 4096


def deliver(
    digest: str,
    structured: dict[str, Any] | None = None,
    today: date | None = None,
) -> Path:
    """Print, persist, and optionally push to Telegram the digest.

    Args:
        digest: The Markdown digest text (used for console + file).
        structured: The typed payload returned by the agent. Required to
            render the Telegram variant; if absent, Telegram delivery is
            skipped even when enabled (with a log line).
        today: Reference date used for the filename. Defaults to today.

    Returns:
        The path to the Markdown file that was written.
    """
    today = today or date.today()

    print(digest)

    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIGESTS_DIR / f"{today.isoformat()}.md"
    out_path.write_text(digest, encoding="utf-8")

    if os.environ.get("DJOHODO_TELEGRAM_ENABLED") == "1":
        if structured is None:
            print(
                "[djohodo] Telegram delivery skipped: "
                "structured payload missing."
            )
        else:
            try:
                _send_telegram(render_telegram(structured))
                print("[djohodo] Telegram delivery OK.")
            except Exception as exc:  # pragma: no cover - depends on TG env
                # Telegram is best-effort; never fail the run because of it.
                print(f"[djohodo] Telegram delivery failed: {exc}")

    return out_path


def _send_telegram(message: str) -> None:
    """Send a single message via the Telegram Bot API.

    Required env:
        ``TELEGRAM_BOT_TOKEN`` — issued by ``@BotFather`` when you create
            the bot (format: ``123456:ABC-…``).
        ``TELEGRAM_CHAT_ID`` — target chat:
            - your own numeric user id for a private chat (positive int),
            - ``-100…`` for a channel or supergroup,
            - or ``@channel_username`` for a public channel.

    Messages longer than 4096 chars are truncated with an ellipsis. The
    bot must have been started by the user once (``/start``) before it can
    DM them.
    """
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    if len(message) > TELEGRAM_TEXT_LIMIT:
        message = message[: TELEGRAM_TEXT_LIMIT - 1] + "…"

    body = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        # Don't show the large card preview for the first source URL — the
        # digest already lists multiple sources, the preview adds noise.
        "disable_web_page_preview": True,
    }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(
                    f"Telegram API non-OK response: {payload}"
                )
    except urllib.error.HTTPError as exc:
        # Telegram puts the actual error reason in the response body
        # ({"ok": false, "description": "..."}). Surface it.
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Telegram API HTTP {exc.code}: {error_body}"
        ) from exc
