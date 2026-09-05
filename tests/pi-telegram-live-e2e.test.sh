#!/bin/sh
set -eu
if [ "${PI_TELEGRAM_LIVE_E2E:-}" != 1 ]; then
  exec python3 -m pytest -q tests/test_telegram.py -k fake_telegram_transport
fi
: "${PI_TELEGRAM_DIR:?PI_TELEGRAM_DIR is required for live E2E}"
exec python3 -m pytest -q tests/test_telegram.py -k 'fake_telegram_transport or migration or oversized'
