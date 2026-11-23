# KK Cloudflare migration

## Target architecture
- Telegram delivers updates to Cloudflare Workers webhooks; no long-running polling process.
- D1 stores tenants, admins, group bindings, player registrations, bets, daily totals and update idempotency.
- Bot tokens are encrypted with AES-GCM using a Cloudflare Worker secret. Raw tokens must never be committed or stored as plaintext.
- Each bot gets a tenant-scoped webhook route and independently generated webhook secret.
- Group posting remains disabled by default until an administrator explicitly enables it.

## Existing behavior to preserve
1. Master administrator identity: @am8818888.
2. Master bot binds/unbinds child bots and views per-bot daily reports.
3. Child bot owner panel, /bindgroup, auto-bet toggle and stage reset.
4. Player private registration and owner approval.
5. Each player may bind no more than two target groups.
6. Canada28 result polling/settlement, six-stage progression (100, 200, 400, 800, 1600, 3200).
7. Game categories/odds variants must remain separate configuration, not separate result feeds.
8. Renewal consent is only a record of consent; it must never be treated as wallet authorization or an on-chain charge.

## Migration safety
- This branch is isolated from the currently running Python bot.
- Do not change Telegram webhooks or rotate tokens during development.
- Do not enable automatic group posts during tests.
- Before cutover, import and validate tenant configuration, run D1 migrations, verify webhook signatures, test admin/player flows in a private test bot, and prepare rollback.
- Production cutover requires Cloudflare account deployment access and bot tokens supplied as Worker secrets or encrypted through the Worker setup flow.

## Current status
The D1 schema and Worker deployment configuration are scaffolding only. The webhook handlers, token encryption, setup automation, result source adapter, and full command/callback parity still need implementation and tests. No live Worker or Telegram webhook has been changed.
