"""Deterministic ticker → canonical-name resolver.

The LLM extraction in :mod:`watcher.portfolio` produces ``{ticker, name}``
pairs from the messy Google Sheet, but the LLM-inferred name is often
wrong (it once labelled ``BNKE.PA`` as "BNP Paribas" — the actual
instrument is the Amundi Euro Stoxx Banks ETF). This module replaces the
LLM's guess with a name fetched from an authoritative source.

Resolution order:
  1. **Yahoo Finance search** (``query1.finance.yahoo.com/v1/finance/search``)
     — primary. Free, no auth/crumb, covers equities, ETFs, mutual funds,
     and crypto pairs (``BTC-USD``). The ``/v7/finance/quote`` endpoint's
     crumb cookie dance is avoided deliberately.
  2. **OpenFIGI** (``api.openfigi.com/v3/mapping``) — fallback. Free up
     to 25 req/min unauth. Needs a Yahoo-suffix → FIGI-exchange-code map
     (``.PA → FP``, etc.).
  3. **LLM-extracted name** (verbatim) — last resort. The holding is
     marked ``verified=False`` so the prompt and the renderers can flag
     it for the human reader.

Resolved entries are cached in ``cache/ticker_registry.json``. Instrument
names effectively never change, so the cache has no TTL.

The resolver never raises for an individual miss — that would block the
whole watch on a single typo. It raises *only* when every lookup fails
in the same run, which signals a network outage rather than a data
issue.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import anyio


CACHE_PATH = Path("cache/ticker_registry.json")

# Default User-Agent — Yahoo 401s the request without it. Any non-empty
# browser-ish UA works; we don't impersonate any specific product.
_USER_AGENT = "djohodo/0.1 (+https://github.com/PaulCrz/djohodo)"

# Yahoo Finance Euronext-style suffix → OpenFIGI exchange code.
# Only covers the suffixes we expect to see; an unmapped suffix just skips
# the OpenFIGI fallback (the LLM name then wins, marked unverified).
_YAHOO_TO_FIGI: dict[str, str] = {
    "PA": "FP",   # Euronext Paris
    "MI": "IM",   # Borsa Italiana (Milan)
    "DE": "GR",   # Xetra
    "L": "LN",    # London Stock Exchange
    "SA": "BZ",   # B3 (São Paulo)
    "AS": "NA",   # Euronext Amsterdam
    "BR": "BB",   # Euronext Brussels
    "LS": "PL",   # Euronext Lisbon
    "MC": "SM",   # BME (Madrid)
    "SW": "SE",   # SIX Swiss Exchange
}


# Yahoo's ``exchange`` field (a 2–4 letter code like "PAR", "NMS", "NCM")
# → human-readable name we show in the digest. Sub-tier NASDAQ markets
# (NMS / NGM / NCM) are intentionally collapsed to "NASDAQ" — most users
# don't care about Global Select vs Global vs Capital. CCC (crypto) and
# CCY (currency) are deliberately absent: the label would just restate
# what the ticker format already implies.
EXCHANGE_LABELS: dict[str, str] = {
    # United States
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ",
    "NYQ": "NYSE", "ASE": "NYSE American", "PCX": "NYSE Arca",
    "BTS": "Cboe", "PNK": "OTC Pink",
    # Euronext
    "PAR": "Euronext Paris", "AMS": "Euronext Amsterdam",
    "BRU": "Euronext Brussels", "LIS": "Euronext Lisbon",
    "ISE": "Euronext Dublin", "OSL": "Euronext Oslo",
    # Other European
    "MIL": "Borsa Italiana", "GER": "Xetra", "FRA": "Frankfurt",
    "STU": "Stuttgart", "BER": "Berlin", "MUN": "Munich",
    "LSE": "London Stock Exchange", "IOB": "LSE Intl Order Book",
    "MCE": "BME (Madrid)", "EBS": "SIX Swiss Exchange",
    "VTX": "SIX Swiss Exchange",
    "STO": "Nasdaq Stockholm", "CPH": "Nasdaq Copenhagen",
    "HEL": "Nasdaq Helsinki", "WSE": "Warsaw Stock Exchange",
    "VIE": "Vienna",
    # Asia / Pacific
    "JPX": "Japan Exchange", "TYO": "Tokyo Stock Exchange",
    "HKG": "Hong Kong Stock Exchange", "SES": "Singapore Exchange",
    "ASX": "Australian Securities Exchange", "NZE": "NZX",
    "KSC": "Korea Stock Exchange", "KOE": "Korea Stock Exchange",
    "KOQ": "KOSDAQ", "SHH": "Shanghai Stock Exchange",
    "SHZ": "Shenzhen Stock Exchange", "BSE": "Bombay Stock Exchange",
    "NSI": "NSE India", "TAI": "Taiwan Stock Exchange",
    # Americas
    "TOR": "Toronto Stock Exchange", "VAN": "TSX Venture",
    "SAO": "B3 (São Paulo)", "MEX": "Bolsa Mexicana",
    "BUE": "BYMA (Buenos Aires)",
}


# Yahoo Finance ticker suffixes we know how to recognise as exchange
# markers. Used by :func:`display_ticker` to decide whether the trailing
# ``.XX`` is a strip-able exchange suffix vs a meaningful part of the
# symbol (e.g. ``BRK.B`` is *not* in this set).
_STRIPPABLE_SUFFIXES: set[str] = {
    # Euronext family
    "PA", "AS", "BR", "LS", "IR",
    # German / Italian / Swiss
    "DE", "F", "SG", "MU", "BE", "MI", "SW", "VX",
    # London
    "L", "IL",
    # Spain
    "MC", "MA",
    # Nordic
    "ST", "CO", "HE", "OL",
    # Eastern Europe
    "WA", "VI",
    # Asia / Pacific
    "T", "HK", "AX", "NZ", "KS", "KQ", "SS", "SZ",
    "BO", "NS", "TW", "TWO", "JK", "BK", "KL", "SI",
    # Americas (ex-US)
    "TO", "V", "CN", "SA", "MX", "BA",
    # Africa
    "JO",
}


def exchange_label(exchange_code: str | None) -> str | None:
    """Return a human-readable exchange name, or ``None`` if unknown.

    Unknown codes deliberately return ``None`` so the renderer can omit
    the exchange line entirely (better than displaying a cryptic code).
    """
    if not exchange_code:
        return None
    return EXCHANGE_LABELS.get(exchange_code.upper())


def display_ticker(ticker: str, exchange_code: str | None) -> str:
    """Strip a known Yahoo exchange suffix from the ticker when we *also*
    have a human-readable label to show in its place.

    If we don't have a label (unknown exchange code, unresolved ticker),
    we leave the full ticker intact — losing the suffix without
    compensating it with a label would silently drop information.
    """
    if not exchange_label(exchange_code):
        return ticker
    root, dot, suffix = ticker.rpartition(".")
    if dot and root and suffix.upper() in _STRIPPABLE_SUFFIXES:
        return root
    return ticker


@dataclass(frozen=True)
class ResolvedTicker:
    """One resolved (or unresolved-but-flagged) holding."""

    ticker: str
    name: str
    quote_type: str | None
    exchange: str | None
    source: str        # "yahoo" | "openfigi" | "llm"

    @property
    def verified(self) -> bool:
        return self.source != "llm"


# --- Public entrypoint ------------------------------------------------------


async def resolve_holdings(
    holdings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Augment each ``{ticker, name}`` with a canonical name + ``verified`` flag.

    Args:
        holdings: List of ``{"ticker": str, "name": str}`` mappings as
            returned by :func:`watcher.portfolio.load_portfolio`.

    Returns:
        A new list where each entry is ``{ticker, name, verified, exchange}``.
        ``verified=True`` when Yahoo or OpenFIGI answered; ``False`` when
        the LLM-extracted name was kept verbatim. ``exchange`` is the raw
        Yahoo exchange code (or ``None`` for LLM fallbacks); the renderer
        maps it through :func:`exchange_label` for display.

    Raises:
        RuntimeError: If *every* lookup failed in the same run (network
            outage). Individual misses are tolerated silently.
    """
    if not holdings:
        return []

    return await anyio.to_thread.run_sync(_resolve_sync, list(holdings))


def _resolve_sync(holdings: list[dict[str, str]]) -> list[dict[str, Any]]:
    cache = _load_cache(CACHE_PATH)
    cache_dirty = False
    resolved: list[ResolvedTicker] = []
    hits = 0

    for entry in holdings:
        ticker = entry["ticker"]
        llm_name = entry.get("name", ticker)

        cached = cache.get(ticker)
        if cached and cached.get("source") in {"yahoo", "openfigi"}:
            resolved.append(
                ResolvedTicker(
                    ticker=ticker,
                    name=cached["name"],
                    quote_type=cached.get("quote_type"),
                    exchange=cached.get("exchange"),
                    source=cached["source"],
                )
            )
            hits += 1
            continue

        rt = _lookup_yahoo(ticker) or _lookup_openfigi(ticker)
        if rt is not None:
            cache[ticker] = {
                "name": rt.name,
                "quote_type": rt.quote_type,
                "exchange": rt.exchange,
                "source": rt.source,
                "resolved_at": date.today().isoformat(),
            }
            cache_dirty = True
            resolved.append(rt)
            hits += 1
        else:
            # Both lookups failed — keep the LLM name, flag unverified.
            print(
                f"[djohodo] resolver: could not resolve {ticker} via Yahoo or "
                f"OpenFIGI; keeping LLM name {llm_name!r} as unverified."
            )
            resolved.append(
                ResolvedTicker(
                    ticker=ticker,
                    name=llm_name,
                    quote_type=None,
                    exchange=None,
                    source="llm",
                )
            )

    if cache_dirty:
        _save_cache(CACHE_PATH, cache)

    if hits == 0 and holdings:
        raise RuntimeError(
            "Ticker resolver: every lookup failed. "
            "Network outage or both Yahoo Finance + OpenFIGI are down."
        )

    return [
        {
            "ticker": r.ticker,
            "name": r.name,
            "verified": r.verified,
            "exchange": r.exchange,    # raw Yahoo code (e.g. "PAR"); renderer
                                       # converts to a human label via
                                       # exchange_label()
        }
        for r in resolved
    ]


# --- Yahoo Finance ----------------------------------------------------------


def _lookup_yahoo(ticker: str) -> ResolvedTicker | None:
    """Hit the Yahoo Finance search endpoint and pick the exact-symbol hit.

    Returns ``None`` on any failure (HTTP error, JSON parse error, no
    matching symbol, missing name). The caller falls through to OpenFIGI.
    """
    qs = urllib.parse.urlencode({"q": ticker, "quotesCount": 5, "newsCount": 0})
    url = f"https://query1.finance.yahoo.com/v1/finance/search?{qs}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None

    quotes = payload.get("quotes") or []
    # Exact-symbol match wins; if not present, Yahoo's first hit is usually
    # a relevant suggestion but for safety we require an exact match.
    for q in quotes:
        if q.get("symbol") != ticker:
            continue
        name = q.get("longname") or q.get("shortname")
        if not name:
            return None
        return ResolvedTicker(
            ticker=ticker,
            name=name,
            quote_type=q.get("quoteType"),
            exchange=q.get("exchange"),
            source="yahoo",
        )
    return None


# --- OpenFIGI (fallback) ----------------------------------------------------


def _lookup_openfigi(ticker: str) -> ResolvedTicker | None:
    """Hit OpenFIGI's mapping endpoint as a fallback for Yahoo misses.

    OpenFIGI requires the ticker root + an exchange code (FP, GR, …) —
    not the Yahoo-style suffix. We split the ticker on '.', map the
    suffix, and bail if the suffix isn't in our table.
    """
    root, _, suffix = ticker.partition(".")
    if not root:
        return None
    exch_code = _yahoo_to_figi_exchange(suffix) if suffix else "US"
    if exch_code is None:
        return None

    body = json.dumps(
        [{"idType": "TICKER", "idValue": root, "exchCode": exch_code}]
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openfigi.com/v3/mapping",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None

    # Response is a list aligned with the request items. We sent one.
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    data = first.get("data") if isinstance(first, dict) else None
    if not data:
        return None

    hit = data[0]
    name = hit.get("name") or hit.get("securityDescription")
    if not name:
        return None
    return ResolvedTicker(
        ticker=ticker,
        name=name,
        quote_type=hit.get("marketSector") or hit.get("securityType"),
        exchange=hit.get("exchCode"),
        source="openfigi",
    )


def _yahoo_to_figi_exchange(suffix: str) -> str | None:
    """Map a Yahoo Finance suffix (without the leading dot) to an OpenFIGI
    exchange code, or ``None`` if we don't have a mapping."""
    return _YAHOO_TO_FIGI.get(suffix.upper())


# --- Persistent cache -------------------------------------------------------


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Read the cache from disk, returning an empty dict on any problem.

    A malformed or partially-written cache shouldn't break a run; we'll
    just rebuild it from scratch.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    """Atomically persist the cache (temp file + rename) so a crash mid-
    write never leaves a half-empty registry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".ticker_registry.", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup; if rename succeeded we never reach here.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
