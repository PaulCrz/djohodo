"""Djohodo CLI entrypoint.

Usage:
    python main.py                       # use PORTFOLIO_SHEET_URL if set, else ./portfolio.json
    python main.py --portfolio my.json   # override the local JSON path
    python main.py --dry-run             # build the prompt and print it, no API call

The portfolio source is picked by :func:`watcher.portfolio.load_portfolio`:
``PORTFOLIO_SHEET_URL`` (Google Sheet published as CSV) takes precedence
over the local JSON file.

The ``--dry-run`` flag is the safe way to iterate on prompt wording,
holdings, or system configuration without consuming any Agent SDK credit.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import anyio

from watcher import deliver
from watcher.agent import assemble_prompt, run_watch
from watcher.portfolio import load_portfolio


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="djohodo",
        description="Daily portfolio news watch — produces a Markdown digest.",
    )
    parser.add_argument(
        "--portfolio",
        type=Path,
        default=Path("portfolio.json"),
        help=(
            "Local JSON file used when PORTFOLIO_SHEET_URL env var is unset "
            "(default: ./portfolio.json)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and print the prompt without calling the model.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the model id (else DJOHODO_MODEL env, else Haiku 4.5).",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    try:
        holdings = load_portfolio(args.portfolio)
    except RuntimeError as exc:
        print(f"[djohodo] {exc}", file=sys.stderr)
        return 2

    today = date.today()

    if args.dry_run:
        prompt = assemble_prompt(holdings, today)
        print("===== DJOHODO DRY RUN — PROMPT PREVIEW =====")
        print(prompt)
        print("===== END OF PROMPT (no model call performed) =====")
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
