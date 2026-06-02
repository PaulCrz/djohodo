"""Djohodo CLI entrypoint.

Usage:
    python main.py                # real run: extract holdings → watch → deliver
    python main.py --dry-run      # extract holdings + print the prompt, no watch
    python main.py --model X      # override the model for both pre-pass and watch

The portfolio source is the published Google Sheet behind
``PORTFOLIO_SHEET_URL``. Telegram is the sole delivery channel
(``DJOHODO_TELEGRAM_ENABLED=1`` + ``TELEGRAM_BOT_TOKEN`` +
``TELEGRAM_CHAT_ID``); the digest is also always written to
``digests/YYYY-MM-DD.md``.

``--dry-run`` still spends the ~$0.005 portfolio extraction call but
skips the WebSearch + digest call, so it's the cheap way to verify the
sheet parses and to inspect the prompt.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import anyio

from watcher.agent import assemble_prompt, run_watch
from watcher.delivery import deliver
from watcher.portfolio import load_portfolio
from watcher.resolver import resolve_holdings
from watcher.snapshot import compute_variations, load_previous, save_today


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="djohodo",
        description="Daily portfolio news watch — produces a Markdown digest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and print the prompt without calling the news watcher.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the model id (else DJOHODO_MODEL env, else Haiku 4.5).",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    try:
        holdings = await load_portfolio()
        holdings = await resolve_holdings(holdings)
    except RuntimeError as exc:
        print(f"[djohodo] {exc}", file=sys.stderr)
        return 2

    today = date.today()

    # Snapshot the values + compute day-over-day variations. Order
    # matters: load the previous snapshot BEFORE saving today's, so a
    # second run on the same day still diffs against the right baseline.
    previous = load_previous(today)
    variations = compute_variations(holdings, previous)
    save_today(holdings, today)
    for h in holdings:
        v = variations.get(h["ticker"])
        if v is not None:
            h["variation"] = {
                "pct": v.pct,
                "abs_eur": v.abs_eur,
                "is_new": v.is_new,
                "prev_date": v.prev_date,
            }

    if args.dry_run:
        prompt = assemble_prompt(holdings, today)
        print("===== DJOHODO DRY RUN — PROMPT PREVIEW =====")
        print(prompt)
        print("===== END OF PROMPT (no watch call performed) =====")
        return 0

    try:
        result = await run_watch(holdings, today=today, model=args.model)
    except RuntimeError as exc:
        print(f"[djohodo] Run failed: {exc}", file=sys.stderr)
        return 1

    out_path = deliver(result.digest, structured=result.structured, today=today)

    cost_str = (
        f"${result.total_cost_usd:.4f}"
        if result.total_cost_usd is not None
        else "n/a"
    )
    print(
        f"\n[djohodo] Model: {result.model} | Cost: {cost_str} | Saved: {out_path}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return anyio.run(_amain, args)


if __name__ == "__main__":
    raise SystemExit(main())
