#!/bin/sh
set -eu
if [ "${PI_TELEGRAM_LIVE_E2E:-}" != 1 ]; then
  echo "set PI_TELEGRAM_LIVE_E2E=1 to run live E2E tests" >&2
  exit 0
fi
exec python3 -m pytest -q tests/test_telegram.py
