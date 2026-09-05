#!/bin/sh
set -eu
if [ "${PI_TELEGRAM_LIVE_E2E:-}" = 1 ]; then
  : "${PI_TELEGRAM_DIR:?PI_TELEGRAM_DIR is required for live E2E}"
fi
exec python3 -m pytest -q tests
