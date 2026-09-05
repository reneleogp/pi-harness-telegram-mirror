#!/bin/sh
set -eu
python3 -m pytest -q tests/test_behavior_matrix.py tests/test_package.py tests/test_telegram.py
./tests/pi-telegram.test.sh
./tests/pi-telegram-extension.test.sh
./tests/pi-telegram-live-e2e.test.sh
