"""Djohodo CLI entrypoint.

Usage:
    python main.py                       # default: load ./portfolio.json and run
    python main.py --portfolio my.json   # custom portfolio path
    python main.py --dry-run             # build the prompt and print it, no API call

The ``--dry-run`` flag is the safe way to iterate on prompt wording, holdings,
or system configuration without consuming any Agent SDK credit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import anyio

from watcher import deliver
from watcher.agent import assemble_prompt, run_watch


def _load_portfolio(path: Path) -> list[dict[str, str]]:
    """Read ``portfolio.json`` and return a list of ``{ticker, name}`` dicts.

    Raises:
        SystemExit: When the file is missing, malformed, or has no holdings.
    """
    if not path.exists():
        print(
            f"[djohodo] Portfolio file not found: {path}\n"
            "         Copy portfolio.example.json to portfolio.json and edit it.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[djohodo] Invalid JSON in {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    holdings = data.get("holdings") if isinstance(data, dict) else None
    if not isinstance(holdings, list) or not holdings:
        print(
            f"[djohodo] {path} must contain a non-empty 'holdings' list.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    cleaned: list[dict[str, str]] = []
    for entry in holdings:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("ticker"), str)
            and isinstance(entry.get("name"), str)
        ):
            cleaned.append({"ticker": entry["ticker"], "name": entry["name"]})
    if not cleaned:
        print(f"[djohodo] No valid holdings found in {path}.", file=sys.stderr)
        raise SystemExit(2)
    return cleaned


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="djohodo",
        description="Daily portfolio news watch — produces a Markdown digest.",
    )
    parser.add_argument(
        "--portfolio",
        type=Path,
        default=Path("portfolio.json"),
        help="Path to the portfolio JSON file (default: ./portfolio.json).",
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
    holdings = _load_portfolio(args.portfolio)
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

    out_path = deliver(result.digest, today=today)

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
