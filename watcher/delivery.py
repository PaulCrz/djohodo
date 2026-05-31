"""Delivery layer: where the digest goes once it has been produced.

Three channels are supported:
  * **console** — always on; prints the digest to stdout.
  * **file** — always on; writes ``digests/YYYY-MM-DD.md``.
  * **smtp** — opt-in via ``DJOHODO_EMAIL_ENABLED=1``; a working but minimal
    SMTP sender. Reads ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``,
    ``SMTP_PASSWORD``, ``SMTP_FROM``, ``SMTP_TO`` from the environment.

The function is intentionally side-effecting and returns the path to the
written file so the caller (or CI) can publish it as an artifact.
"""

from __future__ import annotations

import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path


DIGESTS_DIR = Path("digests")


def deliver(digest: str, today: date | None = None) -> Path:
    """Print, persist, and optionally email the digest.

    Args:
        digest: The Markdown digest text.
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
