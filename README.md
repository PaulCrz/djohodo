# Djohodo

> *Djohodo* — a watcher perched on a roof, seeing everything and reporting back.

A personal automation that, **once per day every morning**, scans the last 24
hours of financial news for the stocks in your portfolio and produces a concise
Markdown digest: per holding, a short summary of any *material* news, its
likely impact (**bullish / bearish / neutral**) with a one-line rationale, and
the source URL.

It is an **informational decision-support tool**. It is **not financial advice**.

---

## Architecture

```
portfolio.json
      │
      ▼
 watcher/  ── builds prompt ──► Claude Agent SDK (WebSearch tool) ──► digest.md
      │                                                                 │
      │                                                                 ▼
      │                                                          delivery
      │                                                  (console, file, SMTP*)
      ▼
 analyst/  ── Phase 2 placeholder: future buy/sell/watch recommender
```

- **`watcher/`** — the only module with logic in Phase 1.
  - `prompt.py` — digest prompt template (French Markdown, "not advice" footer).
  - `agent.py` — orchestrates the async `query()` call, collects text blocks,
    captures `total_cost_usd`.
  - `delivery.py` — prints to stdout, writes `digests/YYYY-MM-DD.md`, optional
    SMTP email behind `DJOHODO_EMAIL_ENABLED=1`.
- **`analyst/`** — documented placeholder for Phase 2 ("help me decide"). See
  [`analyst/README.md`](analyst/README.md) for the planned extension point.
- **`main.py`** — CLI: loads portfolio, runs the watcher, delivers the digest.
  Supports `--dry-run` to preview the prompt without spending credit.
- **`.github/workflows/daily-watch.yml`** — daily cron in GitHub Actions.

---

## Setup

### 1. Install (Python 3.12)

```bash
git clone <this-repo> djohodo
cd djohodo
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure your portfolio

```bash
cp portfolio.example.json portfolio.json
# edit portfolio.json — list of { ticker, name }
```

`portfolio.json` is git-ignored.

### 3. Configure environment

```bash
cp .env.example .env
# edit .env if you want to switch models or enable email
```

---

## Authentication — two supported paths

Djohodo uses the **Claude Agent SDK** (`claude-agent-sdk`), so it inherits the
SDK's authentication rules. **Never hardcode credentials** — everything comes
from the environment.

### (a) Subscription credit (recommended for local use)

Use your Claude subscription's monthly Agent SDK credit:

1. Make sure `ANTHROPIC_API_KEY` is **unset** (the SDK prefers it when present).
   ```bash
   unset ANTHROPIC_API_KEY
   ```
2. Log into Claude Code once on this machine:
   ```bash
   claude login
   ```
3. Run Djohodo normally — the SDK will use your subscription credit.

For CI on this same path, use the **official Claude Code GitHub Actions
integration** (it carries subscription credit through the Action). The
included workflow shows the pay-as-you-go shape; switch the `Run Djohodo` step
to invoke the official Action when you want subscription billing in CI.

### (b) Pay-as-you-go API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

In CI, store it as the repository secret `ANTHROPIC_API_KEY` (the workflow
already references it via `${{ secrets.ANTHROPIC_API_KEY }}` — never inlined).

---

## Run

### Dry run (no model call, no cost)

```bash
python main.py --dry-run
```

Prints the fully-assembled prompt for inspection. Use this while iterating on
`watcher/prompt.py` or your `portfolio.json`.

### Real run

```bash
python main.py
```

This will:
1. Build the prompt from `portfolio.json`.
2. Call the Agent SDK with `WebSearch` allowed.
3. Print the digest, write it to `digests/YYYY-MM-DD.md`.
4. Optionally email it (if `DJOHODO_EMAIL_ENABLED=1`).
5. Print the model id and `total_cost_usd` on stderr.

### Override the model

```bash
python main.py --model claude-sonnet-4-6
# or
DJOHODO_MODEL=claude-sonnet-4-6 python main.py
```

---

## Scheduling (GitHub Actions)

`.github/workflows/daily-watch.yml` runs every day at **06:00 UTC** and on
manual `workflow_dispatch`. Required configuration:

**Repository secrets**
- `PORTFOLIO_JSON` — the full JSON payload of your `portfolio.json`.
- `ANTHROPIC_API_KEY` — only if you use the pay-as-you-go path.
- `SMTP_*` — only if you enable email delivery.

**Repository variables (optional)**
- `DJOHODO_MODEL` — override the model id (default: Haiku 4.5).
- `DJOHODO_EMAIL_ENABLED` — set to `1` to enable SMTP delivery.

Each successful run uploads the digest as a workflow artifact and commits
`digests/YYYY-MM-DD.md` back to the repo. The cron expression is in UTC —
adjust the `0 6 * * *` value if you want a different local morning time.

---

## Cost note

The default model is **Haiku 4.5** (`claude-haiku-4-5-20251001`), Anthropic's
cheapest current model. A daily run scanning ~5–10 tickers with a handful of
WebSearch calls is expected to cost a few cents per run on pay-as-you-go, or
to consume a small slice of your subscription credit.

Every run prints its `total_cost_usd` on stderr so you can monitor consumption.
Switch to Sonnet (`claude-sonnet-4-6`) via env or `--model` once you start
adding analyst-style reasoning in Phase 2.

---

## Roadmap

- **Phase 1 (this repo).** Daily news watch → Markdown digest. Done.
- **Phase 2.** Add `analyst/` recommendation layer: consumes the watcher's
  digest and surfaces `buy` / `sell` / `watch` signals with rationale. See
  [`analyst/README.md`](analyst/README.md).

---

*Djohodo is an informational tool. It does not constitute financial advice.*
