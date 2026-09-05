#!/bin/sh
set -eu
exec python3 -m pytest -q tests/test_telegram.py
