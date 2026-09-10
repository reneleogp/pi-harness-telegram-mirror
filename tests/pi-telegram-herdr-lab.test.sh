#!/bin/sh
set -eu

if [ "${PI_TELEGRAM_HERDR_LAB_E2E:-}" != 1 ]; then
  echo "Herdr named-lab E2E skipped (set PI_TELEGRAM_HERDR_LAB_E2E=1)."
  exit 0
fi

: "${HERDR_LAB_HELPER:?HERDR_LAB_HELPER must point to fm-herdr-lab.sh}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
HERDR_LAB_SESSION=$($HERDR_LAB_HELPER name telegram-herdr-sessions-test)
export HERDR_LAB_SESSION
trap '"$HERDR_LAB_HELPER" teardown "$HERDR_LAB_SESSION"' EXIT
"$HERDR_LAB_HELPER" provision "$HERDR_LAB_SESSION"
"$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" workspace create --cwd /tmp --label primary --no-focus >/dev/null
"$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" workspace create --cwd /tmp --label managed --no-focus >/dev/null
"$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" workspace create --cwd /tmp --label shell --no-focus >/dev/null

socket_path=$("$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" session list --json | python3 -c '
import json, sys
data = json.load(sys.stdin)
name = sys.argv[1]
print(next(item["socket_path"] for item in data["sessions"] if item["name"] == name))
' "$HERDR_LAB_SESSION")

python3 - "$ROOT" "$socket_path" <<'PY'
import importlib.util
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
socket_path = sys.argv[2]
spec = importlib.util.spec_from_file_location(
    "pi_telegram_workers_lab", root / "bin/pi_telegram_workers.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as directory:
    home = Path(directory)
    for name in ("state", "bin", "data"):
        (home / name).mkdir()
    (home / "bin/fm-crew-state.sh").write_text("")
    (home / "data/backlog.md").write_text("")
    messages = module.worker_messages(
        home, herdr_socket_path=socket_path, herdr_workspace_id="w1"
    )
    text = "\n".join(messages)
    for expected in ("Firstmate - primary", "Workspace - managed", "Workspace - shell"):
        assert expected in text, text
    assert "No Firstmate task records" not in text
PY
