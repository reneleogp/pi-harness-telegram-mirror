#!/bin/sh
set -eu
python3 -m pytest -q tests
./tests/pi-telegram.test.sh
./tests/pi-telegram-extension.test.sh
./tests/pi-telegram-live-e2e.test.sh
