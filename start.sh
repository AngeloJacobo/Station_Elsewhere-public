#!/usr/bin/env bash
# Station Elsewhere on Linux / GitHub Codespaces: ./start.sh   (Ctrl+C stops it)
# The first start installs everything it needs (tools/setup_linux.sh), then the app opens on port 8767.
set -euo pipefail
cd "$(dirname "$0")" || exit 1
ENV_FILE="$HOME/.config/storycraft/env"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE" || exit 1
  set +a
fi
bash tools/setup_linux.sh || { echo "Setup failed: see the messages above."; exit 1; }
if [ "${1:-}" = "--restart" ]; then
  if [ ! -f ../StoryCraft/codespace/restart.sh ]; then
    echo "--restart requires the sibling StoryCraft/codespace/restart.sh helper." >&2
    exit 1
  fi
  . ../StoryCraft/codespace/restart.sh
  PORT="$(.venv/bin/python -c 'from app.config import CONFIG; print(int(CONFIG.get("port", 8767)))')"
  restart_server "$PORT"
fi

export PATH="$PWD/ffmpeg/bin:$PATH"
export STORYCRAFT_NO_BROWSER="${STORYCRAFT_NO_BROWSER:-1}"
# Opened when the browser has no autosaved work yet (e.g. the first visit in a new Codespace).
if [ -z "${STORYCRAFT_START_PROJECT+x}" ] && [ -f projects/first_project.json ]; then
  export STORYCRAFT_START_PROJECT="projects/first_project.json"
fi
if [ "${CODESPACES:-}" = "true" ]; then export STORYCRAFT_HOST="${STORYCRAFT_HOST:-0.0.0.0}"; fi

if [ -f ../StoryCraft/app/shared_autostop.py ] && [ -n "${CODESPACE_NAME:-}" ]; then
  SERVER_PID=""
  FEED_PID=""
  cleanup() {
    for pid in "$SERVER_PID" "$FEED_PID"; do
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        wait "$pid" || true
      fi
    done
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  .venv/bin/python -m app.main 9>&- &
  SERVER_PID=$!
  if [ "${1:-}" = "--restart" ]; then
    for _ in {1..60}; do
      curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$PORT/api/instance" >/dev/null 2>&1 && break
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 1
    done
    if ! kill -0 "$SERVER_PID" 2>/dev/null ||
       ! curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$PORT/api/instance" >/dev/null 2>&1; then
      echo "Station Elsewhere did not start. Check the server messages above." >&2
      exit 1
    fi
    release_launch_lock
  fi
  .venv/bin/python ../StoryCraft/codespace/progress_feed.py "$PWD/workspace/monitor/events.jsonl" &
  FEED_PID=$!
  wait "$SERVER_PID"
else
  exec .venv/bin/python -m app.main 9>&-
fi
