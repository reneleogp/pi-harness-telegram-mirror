# Changelog

All notable user-visible changes to this package are recorded here.

## Unreleased

No unreleased changes.

## [1.0.0] - 2026-09-09

### Added

- Bidirectional Telegram text, image, and final-reply mirroring for one eligible Pi session.
- Safe bounded formatting for Markdown, HTML, code, links, lists, and long replies.
- Local Parakeet V3 voice-note transcription with review, edit, send, cancel, and temporary-file cleanup.
- Telegram and terminal controls for mirror mode, settings, delivery confirmations, model and thinking changes, GPT quota, worker status, and connected-terminal reload.
- Owner-private pairing, exact-root session eligibility, one-session ownership, kernel peer checks, and safe legacy configuration migration.
- macOS LaunchAgent and Linux systemd user-service generation, installation, status, and removal.

### Security

- Normal startup and mirror behavior remain independent of Firstmate, with `/workers` limited to the authorized connected Firstmate home.
- Tokens, pairing data, sockets, ownership records, and voice audio remain under the owner-private `PI_TELEGRAM_DIR`.

### Testing

- Added portable regression, fake-transport, isolated end-to-end, package-load, and service-unit coverage for the shipped behavior.
- Live Telegram, live voice-model, and installed-service cutover checks remain operator actions rather than automated tests.
