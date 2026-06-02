# `djohodo-trigger` — Telegram → GitHub Actions bridge

Tiny Cloudflare Worker that lets you trigger the Djohodo daily watch on
demand by sending `/watch` to `@djohodobot` on Telegram. Receives the
Telegram webhook, authorises the caller, then calls GitHub's
`workflow_dispatch` API for `PaulCrz/djohodo`'s `daily-watch.yml`.

## Architecture

```
You ──/watch──► Telegram ──webhook──► this Worker
                                          │
                                          ├── 200 OK (immediate)
                                          ├── sendMessage "🟡 Veille en cours…"
                                          └── POST workflow_dispatch ──► GitHub Actions
                                                                              │
                                                                              ▼
                                                                       daily-watch.yml
                                                                              │
                                                                              ▼
                                                                  digest delivered on Telegram
```

## Security model

Two independent gates:

1. **`X-Telegram-Bot-Api-Secret-Token` header** — set by Telegram when
   forwarding the webhook, compared timing-safe against
   `TELEGRAM_SECRET_TOKEN`. Anything mismatching → 401.
2. **`message.from.id` whitelist** — must equal
   `TELEGRAM_AUTHORIZED_CHAT_ID`. Anyone else → silent 200 (don't even
   reveal the bot exists).

A 10-minute per-chat cooldown (KV-backed) prevents accidental
double-triggers from burning Anthropic credit.

## Secrets

Set via `wrangler secret put NAME` (interactive — values never enter
git or config files):

| Name | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot HTTP token from `@BotFather`. Same one used by the Python watcher. |
| `TELEGRAM_SECRET_TOKEN` | Random 32-byte hex string. Generate once via `openssl rand -hex 32`. Must match the `secret_token` passed to `setWebhook`. |
| `TELEGRAM_AUTHORIZED_CHAT_ID` | Numeric Telegram user id of the only allowed caller. |
| `GITHUB_PAT` | Fine-grained PAT, repo `PaulCrz/djohodo` only, permission `Actions: Read and write`. ~6-month expiry. |

## Non-secret config (in `wrangler.jsonc`)

| Variable | Default |
|---|---|
| `GITHUB_REPO` | `PaulCrz/djohodo` |
| `GITHUB_WORKFLOW` | `daily-watch.yml` |
| `GITHUB_REF` | `main` |
| `WATCH_COOLDOWN_SECONDS` | `600` (10 min) |

## One-time setup

1. **KV namespace** (cooldown storage):
   ```bash
   npx wrangler kv namespace create djohodo-trigger-rate
   ```
   Paste the returned `id` into `wrangler.jsonc`'s `kv_namespaces[0].id`.

2. **Secrets** (interactive):
   ```bash
   openssl rand -hex 32 | npx wrangler secret put TELEGRAM_SECRET_TOKEN
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_AUTHORIZED_CHAT_ID
   npx wrangler secret put GITHUB_PAT
   ```

3. **Generate `Env` type from current config**:
   ```bash
   npx wrangler types
   ```

4. **Deploy**:
   ```bash
   npx wrangler deploy
   ```
   Cloudflare returns the public URL, e.g.
   `https://djohodo-trigger.<your-subdomain>.workers.dev`.

5. **Tell Telegram about the webhook**:
   ```bash
   curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -H 'Content-Type: application/json' \
     -d '{
       "url":"https://djohodo-trigger.<subdomain>.workers.dev",
       "secret_token":"<the random hex from step 2>",
       "allowed_updates":["message"]
     }'
   ```

6. **Register `/watch` in the bot's command menu** so users see it in
   the `/` autocomplete:
   ```bash
   curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
     -H 'Content-Type: application/json' \
     -d '{"commands":[{"command":"watch","description":"Lancer la veille du jour"}]}'
   ```

## Day-to-day

- **Send `/watch`** to `@djohodobot` → ack arrives within ~5 s, digest
  1–2 min later via the existing pipeline.
- **Cooldown hit** → bot replies `⏳ Déjà déclenché il y a Xs, attends Ys.`
- **Watch live logs** during a deploy:
  ```bash
  npx wrangler tail
  ```

## Updating

Edit `src/index.ts` and `npx wrangler deploy`. Secrets persist across
deploys; KV data persists across deploys.

## Rotating secrets

`npx wrangler secret put NAME` overwrites the existing value. After
rotating `TELEGRAM_SECRET_TOKEN`, immediately re-run `setWebhook` with
the new value or Telegram's webhooks will start 401-ing.
