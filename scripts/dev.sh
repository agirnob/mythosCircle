#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="$project_dir/backend"
frontend_dir="$project_dir/frontend"
data_dir="$project_dir/backend/data"
mkdir -p "$data_dir/media"

if [[ ! -x "$backend_dir/.venv/bin/uvicorn" || ! -x "$frontend_dir/node_modules/.bin/vite" ]]; then
  echo 'Dev dependencies are missing. Run make setup first.' >&2
  exit 1
fi

export MYTHOSCIRCLE_DB="${MYTHOSCIRCLE_DB:-sqlite:///$data_dir/mythosCircle.db}"
export MYTHOSCIRCLE_MEDIA_DIR="${MYTHOSCIRCLE_MEDIA_DIR:-$data_dir/media}"
export MYTHOSCIRCLE_LOG_FILE="${MYTHOSCIRCLE_LOG_FILE:-$data_dir/app.jsonl}"

backend_pid=''
frontend_pid=''
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$frontend_pid" ]]; then kill "$frontend_pid" 2>/dev/null || true; fi
  if [[ -n "$backend_pid" ]]; then kill "$backend_pid" 2>/dev/null || true; fi
  if [[ -n "$frontend_pid" ]]; then wait "$frontend_pid" 2>/dev/null || true; fi
  if [[ -n "$backend_pid" ]]; then wait "$backend_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo 'Starting backend at http://127.0.0.1:8000/api/health'
(cd "$backend_dir" && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000) &
backend_pid=$!

echo 'Starting frontend at http://127.0.0.1:5173/'
(cd "$frontend_dir" && exec node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort) &
frontend_pid=$!

set +e
wait -n "$backend_pid" "$frontend_pid"
status=$?
set -e
exit "$status"
