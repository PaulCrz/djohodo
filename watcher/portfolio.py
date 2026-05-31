"""Load the portfolio holdings — from a Google Sheet URL or a JSON file.

Precedence (highest first):
  1. ``PORTFOLIO_SHEET_URL`` env var → fetch CSV from that URL and parse it.
     Designed for a Google Sheet published as CSV (no API key needed).
  2. JSON file at the path passed by the caller (defaults to
     ``./portfolio.json``). Used for local dev and as a fallback.

Sheet contract:
  * First column = ticker, second column = name (additional columns are
    ignored).
  * A header row with column names ``ticker`` and ``name`` is supported and
    enables name-based mapping in any order; without a header we fall back
    to positional (A=ticker, B=name).
  * Blank rows are skipped silently.
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
from pathlib import Path
from typing import Any


def load_portfolio(json_path: Path | None = None) -> list[dict[str, str]]:
    """Return a list of ``{"ticker", "name"}`` holdings.

    Args:
        json_path: File to read when no ``PORTFOLIO_SHEET_URL`` env var is
            set. Defaults to ``./portfolio.json``.

    Raises:
        RuntimeError: If neither source yields a non-empty, well-formed
            holdings list.
    """
    sheet_url = os.environ.get("PORTFOLIO_SHEET_URL")
    if sheet_url:
        return _load_from_sheet(sheet_url)
    return _load_from_json(json_path or Path("portfolio.json"))


def _load_from_sheet(url: str) -> list[dict[str, str]]:
    """Fetch and parse a Google Sheet exported as CSV."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            text = resp.read().decode("utf-8-sig")  # tolerate BOM
    except Exception as exc:
        raise RuntimeError(
            f"Could not fetch portfolio sheet at {url}: {exc}"
        ) from exc

    rows = [
        row
        for row in csv.reader(io.StringIO(text))
        if row and any(cell.strip() for cell in row)
    ]
    if not rows:
        raise RuntimeError(f"Portfolio sheet at {url} is empty.")

    ticker_idx, name_idx, data_rows = _detect_columns(rows)

    holdings: list[dict[str, str]] = []
    for row in data_rows:
        if len(row) <= max(ticker_idx, name_idx):
            continue
        ticker = row[ticker_idx].strip()
        name = row[name_idx].strip()
        if ticker and name:
            holdings.append({"ticker": ticker, "name": name})

    if not holdings:
        raise RuntimeError(
            f"No valid (ticker, name) rows found in sheet at {url}."
        )
    return holdings


def _detect_columns(rows: list[list[str]]) -> tuple[int, int, list[list[str]]]:
    """Pick column indices for ticker and name, with header-row autodetect.

    Returns ``(ticker_idx, name_idx, data_rows)``. ``data_rows`` is ``rows``
    minus the header if one was detected.
    """
    first = [c.strip().lower() for c in rows[0]]
    has_header = "ticker" in first or "name" in first

    if not has_header:
        return 0, 1, rows

    try:
        ticker_idx = first.index("ticker")
    except ValueError:
        # Header row, but no 'ticker' column — fall back to positional A.
        ticker_idx = 0
    try:
        name_idx = first.index("name")
    except ValueError:
        # No 'name' column either — take the column right after the ticker.
        name_idx = ticker_idx + 1

    return ticker_idx, name_idx, rows[1:]


def _load_from_json(path: Path) -> list[dict[str, str]]:
    """Read a local JSON file with shape ``{"holdings": [{ticker, name}, …]}``."""
    if not path.exists():
        raise RuntimeError(
            f"Portfolio file not found: {path}. "
            "Either copy portfolio.example.json to portfolio.json, "
            "or set the PORTFOLIO_SHEET_URL env var."
        )

    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc

    holdings = data.get("holdings") if isinstance(data, dict) else None
    if not isinstance(holdings, list) or not holdings:
        raise RuntimeError(
            f"{path} must contain a non-empty 'holdings' list."
        )

    cleaned: list[dict[str, str]] = []
    for entry in holdings:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("ticker"), str)
            and isinstance(entry.get("name"), str)
        ):
            cleaned.append({"ticker": entry["ticker"], "name": entry["name"]})
    if not cleaned:
        raise RuntimeError(f"No valid holdings found in {path}.")
    return cleaned
