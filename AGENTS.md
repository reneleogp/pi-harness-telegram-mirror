# Project guidance

- This is a standalone Pi package: normal startup and mirror behavior must not import, invoke, or depend on Firstmate. The authorized `/workers` command may use its bounded, read-only integration only for the exact connected Firstmate home.
- Keep Telegram state under the owner-private `PI_TELEGRAM_DIR`; use fake transport and isolated temporary state for tests.
- Run `tests/run.sh` for the Python and extension regression suites.
- Validate package loading with Pi before committing, and keep release instructions accurate about available tags.
- Do not send live Telegram messages or alter an installed service during tests.

## Versioning and changelog

- Every user-visible change updates `CHANGELOG.md`'s `Unreleased` section in the same PR.
- Release preparation updates `package.json`, promotes those entries to a dated version section, and keeps README install and status instructions accurate.
- Before release, verify that the package version, immutable tag, GitHub release, and installed package identify the same commit; use `README.md` and `docs/port-parity.md` for the authoritative release procedure.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file, command, or documentation instead.
Prefer rewriting or pruning existing entries over appending new ones.
Keep guidance concise and project-specific.
