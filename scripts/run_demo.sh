#!/usr/bin/env bash
# Keep ownership of exactly one child; never use pkill or alter existing rules.
set -euo pipefail
EARSHOT_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$EARSHOT_PROJECT_ROOT"
if [[ ! -f venv/bin/activate ]]; then
  echo 'Demo cannot start: venv is missing. Follow the README setup instructions first.' >&2
  exit 2
fi
source venv/bin/activate
EARSHOT_LAUNCH_STARTED="$(python -c 'import time; print(time.time())')"
export EARSHOT_LAUNCH_STARTED
python scripts/demo_preflight.py
export EARSHOT_DEMO=1
# config.py loads .env; the server defaults to offline when unset. Explicit
# shell variables still take precedence over .env through load_dotenv.
EARSHOT_DEMO_PID=''
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$EARSHOT_DEMO_PID" ]] && kill -0 "$EARSHOT_DEMO_PID" 2>/dev/null; then
    kill "$EARSHOT_DEMO_PID" 2>/dev/null || true
    wait "$EARSHOT_DEMO_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python -m uvicorn earshot.server:app --host 127.0.0.1 --port 8000 --workers 1 &
EARSHOT_DEMO_PID=$!
python scripts/demo_preflight.py --wait-pid "$EARSHOT_DEMO_PID" --timeout 55
wait "$EARSHOT_DEMO_PID"
