# Djohodo

Daily portfolio news watch. Every morning at 07:30 Paris time, Djohodo reads
the last 24 hours of financial news for the stocks in your Google Sheet and
pushes a concise digest to your Telegram — impact rating, day-over-day
variation, and source links per position.

> Informational tool. **Not financial advice.**

## What you get

Each morning, a Telegram message that looks like this:

```
2026-06-03

▌ BNKE — Amundi Euro Stoxx Banks UCITS ETF Acc
▌ Euronext Paris
▌ 🟢 +2,45 %   (+125 €)
▌
▌ • BCE évoque un assouplissement monétaire avancé
▌   Impact: haussier — Les banques européennes profiteraient d'une remontée
▌   anticipée des marges nettes d'intérêt.
▌   Source: Reuters
```

One block per position, with clickable ticker → Yahoo Finance quote page.

## How it works

```
   Your Google Sheet                Cloudflare Worker
   (any layout — the LLM            cron @ 07:30 Paris
    extracts the positions)             │
            │                           │ workflow_dispatch
            ▼                           ▼
        ┌──────────────────────  GitHub Actions  ─────────────┐
        │  Read sheet → resolve tickers (Yahoo) → fetch news  │
        │  with Claude + WebSearch → render → push Telegram   │
        └─────────────────────────────────────────────────────┘
                                  │
                                  ▼
                              You read it
```

You can also type `/watch` to `@djohodobot` for an instant run any time.

## Setup

Three things to wire up — each has its own short guide:

1. **Your portfolio** in a Google Sheet (any layout, published as CSV).
   See the [Portfolio section](#portfolio) below.
2. **A Telegram bot** to receive the digest.
   See the [Telegram section](#telegram) below.
3. **A Cloudflare Worker** for the daily cron + `/watch` command.
   See [`telegram-trigger/README.md`](telegram-trigger/README.md) and
   [`docs/cron-schedule.md`](docs/cron-schedule.md).

Then add these secrets to the GitHub repo (`gh secret set NAME` or
Settings → Secrets):

| Secret | What it is |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` output. Carries your Anthropic subscription credit into CI. |
| `PORTFOLIO_SHEET_URL` | The CSV-export URL of your Google Sheet. |
| `TELEGRAM_BOT_TOKEN` | From `@BotFather` when you create the bot. |
| `TELEGRAM_CHAT_ID` | Your numeric Telegram id (ask `@userinfobot`). |

And one variable to turn Telegram delivery on:

```bash
gh variable set DJOHODO_TELEGRAM_ENABLED --body 1
```

## Daily use

- **`/watch`** in Telegram → digest in 1–2 minutes.
- The morning cron fires automatically at **07:30 Paris CEST** (06:30 in winter).
- `gh workflow run daily-watch.yml` from a terminal — same effect.

The digest is also saved as `digests/YYYY-MM-DD.md` in the repo.

## Cost

Each run consumes ~$0.30–0.60 of your Claude subscription credit. The
Cloudflare Worker and GitHub Actions are both on free tiers — $0/month
of extra infrastructure.

`total_cost_usd` is printed on stderr at the end of every run.

## Portfolio

Any Google Sheet layout works — a clean two-column ticker list or a full
patrimonial dashboard. Before each run, a cheap Haiku pre-pass (~$0.005)
extracts the tradable positions (equities, ETFs, crypto) and ignores the
rest (cash, savings accounts, livrets, biens personnels…). It also
normalises Google Finance prefixes (`EPA:AM`) to Yahoo format (`AM.PA`).

**Publish your sheet as CSV** — two equivalent paths:

- **File → Share → Publish to web → CSV** (recommended).
  Gives you a stable URL like `…/pub?output=csv`.
- **File → Share → Anyone with the link → Viewer**, then rewrite the share
  URL from `/edit?gid=0` to `/export?format=csv&gid=0`.

> Either makes the sheet readable by anyone holding the URL. Fine for
> tickers; don't add account numbers or other sensitive columns.

## Telegram

1. Message `@BotFather` → `/newbot` → save the token it gives you
   (`123456789:ABC…`).
2. Message `@userinfobot` → save your numeric id.
3. Open the new bot and press **Start** (Telegram requires this before a bot
   can DM you).

That's it. Add the two values as repo secrets (see [Setup](#setup)).

## Phase 2 — coming later

The [`analyst/`](analyst/) module is a documented placeholder for a
recommendation layer: consume the watcher's digest and surface
**buy / sell / watch** signals with rationale. Not built yet — see
[`analyst/README.md`](analyst/README.md) for the planned extension point.
