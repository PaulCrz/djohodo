# Cron schedule

Djohodo runs on a daily cron to produce the news digest. **The cron is
fired from the Cloudflare Worker** (`telegram-trigger/`), which
dispatches the `daily-watch.yml` GitHub Actions workflow via
`workflow_dispatch`. The Python pipeline itself still runs in GitHub
Actions; only the scheduling lives at Cloudflare.

## Why not GitHub Actions `schedule`?

GitHub's scheduled-workflow events are routinely delayed during periods
of high load on the shared scheduler. Delays of 15 minutes to several
hours are normal — and the most popular cron minutes (`:00`, `:15`,
`:30`) are the most congested. We tried `:17` as an off-peak workaround;
runs still slipped into the afternoon.

Cloudflare Worker cron triggers fire **within seconds** of the declared
minute. The Worker then dispatches the GitHub workflow via
`workflow_dispatch`, which is event-driven (not polled) and queues
within ~30 seconds. End-to-end: digest arrives 1–2 min after the cron
time, predictably.

## Current setup — one run, morning

| Aspect | Value |
|---|---|
| Cron (UTC) | `30 5 * * *` |
| Paris CEST (Apr–Oct) | **07:30** |
| Paris CET (Nov–Mar) | 06:30 (1 hour earlier — see DST section) |
| Defined in | `telegram-trigger/wrangler.jsonc` → `triggers.crons` |
| Digest arrives | ~07:31–07:35 Paris CEST |

The 07:30 morning window catches:

- **US after-hours** from the previous trading session
  (NASDAQ / NYSE close at 22:00 Paris CEST)
- The **full Asian session** (Tokyo closes ~08:00 Paris)
- **European pre-market** news (FT, Reuters, Les Echos early editions)

Reading the digest before Paris opens at 09:00 leaves ~1h30 to act on
material news before either Euronext or NASDAQ pre-market reactions
start mattering.

## Alternative — two runs, morning + early afternoon

For more actionable coverage of US-driven positions (NASDAQ stocks,
US-tracking ETFs like `PUST.PA`), a second run before the NASDAQ open
catches morning Euro session news + US pre-market reactions.

To switch from one run to two:

```jsonc
// telegram-trigger/wrangler.jsonc
"triggers": {
  "crons": ["30 5 * * *", "30 11 * * *"]
}
```

Then redeploy:

```bash
cd telegram-trigger
npx wrangler deploy
```

| Run | Cron (UTC) | Paris CEST | Paris CET | What it catches |
|---|---|---|---|---|
| Morning | `30 5 * * *` | 07:30 | 06:30 | US after-hours + Asia + Euro pre-market |
| Afternoon | `30 11 * * *` | 13:30 | 12:30 | Euro morning session + US pre-market (2h before NASDAQ open at 15:30 CEST) |

**Cost.** Each run consumes ~$0.40–0.60 of Anthropic subscription
credit. Two runs/day ≈ $0.80–1.20/day ≈ $24–36/month — comfortably
inside the monthly subscription quota for a Pro plan.

**No GitHub change needed.** The `workflow_dispatch` event in
`daily-watch.yml` already accepts both runs without modification; the
Worker dispatches it twice per day.

## Daylight saving consideration

Cron expressions are evaluated in **UTC** on both Cloudflare and GitHub
Actions. They do not auto-shift for DST. So the hour-of-day in Paris
drifts by 1 hour each twice-yearly DST switch:

- **Last Sunday of March** — CET → CEST (clocks forward). Paris-local
  cron fires **1 hour later** than the day before.
- **Last Sunday of October** — CEST → CET (clocks back). Paris-local
  cron fires **1 hour earlier** than the day before.

With the current `30 5 * * *` UTC cron:
- Summer (CEST) → digest at 07:30 Paris ✓
- Winter (CET) → digest at 06:30 Paris

If the 1-hour drift is acceptable, leave the cron as-is.

If you want **strict 07:30 Paris** all year, switch the cron expression
at each DST transition:

| Period | Cron (UTC) | Paris |
|---|---|---|
| End of March → end of October | `30 5 * * *` | 07:30 CEST |
| End of October → end of March | `30 6 * * *` | 07:30 CET |

The switch is a 30-second edit + `wrangler deploy`. A calendar reminder
twice a year is the lightest-weight solution.

## Manual triggers (unchanged)

These all continue to work independently of the cron:

| Trigger | Mechanism | `triggered_by` value in run logs |
|---|---|---|
| Worker scheduled cron | Cloudflare → GitHub `workflow_dispatch` | `worker-cron` |
| Telegram `/watch` | Worker → GitHub `workflow_dispatch` | `telegram` |
| `gh workflow run daily-watch.yml` | direct GitHub API | `manual` (default) |
| GitHub Actions UI → "Run workflow" | direct GitHub API | `manual` (default) |

The `triggered_by` input is logged as a notice at the start of every
run, so the source of each run is visible in the Actions UI.

## Verifying the cron is registered

After deploying the Worker:

```bash
cd telegram-trigger
npx wrangler triggers list
```

Or open the Cloudflare dashboard → Workers → `djohodo-trigger` →
Triggers → Cron Triggers. The scheduled cron should appear with its
next-fire time.

You can also force a one-off scheduled invocation for testing without
waiting for the cron time:

```bash
# Send a synthetic scheduled event to the deployed Worker
curl -sS "https://djohodo-trigger.paul-creze0.workers.dev/__scheduled?cron=30+5+*+*+*"
```

The dev server (`wrangler dev`) supports `--test-scheduled` and the
`/cdn-cgi/handler/scheduled` endpoint for the same purpose locally.
