"""Load the portfolio holdings — from a Google Sheet URL or a JSON file.

Precedence (highest first):
  1. ``PORTFOLIO_SHEET_URL`` env var → fetch the sheet as CSV and ask the
     Agent SDK to extract tradable holdings (equities, ETFs, crypto) from
     it. This is robust to arbitrary layouts (multi-section patrimonial
     dashboards, mixed asset classes, French/English labels, etc.) and
     converts Google-Finance-style tickers ("EPA:AM") to the Yahoo-Finance
     format ("AM.PA") that financial news sources index more reliably.
  2. JSON file at the path passed by the caller (defaults to
     ``./portfolio.json``). Used for local dev / dry-run iteration with
     a known clean list.

Both paths return a ``list[{"ticker": str, "name": str}]``. The function
itself is ``async`` because the sheet path makes an Agent SDK call;
``main.py`` already runs inside ``anyio.run``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

import anyio


# --- LLM extraction schema + prompt -----------------------------------------

PORTFOLIO_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "holdings": {
            "type": "array",
            "description": (
                "Positions financières traçables extraites de la feuille "
                "de calcul fournie."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "Symbole au format Yahoo Finance "
                            "(ex : 'AAPL', 'AM.PA', 'BTC-USD')."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Nom complet de l'instrument "
                            "(entreprise, ETF, ou cryptomonnaie)."
                        ),
                    },
                },
                "required": ["ticker", "name"],
            },
        },
    },
    "required": ["holdings"],
}


def _build_extract_prompt(csv_text: str) -> str:
    return f"""Voici l'export CSV d'une feuille de calcul de suivi patrimonial
personnel. Le format est libre (sections multiples, colonnes désordonnées,
sous-lignes Amount/Total/Proportion, mélange français/anglais).

```csv
{csv_text}
```

Ta tâche : extraire la liste des positions FINANCIÈRES TRAÇABLES susceptibles
d'avoir des actualités matérielles publiables :

✔ Actions cotées (equities)
✔ ETF et fonds cotés
✔ Cryptomonnaies

IGNORE strictement :
✘ Cash, comptes courants, livrets (Livret A, LEP, CEL), épargne non investie
✘ Assurance-vie sans ligne d'instrument détaillée (juste « AV : 910 € »
  est trop vague)
✘ Biens personnels (moto, voiture, immobilier, montre, etc.)
✘ Cartes / soldes bancaires (Revolut, CIC checking, etc.)
✘ Toute ligne qui n'est pas un instrument coté identifiable par un ticker

Pour chaque position retenue, fournis :

- **`ticker`** : symbole au **format Yahoo Finance**. Convertis depuis
  Google Finance si nécessaire :
  - `EPA:XYZ` → `XYZ.PA` (Euronext Paris)
  - `NASDAQ:XYZ` → `XYZ`
  - `NYSE:XYZ` → `XYZ`
  - `LON:XYZ` → `XYZ.L` (Londres)
  - `BIT:XYZ` → `XYZ.MI` (Milan)
  - `ETR:XYZ` → `XYZ.DE` (Xetra)
  - Cryptomonnaies : `BTC-USD`, `ETH-EUR`, `SOL-USD`, etc.
- **`name`** : nom officiel de l'instrument. Si tu reconnais avec certitude
  l'émetteur derrière un ticker obscur, utilise le nom complet ; sinon,
  garde le libellé brut tel qu'il apparaît dans la feuille.

Quand tu as terminé, appelle UNE SEULE FOIS l'outil
`mcp__djohodo_portfolio__submit_portfolio` avec la liste complète. NE
PUBLIE AUCUN TEXTE LIBRE — passe exclusivement par l'outil.
"""


# --- Public entrypoint -------------------------------------------------------


async def load_portfolio(json_path: Path | None = None) -> list[dict[str, str]]:
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
        csv_text = await _fetch_sheet_csv(sheet_url)
        return await _extract_holdings(csv_text)
    return _load_from_json(json_path or Path("portfolio.json"))


# --- Sheet path --------------------------------------------------------------


async def _fetch_sheet_csv(url: str) -> str:
    """Fetch the published CSV. Synchronous urllib runs on a worker thread
    so the event loop isn't blocked while Google Sheets does its thing
    (typically <500 ms but can spike under cache misses)."""

    def _do_fetch() -> str:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8-sig")  # tolerate BOM

    try:
        return await anyio.to_thread.run_sync(_do_fetch)
    except Exception as exc:
        raise RuntimeError(
            f"Could not fetch portfolio sheet at {url}: {exc}"
        ) from exc


async def _extract_holdings(csv_text: str) -> list[dict[str, str]]:
    """Ask the Agent SDK to extract tradable holdings from a messy CSV."""
    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            ClaudeAgentOptions,
            create_sdk_mcp_server,
            query,
            tool,
        )
    except ImportError as exc:  # pragma: no cover - environmental
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run: pip install -e ."
        ) from exc

    model = (
        os.environ.get("DJOHODO_PORTFOLIO_MODEL")
        or os.environ.get("DJOHODO_MODEL")
        or "claude-haiku-4-5-20251001"
    )

    captured: dict[str, Any] = {}

    @tool(
        "submit_portfolio",
        (
            "Submit the structured list of tradable holdings extracted "
            "from the spreadsheet. Call this exactly once."
        ),
        PORTFOLIO_EXTRACT_SCHEMA,
    )
    async def submit_portfolio(args: dict[str, Any]) -> dict[str, Any]:
        captured["holdings"] = args.get("holdings", [])
        return {"content": [{"type": "text", "text": "Liste enregistrée."}]}

    server = create_sdk_mcp_server(
        name="djohodo_portfolio", tools=[submit_portfolio]
    )

    options = ClaudeAgentOptions(
        model=model,
        mcp_servers={"djohodo_portfolio": server},
        allowed_tools=["mcp__djohodo_portfolio__submit_portfolio"],
        system_prompt=(
            "Tu es un assistant d'extraction. Tu ne dialogues pas. "
            "Tu n'expliques pas. Tu appelles l'outil avec le résultat "
            "structuré, point final."
        ),
    )

    prompt = _build_extract_prompt(csv_text)

    try:
        async for _message in query(prompt=prompt, options=options):
            pass  # data is collected by the tool handler via `captured`
    except Exception as exc:  # pragma: no cover - depends on SDK runtime
        raise RuntimeError(
            f"Portfolio extraction call failed: {exc}"
        ) from exc

    raw_holdings = captured.get("holdings")
    if raw_holdings is None:
        raise RuntimeError(
            "Portfolio extraction agent never called submit_portfolio. "
            "The CSV may be empty or unparseable."
        )

    cleaned = [
        {"ticker": h["ticker"], "name": h["name"]}
        for h in raw_holdings
        if isinstance(h, dict)
        and isinstance(h.get("ticker"), str) and h["ticker"].strip()
        and isinstance(h.get("name"), str) and h["name"].strip()
    ]
    if not cleaned:
        raise RuntimeError(
            "Portfolio extraction returned no usable holdings. "
            "Check that the sheet contains tradable instruments "
            "(equities, ETFs, crypto) with identifiable tickers."
        )
    return cleaned


# --- JSON path (local fallback) ---------------------------------------------


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
