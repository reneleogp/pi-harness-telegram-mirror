# Standalone Telegram port parity

This checklist records the standalone behavior covered by the package and the validation run for this commit.

## Parity checklist

- [x] Text delivery from Telegram to one eligible Pi session, with FIFO ordering, idle delivery, busy steering, acceptance receipts, retry, and bounded in-memory queues.
- [x] Text delivery from Pi to the paired Telegram chat, with final visible replies only and no duplicate threading.
- [x] PNG, JPEG, and WebP images in both directions, with magic validation, size limits, captions, albums, and unsupported-session handling.
- [x] Local Parakeet voice review, bounded transcript cards, edit, copy, send, cancel, stale-button protection, and temporary-file cleanup.
- [x] Mirror settings, footer visibility, delivery confirmations, status, and Telegram command aliases.
- [x] Private pairing, exact canonical root eligibility, one live session, kernel peer checks, owner-private state, and safe file boundaries.
- [x] Markdown and HTML-safe formatting with bounded chunks and plain-text fallback.
- [x] macOS LaunchAgent and Linux systemd user-service generation, installation, status, and removal paths.
- [x] Explicit migration of legacy token, pairing, and settings without changing the legacy directory.
- [x] Concise GPT quota reporting through the local Codex app-server without reading credentials or making inference calls.
- [x] Normal runtime contains no Firstmate file or command dependency; the legacy path is referenced only by explicit migration.

## Validation evidence

- `tests/run.sh` passed: 73 test executions (27 combined, 13 behavior-matrix, 6 package, and 27 combined through the non-live E2E wrapper).
- `python3 -m pytest -q` passed: 27 tests.
- `python3 -m pytest -q tests/test_behavior_matrix.py` passed: 8 tests.
- `python3 -m pytest -q tests/test_package.py` passed: 6 tests.
- `python3 -m pytest -q tests/test_telegram.py` passed: 13 tests.
- Pi extension delivery-mode smoke passed with Node's type stripping loader.
- Fake HTTP transport and real Unix-socket protocol smoke passed with isolated temporary state.
- Service-unit tests validated Linux systemd semantics on this host.
- macOS plist generation and LaunchAgent lifecycle were not executed on this Linux host.
- Live Telegram, live voice-model, and installed-service cutover tests were not run by design.
- The configured live model context requirement was not changed by this package; model selection remains Pi configuration.

## Release state

`git ls-remote --tags origin` returned no tags during this task, so `v1` is not an available install target.

After merge, firstmate needs explicit approval to create and push the immutable `v1` tag at the validated merge commit.

Only after that publication action should operators use the `pi install git:github.com/reneleogp/pi-harness-telegram-mirror@v1` command documented in the README.
