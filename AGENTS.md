# Project guidance

- This is a standalone Pi package: normal runtime code must not import, invoke, or depend on Firstmate.
- Keep Telegram state under the owner-private `PI_TELEGRAM_DIR`; use fake transport and isolated temporary state for tests.
- Run `tests/run.sh` for the Python and extension regression suites.
- Validate package loading with Pi before committing, and keep release instructions accurate about available tags.
- Do not send live Telegram messages or alter an installed service during tests.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file, command, or documentation instead.
Prefer rewriting or pruning existing entries over appending new ones.
Keep guidance concise and project-specific.
