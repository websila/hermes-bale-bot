# hermes-bale-bot

Hermes Agent **platform plugin** for [Bale](https://ble.ir) messenger.

It reuses Hermes’s Telegram adapter against Bale’s Telegram-compatible Bot API (`https://tapi.bale.ai/bot`), so you can run **Telegram and Bale on the same profile**.

> Persian: Hermes platform plugin for Bale bots. Install with the command below; get a token from `@botfather` in Bale.

## Requirements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) with Telegram platform dependencies installed (this plugin subclasses the bundled Telegram adapter)
- A Bale bot token from [@botfather](https://ble.ir/botfather) in Bale

## Install

```bash
hermes plugins install kordkheili/hermes-bale-bot --enable
```

Or from a full Git URL:

```bash
hermes plugins install https://github.com/kordkheili/hermes-bale-bot.git --enable
```

Then restart the gateway for the profile that should use Bale (on a multiplex host, restart the **default** gateway).

## Configuration

### Environment

| Variable | Required | Description |
| --- | --- | --- |
| `BALE_BOT_TOKEN` | yes | Bot token from Bale `@botfather` |
| `BALE_ALLOWED_USERS` | no | Comma-separated Bale user IDs allowed to talk to the bot |
| `BALE_ALLOW_ALL_USERS` | no | Set `true` to allow any user (dev/personal only) |
| `BALE_HOME_CHANNEL` | no | Default chat ID for cron / notification delivery |
| `BALE_REPLY_TO_MODE` | no | Reply threading mode (same idea as Telegram) |
| `BALE_ALLOW_BOTS` | no | Allow other bots |
| `BALE_GROUP_ALLOWED_CHATS` | no | Group chat allowlist |

Put these in the profile `.env` (or wherever that profile loads secrets).

### Profile config

Enable the platform in `config.yaml`:

```yaml
platforms:
  bale:
    enabled: true
```

Optional extras (defaults are already set by the plugin):

```yaml
platforms:
  bale:
    enabled: true
    extra:
      base_url: https://tapi.bale.ai/bot
      base_file_url: https://tapi.bale.ai/file/bot
```

Get your Bale user id by messaging the bot after it is connected (Hermes `/id` works the same way as on Telegram when that command is available).

## Behavior notes

- **Markdown:** Bale auto-renders its own Markdown dialect. This adapter converts GFM-style agent text to Bale’s spaced ` *bold* ` / ` _italic_ ` markers and sends **without** `parse_mode`. It does **not** use Telegram MarkdownV2 or HTML.
- **Approvals / clarify:** Telegram HTML cards (`<b>`, `<pre>`, `&#x27;`, …) are converted to plain Bale Markdown so tags do not show raw.
- **No Telegram proxy:** outbound traffic goes direct to `tapi.bale.ai` (no `TELEGRAM_PROXY` / api.telegram.org IP fallback).
- **Side-by-side:** Telegram stays independent; set both `platforms.telegram` and `platforms.bale` if you want both.

## Compatibility caveat

`hermes plugins validate` reports private Hermes imports (Telegram adapter internals, `gateway.*`, etc.). That is expected for a platform adapter today, but **Hermes upgrades can break this plugin** until it is updated. Pin a Hermes version you have tested, or re-validate after upgrades:

```bash
hermes plugins validate /path/to/hermes-bale-bot
```

## License

MIT — see [LICENSE](LICENSE).
