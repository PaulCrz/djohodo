/**
 * Djohodo Telegram trigger Worker.
 *
 * Receives Telegram webhooks for @djohodobot. When the authorized user
 * sends `/watch`, dispatches the `daily-watch.yml` workflow on GitHub
 * Actions and replies on Telegram with an acknowledgment. A 10-minute
 * per-chat cooldown (KV-backed) prevents accidental double-triggers
 * from burning Anthropic credit.
 *
 * Auth layers (defence in depth):
 *   1. `X-Telegram-Bot-Api-Secret-Token` header must match
 *      env.TELEGRAM_SECRET_TOKEN (set on Telegram's side via
 *      `setWebhook` with secret_token; compared timing-safe here).
 *   2. The Telegram `message.from.id` must equal
 *      env.TELEGRAM_AUTHORIZED_CHAT_ID — anyone else is silently
 *      ignored (returning 200 so Telegram doesn't retry).
 *
 * The webhook handler always returns 200 OK quickly; the reply
 * `sendMessage` calls run via `ctx.waitUntil()` so the response isn't
 * blocked on Telegram's API latency.
 */

// --- Telegram update shape (only what we use) -------------------------------

interface TelegramUpdate {
  message?: {
    chat: { id: number };
    from?: { id: number };
    text?: string;
  };
}

// --- Handler ----------------------------------------------------------------

export default {
  /**
   * Scheduled cron trigger — fires on the schedule declared in
   * wrangler.jsonc's `triggers.crons`. We dispatch the same GitHub
   * workflow as /watch, just tagged `triggered_by=worker-cron` for
   * observability. The handler returns fast; the actual HTTP call to
   * GitHub (and any failure notification to Telegram) runs in
   * ctx.waitUntil() so we don't block the Worker scheduler.
   */
  async scheduled(
    _controller: ScheduledController,
    env: Env,
    ctx: ExecutionContext
  ): Promise<void> {
    ctx.waitUntil(runScheduledWatch(env));
  },

  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext
  ): Promise<Response> {
    // Telegram only POSTs JSON webhooks. Reject anything else for clarity.
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    // (1) Verify the shared secret token Telegram includes on every
    // webhook (set via `setWebhook?secret_token=...`). Timing-safe to
    // avoid leaking the token via response-time side-channels.
    const incomingSecret =
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") ?? "";
    if (!timingSafeEqualStr(incomingSecret, env.TELEGRAM_SECRET_TOKEN)) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update: TelegramUpdate;
    try {
      update = (await request.json()) as TelegramUpdate;
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    const message = update.message;
    const chatId = message?.chat?.id;
    const fromId = message?.from?.id;
    const text = (message?.text ?? "").trim();

    // No message we can act on — could be a channel_post or other
    // update type. ACK to keep Telegram from retrying.
    if (chatId === undefined || fromId === undefined) {
      return new Response("ok");
    }

    // (2) Whitelist check. Anyone other than the owner is silently
    // ignored — don't even acknowledge, to avoid leaking the bot's
    // function to randos.
    if (String(fromId) !== env.TELEGRAM_AUTHORIZED_CHAT_ID) {
      return new Response("ok");
    }

    const command = parseCommand(text);

    if (command === "watch" || command === "start") {
      await handleWatch(env, ctx, chatId);
      return new Response("ok");
    }

    if (command === "help") {
      ctx.waitUntil(sendTelegram(env, chatId, HELP_TEXT));
      return new Response("ok");
    }

    if (command !== null) {
      ctx.waitUntil(
        sendTelegram(
          env,
          chatId,
          `Commande inconnue : <code>/${escapeHtml(command)}</code>\n\n${HELP_TEXT}`
        )
      );
    }
    // Non-command text → silent.
    return new Response("ok");
  },
} satisfies ExportedHandler<Env>;

// --- Command handlers -------------------------------------------------------

const HELP_TEXT =
  "Commandes disponibles :\n" +
  "• <code>/watch</code> — lancer la veille du jour\n" +
  "• <code>/help</code> — afficher cette aide";

async function handleWatch(
  env: Env,
  ctx: ExecutionContext,
  chatId: number
): Promise<void> {
  const cooldownSeconds = Number(env.WATCH_COOLDOWN_SECONDS) || 600;
  const key = `watch:${chatId}`;

  // Cooldown gate.
  const lastTriggerRaw = await env.RATE_LIMIT_KV.get(key);
  if (lastTriggerRaw) {
    const lastTrigger = Number(lastTriggerRaw);
    if (Number.isFinite(lastTrigger)) {
      const elapsed = Math.floor((Date.now() - lastTrigger) / 1000);
      const remaining = cooldownSeconds - elapsed;
      if (remaining > 0) {
        ctx.waitUntil(
          sendTelegram(
            env,
            chatId,
            `⏳ Déjà déclenché il y a ${formatDuration(elapsed)}.\n` +
              `Attends encore ${formatDuration(remaining)} avant un nouveau /watch.`
          )
        );
        return;
      }
    }
  }

  // Trigger the GitHub workflow. Catch and surface any error to the user
  // — silent failures would defeat the whole point of an on-demand trigger.
  try {
    await dispatchWorkflow(env, "telegram");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("workflow_dispatch failed:", msg);
    ctx.waitUntil(
      sendTelegram(
        env,
        chatId,
        `❌ Échec du déclenchement du workflow.\n<code>${escapeHtml(msg)}</code>`
      )
    );
    return;
  }

  // Cooldown stamp — only set AFTER a successful dispatch so a failed
  // attempt doesn't lock the user out.
  ctx.waitUntil(
    env.RATE_LIMIT_KV.put(key, String(Date.now()), {
      expirationTtl: cooldownSeconds,
    })
  );

  ctx.waitUntil(
    sendTelegram(
      env,
      chatId,
      "🟡 Veille en cours…\nLe digest arrive dans 1 à 2 min."
    )
  );
}

// --- GitHub Actions dispatch ------------------------------------------------

type TriggerSource = "telegram" | "worker-cron";

async function dispatchWorkflow(
  env: Env,
  triggeredBy: TriggerSource
): Promise<void> {
  const url =
    `https://api.github.com/repos/${env.GITHUB_REPO}` +
    `/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "djohodo-trigger (Cloudflare Worker)",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: env.GITHUB_REF,
      inputs: { triggered_by: triggeredBy },
    }),
  });

  // GitHub returns 204 No Content on success.
  if (response.status !== 204) {
    // Bounded read — error bodies from GitHub are short.
    const body = await response.text();
    throw new Error(
      `GitHub API ${response.status}: ${body.slice(0, 300)}`
    );
  }
}

// --- Scheduled handler ------------------------------------------------------

/**
 * Body of the cron-triggered run. No KV cooldown (the schedule itself
 * is the rate limit), no pre-ack message (the digest IS the
 * acknowledgment). If GitHub rejects the dispatch, surface the error
 * via Telegram so the user knows the morning digest is going to be
 * silent today.
 */
async function runScheduledWatch(env: Env): Promise<void> {
  try {
    await dispatchWorkflow(env, "worker-cron");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("scheduled dispatchWorkflow failed:", msg);
    try {
      await sendTelegram(
        env,
        Number(env.TELEGRAM_AUTHORIZED_CHAT_ID),
        `❌ Veille programmée du matin: échec du déclenchement.\n` +
          `<code>${escapeHtml(msg)}</code>`
      );
    } catch (notifyErr) {
      console.error("failed to notify on scheduled failure:", notifyErr);
    }
  }
}

// --- Telegram sendMessage ---------------------------------------------------

async function sendTelegram(
  env: Env,
  chatId: number,
  text: string
): Promise<void> {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  if (!response.ok) {
    const body = await response.text();
    console.error(
      `Telegram sendMessage failed: ${response.status} ${body.slice(0, 200)}`
    );
  }
}

// --- Helpers ----------------------------------------------------------------

/**
 * Strip a Telegram command from message text. Supports both `/cmd` and
 * `/cmd@botname` (which Telegram uses in group chats so other bots
 * ignore the command). Returns the lowercase command name without `/`,
 * or `null` for non-commands.
 */
function parseCommand(text: string): string | null {
  if (!text.startsWith("/")) return null;
  const head = text.slice(1).split(/\s+/)[0];
  if (!head) return null;
  return head.split("@")[0].toLowerCase();
}

/**
 * Constant-time comparison of two strings via Workers' non-standard
 * `crypto.subtle.timingSafeEqual` (which takes BufferSources of equal
 * length). The length check up front is both an early-exit and a
 * precondition of the call — `timingSafeEqual` throws on mismatched
 * lengths.
 */
function timingSafeEqualStr(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  const encoder = new TextEncoder();
  return crypto.subtle.timingSafeEqual(encoder.encode(a), encoder.encode(b));
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return rem === 0 ? `${minutes} min` : `${minutes} min ${rem} s`;
}
