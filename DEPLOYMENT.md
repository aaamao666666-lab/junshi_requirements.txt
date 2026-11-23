# Deployment checklist

This repository includes a Render Blueprint and Docker image definition.

## Required before first deployment
1. Rotate the master Telegram bot token in BotFather because an older token was previously committed to source history.
2. In Render, create a new Blueprint from this repository and select branch `codex/safe-stabilization-2026-11-24`.
3. Add the rotated `MASTER_BOT_TOKEN` as a secret environment variable. Never commit it to Git.
4. Keep `ENABLE_GROUP_POSTS=false` until the group-posting behavior has been explicitly reviewed and accepted.
5. Confirm the persistent disk is mounted at `/var/data`; SQLite and bot JSON configuration are stored there.

## Current implementation boundary
- Telegram player registration and administrator approve/reject buttons are connected to SQLite.
- Admin commands: `/renew_terms amount|period_seconds|TRON_address|agreement_text|notice_text` and `/offer player_telegram_id`.
- Player consent is recorded in Telegram. It does not itself grant permission to transfer tokens.
- The TRON wallet authorization UI, production subscription contract, chain transaction monitor and scheduled keeper are not implemented or deployed yet. No automatic USDT charge can occur until those components are integrated and independently tested.
- This blueprint does not deploy itself. A Render account owner must create the service and supply the rotated Telegram token. The assistant cannot access Render account secrets from GitHub.
