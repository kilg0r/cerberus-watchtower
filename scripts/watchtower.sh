#!/usr/bin/env bash
# Summon Cerberus Watchtower (macOS / Linux) - the Start-Watchtower.ps1 equivalent.
#
# Idempotent: if the server is already listening on :8765 it just opens the
# dashboard; otherwise it starts the server detached, waits for it, then opens.
#
#   scripts/watchtower.sh                 # start if needed + open app window
#   scripts/watchtower.sh --no-browser    # start/verify only
#   scripts/watchtower.sh --browser       # default browser instead of app window
#   scripts/watchtower.sh --stop          # shut the server down
#   scripts/watchtower.sh --status        # report without changing anything

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="http://127.0.0.1:8765"
PYTHON="$ROOT/.venv/bin/python"
LOG="$HOME/.cerberus-watchtower/server.log"

no_browser=0
force_browser=0
action="start"
for arg in "$@"; do
  case "$arg" in
    --no-browser) no_browser=1 ;;
    --browser)    force_browser=1 ;;
    --stop)       action="stop" ;;
    --status)     action="status" ;;
    -h|--help)    sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

is_up() { curl -fsS --max-time 2 "$URL/api/repos" >/dev/null 2>&1; }
server_pid() { lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null | head -1; }

case "$action" in
  status)
    if is_up; then echo "Watchtower is up at $URL (pid $(server_pid))"; else echo "Watchtower is not running."; fi
    exit 0
    ;;
  stop)
    pid="$(server_pid || true)"
    if [ -n "$pid" ]; then kill "$pid" && echo "Watchtower stopped (pid $pid)."; else echo "Watchtower is not running."; fi
    exit 0
    ;;
esac

if is_up; then
  echo "Watchtower already running at $URL"
else
  if [ ! -x "$PYTHON" ]; then
    echo "venv not found at $PYTHON - run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
  fi
  if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
    echo "warning: frontend/dist not built - the dashboard UI will 404. Run: (cd frontend && npm run build)" >&2
  fi
  echo "Starting Watchtower..."
  mkdir -p "$(dirname "$LOG")"
  (cd "$ROOT" && nohup "$PYTHON" -m watchtower >>"$LOG" 2>&1 &)

  for _ in $(seq 1 40); do          # 40 x 0.4s = 16s
    is_up && break
    sleep 0.4
  done
  if ! is_up; then
    echo "Watchtower did not come up within ~16s - see $LOG" >&2
    exit 1
  fi
  echo "Watchtower up at $URL"
fi

[ "$no_browser" -eq 1 ] && exit 0

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ "$force_browser" -eq 0 ] && [ -x "$CHROME" ]; then
  # chromeless app window - native feel, its own Dock identity
  "$CHROME" --app="$URL" >/dev/null 2>&1 &
else
  open "$URL" 2>/dev/null || xdg-open "$URL" >/dev/null 2>&1 || true
fi
