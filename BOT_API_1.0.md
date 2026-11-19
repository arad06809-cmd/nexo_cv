# NEXO Bot API 1.0 — implemented subset

Base URL: `https://YOUR-NEXO-DOMAIN`

Authentication: send `X-NEXO-BOT-TOKEN: <token>` and call `/api/bot/<method>` (recommended). The compatibility form `/bot<TOKEN>/<method>` is also accepted; avoid logging token-bearing URLs.

## Supported methods

| Method | HTTP | Parameters |
|---|---|---|
| `getMe` | POST | none |
| `getUpdates` | POST | `offset`, `limit` (1–100), `timeout` (0–20 seconds) |
| `sendMessage` | POST | `chat_id`, `text` (up to 4,000 chars) |
| `getChat` | POST | `chat_id` |
| `getUser` | POST | `chat_id` (user ID) |

All responses use `{ "ok": true, "result": ... }` on success. Authentication failures return HTTP 401. A user must explicitly start a bot using the authenticated NEXO endpoint before the bot can message them.

## Create and start a bot

Create from an authenticated NEXO session: `POST /api/bots` with `{ "name":"Support Bot", "username":"supporthelperbot" }`. The generated token is shown only once. Token rotation immediately invalidates the previous token.

An authenticated user starts the bot via `POST /api/bots/<bot_id>/start`. The bot receives a `/start` update through `getUpdates`. A bot reply is stored in the recipient's NEXO inbox and can be read from `GET /api/bot-inbox`.

## Long polling

Send `getUpdates` with an offset. After successfully handling update `N`, request the next batch with `offset=N+1`. The server retains updates until the offset acknowledges them.

## Webhook status

The webhook configuration endpoint validates HTTPS and stores the callback URL and a one-time secret. Outbound delivery is not enabled in this release; a dedicated delivery worker with retry, signature verification and SSRF protections must be deployed first. Do not rely on webhook delivery in production yet.

## Not yet implemented

`sendPhoto`, `sendVideo`, `sendDocument`, `sendAudio`, `editMessage`, `deleteMessage`, `answerCallbackQuery`, `sendKeyboard`, webhook delivery and BotFather conversational UI are outside this foundation release. Do not treat them as working methods.
