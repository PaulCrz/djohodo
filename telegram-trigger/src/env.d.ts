/**
 * Secrets augmentation.
 *
 * `wrangler types` regenerates `Env` from `wrangler.jsonc` bindings + vars
 * after every config change, but it has no visibility into secrets (those
 * are set out-of-band via `wrangler secret put` and intentionally never
 * live in config files). We extend `Env` here via TypeScript interface
 * declaration merging so the Worker still has type-checked access to them.
 *
 * Keep this file in sync with the secrets actually `put` for this Worker
 * (see README → Secrets section).
 */

declare global {
  interface Env {
    /** Bot HTTP token from @BotFather. */
    TELEGRAM_BOT_TOKEN: string;
    /**
     * Random 32-byte hex shared with Telegram via `setWebhook?secret_token=…`.
     * Validated timing-safe on every incoming webhook.
     */
    TELEGRAM_SECRET_TOKEN: string;
    /** Numeric Telegram user id whitelist — only this user can trigger. */
    TELEGRAM_AUTHORIZED_CHAT_ID: string;
    /** Fine-grained PAT, `PaulCrz/djohodo` only, `Actions: Read and write`. */
    GITHUB_PAT: string;
  }
}

export {};
