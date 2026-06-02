"""Daily portfolio snapshots + day-over-day variation computation.

Every run writes today's holdings (ticker, name, amount, total_eur) to
``cache/snapshots/YYYY-MM-DD.json``. The next run loads the most recent
prior snapshot to compute a per-ticker variation: percentage and absolute
euro change of the position's total value.

The snapshots are intentionally gitignored — they contain the value of
each position in euros, and committing them would freeze that data into
git history forever (a real concern if the repo ever leaves private
visibility). In CI, ``actions/cache@v4`` restores/saves the
``cache/`` directory across runs so the diff still works.

Variation semantics:
  * Computed on ``total_eur`` (matches the user's "+125 €" example) — not
    on price-per-unit. If the user adds shares to a position between
    snapshots, the metric will spike on that day; that's accepted
    simplicity. To get pure market movement, divide by ``amount`` here
    instead.
  * A ticker present today but absent yesterday yields ``is_new=True``
    (renderer displays a "🆕" marker).
  * A ticker present yesterday but gone today is simply omitted — it's no
    longer in the portfolio.
  * If either day lacks ``total_eur`` for the ticker, no variation is
    emitted for it.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from watcher.portfolio import merge_same_ticker_holdings


SNAPSHOTS_DIR = Path("cache/snapshots")


@dataclass(frozen=True)
class Variation:
    """One day-over-day delta for a single holding.

    Attributes:
        pct: Percentage change vs the prior snapshot's ``total_eur``.
            ``None`` only when ``is_new`` is True.
        abs_eur: Absolute change in euros (today − previous).
            ``None`` only when ``is_new`` is True.
        is_new: True when the ticker had no prior snapshot entry.
        prev_date: ISO date of the snapshot we diffed against, for
            human-readable context in logs.
    """

    pct: float | None
    abs_eur: float | None
    is_new: bool
    prev_date: str | None


# --- Persistence ------------------------------------------------------------


def save_today(holdings: list[dict[str, Any]], today: date) -> None:
    """Persist today's holdings to ``cache/snapshots/YYYY-MM-DD.json``.

    Only the fields useful for diffing are stored — ticker, name, amount,
    total_eur. Resolver-added fields like ``verified`` are intentionally
    excluded (they're recomputed each run).
    """
    payload = {
        "date": today.isoformat(),
        "holdings": [
            {
                k: v
                for k, v in h.items()
                if k in ("ticker", "name", "amount", "total_eur")
            }
            for h in holdings
        ],
    }
    path = SNAPSHOTS_DIR / f"{today.isoformat()}.json"
    _atomic_write_json(path, payload)


def load_previous(today: date) -> dict[str, Any] | None:
    """Load the most recent snapshot strictly dated before ``today``.

    Returns the full ``{date, holdings}`` payload, or ``None`` when no
    prior snapshot exists (first run, or cache evicted).
    """
    if not SNAPSHOTS_DIR.exists():
        return None
    today_iso = today.isoformat()
    candidates = sorted(
        (p for p in SNAPSHOTS_DIR.glob("*.json") if p.stem < today_iso),
        reverse=True,
    )
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # corrupted file, try the one before
        if isinstance(data, dict) and isinstance(data.get("holdings"), list):
            return data
    return None


# --- Diff -------------------------------------------------------------------


def compute_variations(
    today_holdings: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> dict[str, Variation]:
    """Return per-ticker variations: today vs the most recent prior snapshot.

    Tickers present today but missing ``total_eur`` get no entry. Tickers
    present today but absent from the prior snapshot get ``is_new=True``.
    Tickers present in the prior snapshot but absent today are not in the
    returned dict (they've left the portfolio).
    """
    if previous is None:
        prev_by_ticker: dict[str, dict[str, Any]] = {}
        prev_date: str | None = None
    else:
        # Snapshots saved before the deduplication fix (commit "fix BTC
        # duplicate") may contain the same ticker twice — e.g. BTC for
        # Kraken Pro + Kraken Wallet. Merge defensively so the variation
        # diff isn't comparing today's summed total to yesterday's
        # last-occurrence-wins sliver.
        raw_prev = [
            h
            for h in previous.get("holdings", [])
            if isinstance(h, dict) and isinstance(h.get("ticker"), str)
        ]
        prev_by_ticker = {
            h["ticker"]: h for h in merge_same_ticker_holdings(raw_prev)
        }
        prev_date = previous.get("date")

    out: dict[str, Variation] = {}
    for h in today_holdings:
        ticker = h.get("ticker")
        today_total = h.get("total_eur")
        if not isinstance(ticker, str):
            continue
        if not isinstance(today_total, (int, float)):
            continue  # can't diff without a current value

        prev = prev_by_ticker.get(ticker)
        if prev is None:
            out[ticker] = Variation(
                pct=None, abs_eur=None, is_new=True, prev_date=prev_date
            )
            continue

        prev_total = prev.get("total_eur")
        if not isinstance(prev_total, (int, float)) or prev_total == 0:
            # Prior snapshot has no usable value — treat as missing.
            continue

        abs_change = float(today_total) - float(prev_total)
        pct_change = abs_change / float(prev_total) * 100.0
        out[ticker] = Variation(
            pct=pct_change,
            abs_eur=abs_change,
            is_new=False,
            prev_date=prev_date,
        )
    return out


# --- Internals --------------------------------------------------------------


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically via temp + rename so a crash never leaves a
    half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
