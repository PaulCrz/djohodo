"""Delivery layer: where the digest goes once it has been produced.

Four channels are supported:
  * **console** — always on; prints the Markdown digest to stdout.
  * **file** — always on; writes ``digests/YYYY-MM-DD.md``.
  * **SMTP** — opt-in via ``DJOHODO_EMAIL_ENABLED=1``; minimal plain-text
    sender. Reads ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``,
    ``SMTP_PASSWORD``, ``SMTP_FROM``, ``SMTP_TO`` from the environment.
  * **WhatsApp** — opt-in via ``DJOHODO_WHATSAPP_ENABLED=1``; sends through
    the Meta Cloud API. Uses the WhatsApp-flavoured renderer from
    :mod:`watcher.render`, not the Markdown digest. See
    :func:`_send_whatsapp` for the env vars and the text-vs-template choice.

The function returns the path of the written file so the caller (or CI) can
publish it as an artifact.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from watcher.render import render_whatsapp


DIGESTS_DIR = Path("digests")

# Meta Cloud API constants.
WHATSAPP_API_VERSION = "v21.0"
WHATSAPP_TEXT_BODY_LIMIT = 4096
WHATSAPP_TEMPLATE_PARAM_LIMIT = 1024


def deliver(
    digest: str,
    structured: dict[str, Any] | None = None,
    today: date | None = None,
) -> Path:
    """Print, persist, and optionally email + WhatsApp the digest.

    Args:
        digest: The Markdown digest text (used for console, file, and SMTP).
        structured: The typed payload returned by the agent. Required to
            render the WhatsApp variant; if absent, WhatsApp delivery is
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

    if os.environ.get("DJOHODO_EMAIL_ENABLED") == "1":
        try:
            _send_email(digest, today)
        except Exception as exc:  # pragma: no cover - depends on SMTP env
            # Email is best-effort; never fail the run because of it.
            print(f"[djohodo] SMTP delivery failed: {exc}")

    if os.environ.get("DJOHODO_WHATSAPP_ENABLED") == "1":
        if structured is None:
            print(
                "[djohodo] WhatsApp delivery skipped: "
                "structured payload missing."
            )
        else:
            try:
                _send_whatsapp(render_whatsapp(structured))
                print("[djohodo] WhatsApp delivery OK.")
            except Exception as exc:  # pragma: no cover - depends on WA env
                # WhatsApp is best-effort; never fail the run because of it.
                print(f"[djohodo] WhatsApp delivery failed: {exc}")

    return out_path


def _send_email(digest: str, today: date) -> None:
    """Minimal SMTP sender. Raises if any required env var is missing."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("SMTP_FROM", user)
    recipient = os.environ["SMTP_TO"]

    msg = EmailMessage()
    msg["Subject"] = f"Veille Djohodo — {today.isoformat()}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(digest)

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def _send_whatsapp(message: str) -> None:
    """Send a WhatsApp message via Meta's Cloud API.

    Two modes, selected with the ``WHATSAPP_MODE`` env var:

    * ``text`` (default) — free-form text message. Meta only allows this
      *within the 24-hour customer-service window* opened by the recipient
      messaging your business number. For an unattended daily cron, the
      window is unlikely to be open, so prefer ``template`` mode below.
      Useful for interactive debugging or for delivery to a number that
      messages your bot daily.

    * ``template`` — sends a pre-approved template message. This is the
      canonical pattern for unattended outbound messages. The template
      must be created (and approved) in Meta Business Suite first; this
      code assumes a single ``{{1}}`` body parameter into which the
      rendered digest is injected. Template body parameters are capped at
      1024 characters by Meta — longer payloads are truncated with an
      ellipsis. Set ``WHATSAPP_TEMPLATE_NAME`` and optionally
      ``WHATSAPP_TEMPLATE_LANGUAGE`` (default ``fr``).

    Required env (both modes):
        ``WHATSAPP_PHONE_NUMBER_ID`` — id of your business phone number
        ``WHATSAPP_ACCESS_TOKEN`` — bearer token (system-user token preferred)
        ``WHATSAPP_RECIPIENT`` — destination phone in E.164 without the ``+``
    """
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    access_token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    recipient = os.environ["WHATSAPP_RECIPIENT"]
    mode = os.environ.get("WHATSAPP_MODE", "text").lower()

    if mode == "template":
        body = _build_template_body(recipient, message)
    elif mode == "text":
        body = _build_text_body(recipient, message)
    else:
        raise ValueError(
            f"Invalid WHATSAPP_MODE={mode!r}; expected 'text' or 'template'."
        )

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"
        f"/{phone_number_id}/messages"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()  # consume so the connection can be reused/closed cleanly
    except urllib.error.HTTPError as exc:
        # Meta puts the actual error reason in the response body — surface it.
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"WhatsApp API HTTP {exc.code}: {error_body}"
        ) from exc


def _build_text_body(recipient: str, message: str) -> dict[str, Any]:
    """Compose a WhatsApp text message body, truncating past Meta's limit."""
    if len(message) > WHATSAPP_TEXT_BODY_LIMIT:
        message = message[: WHATSAPP_TEXT_BODY_LIMIT - 1] + "…"
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }


def _build_template_body(recipient: str, message: str) -> dict[str, Any]:
    """Compose a WhatsApp template message body.

    The template must already exist on Meta's side and use a single
    ``{{1}}`` body parameter — that's where ``message`` is injected.
    """
    template_name = os.environ["WHATSAPP_TEMPLATE_NAME"]
    language = os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "fr")
    if len(message) > WHATSAPP_TEMPLATE_PARAM_LIMIT:
        message = message[: WHATSAPP_TEMPLATE_PARAM_LIMIT - 1] + "…"
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": message}],
                }
            ],
        },
    }
