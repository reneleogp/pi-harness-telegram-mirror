# Pi Telegram Mirror

Standalone bidirectional Telegram mirror for one Pi terminal session.
Review extensions before installing: Pi packages run with full local permissions.
This package has no required Firstmate runtime dependency, database, web service, cloud queue, account system, telemetry, or generalized daemon.

## Install

The stable `v1` tag is published on `origin`.

For isolated validation from a checked-out commit, install the local package without changing global settings:

```sh
pi install -l /absolute/path/to/pi-harness-telegram-mirror
pi list
pi remove -l /absolute/path/to/pi-harness-telegram-mirror
```

Stable install commands are:

```sh
pi install git:github.com/reneleogp/pi-harness-telegram-mirror@v1
pi update git:github.com/reneleogp/pi-harness-telegram-mirror@v1
pi remove git:github.com/reneleogp/pi-harness-telegram-mirror
```

`package.json` is a Pi manifest (`pi-package`) and loads `extensions/telegram-mirror.ts`. Python runs from this stable installed package location and uses the standard library; `mistune` is optional for formatting.

## Private setup

Create a bot with BotFather. Pi's ordinary input is not secret entry, so write the token yourself:

```sh
mkdir -p ~/.pi-telegram; chmod 700 ~/.pi-telegram
printf 'TELEGRAM_BOT_TOKEN=%s\n' 'PASTE_TOKEN_HERE' > ~/.pi-telegram/env
chmod 600 ~/.pi-telegram/env
python3 /installed/package/bin/pi-telegram.py allow-root "$PWD"
python3 /installed/package/bin/pi-telegram.py pair
```

Pairing stores only one private sender/chat in owner-only `config.json`. Never put the token in argv, a service unit, logs, tests, README examples, or sessions.

## Ownership and security

`allow-root` records the exact canonical root. A session claims the mirror only when exact `ctx.cwd` matches a registered root; subdirectories and worktrees do not match. An owner-only atomic session record contains PID, UID, process-start identity, root, and random incarnation. Dead/reused records are safely reclaimable. The bot validates kernel identity, never client claims: Linux `SO_PEERCRED`; macOS kernel peer PID and `getpeereid` UID. A second session or unrelated session is refused and an ineligible session stays inert. Kernel peer credentials protect the bot boundary; a same-UID process that can execute the local helper is trusted by the ownership handoff.

State, token, config, socket, ownership, and audio are under `~/.pi-telegram`, so service execution never needs project-folder privacy permission. Files/config are 0600 and the directory is 0700.

## Service

```sh
python3 /installed/package/bin/pi-telegram.py service-unit
python3 /installed/package/bin/pi-telegram.py install-service
python3 /installed/package/bin/pi-telegram.py status
python3 /installed/package/bin/pi-telegram.py uninstall-service
```

Linux systemd user services and macOS LaunchAgents are supported. Linux uses `~/.config/systemd/user/pi-telegram.service`; macOS uses `~/Library/LaunchAgents/com.pi.telegram.plist`. Both contain only the stable script path, private directory, and required service PATH, never the token. Install, status, and uninstall are idempotent.

## Behavior and Pi commands

The extension registers `/telegram` and `/telegram-settings`; the latter controls footer visibility and delivery confirmations.
Telegram commands are `/telegram_on`, `/telegram_off`, `/telegram_status`, `/token_usage`, `/agent_info`, `/change_model`, `/change_thinking`, and `/workers` (plus spaced forms where applicable).
See [docs/telegram.md](docs/telegram.md) for agent-control behavior, ownership, confirmation, and worker status details.
`/token_usage` reports concise GPT quota windows in the paired private chat.
`/workers` optionally lists only the current connected Firstmate home's managed direct reports, with their recorded task descriptions, actual Herdr lifecycle status, and separately labeled current task progress.
It reports unavailable or not applicable rather than affecting normal mirror behavior when the session is not a Firstmate home, Herdr cannot be read, or a worker uses another runtime.
Text is in-memory FIFO with bounded frames and one session.
Accepted messages optionally receive `Pi · Sent to Pi.`.
Final visible replies are sent once, unthreaded.
Markdown, code, links, lists, and formatting are converted safely to bounded Telegram HTML chunks.
Terminal text and validated PNG/JPEG/WebP images are mirrored in both directions.
Unsupported/oversized files are rejected.
Unaccepted queue entries are lost on restart by design.

Voice notes receive a review card with Send, Edit, Cancel, bounded transcripts, one active transcription, bounded pending cards, and complete temporary-file cleanup.

## Local Parakeet V3 voice

Optional macOS or Linux setup:

```sh
# macOS: brew install ffmpeg uv
# Linux: install ffmpeg and uv with your distribution's package manager
uv tool install 'parakeet-mlx==0.5.2'
python3 /installed/package/bin/pi-parakeet-mlx-transcribe.py --help
# prewarm by transcribing a short local sample before going live
```

The adapter uses public `mlx-community/parakeet-tdt-0.6b-v3`, no cloud or required Hugging Face token, private temporary output, transcript-only stdout, bounded diagnostics, process-group termination, and cleanup. Cloud transcription is not required. On Linux, the service includes `~/.local/bin` so a `uv tool install` executable is available.

## Migration

After installing and connecting Pi, run:

```sh
python3 /installed/package/bin/pi-telegram.py migrate
```

It validates and copies token, pairing, and settings from the legacy `~/.firstmate-telegram` configuration into `~/.pi-telegram` without printing secrets or deleting or mutating the old directory. Retry is idempotent. Development and migration validation use isolated private state and fake transport. After installation, an operator may separately approve a real text, image, and voice verification through Pi before retiring the legacy setup; this is not part of automated testing and must not use test credentials or alter the installed service. The legacy path exists only in this explicit migration operation, not normal runtime.

## Troubleshooting, privacy, limitations

Run `status`, inspect `service-unit`, confirm `env`/`config.json` permissions, run `allow-root` from the intended root, and reload Pi. An unavailable footer means service/socket unavailable; inert sessions indicate an unregistered exact root or an existing owner. Telegram polling needs network access and one bot process. Queue state is not durable. Voice requires local ffmpeg and model dependencies. No telemetry or cloud copy is made beyond Telegram delivery.

## Verification

Fake transport tests use isolated state and no real token: `tests/run.sh`.
The non-live isolated regression wrapper is available as `tests/pi-telegram-live-e2e.test.sh`; it does not provide production Telegram coverage.
The current parity checklist and validation evidence are in `docs/port-parity.md`.
