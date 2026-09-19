#!/usr/bin/env bash
# Nightly snapshot of world state + media (see backup.cron for the schedule).
#
# Integrity (epic-6 story 6-1, AR13/AR30): the snapshot is taken by the
# python backup module — the sqlite3 *online backup API* (WAL-consistent,
# never a raw WAL-file copy), checksum written at creation, media
# manifest with per-file hashes, retention-pruned. This wrapper stays
# the cron's entrypoint: it keeps the flock single-instance guard and
# the clean no-database skip. No secrets involved (AD-22).
set -euo pipefail

DATA_DIR="${MYTHOSCIRCLE_DATA_DIR:-/var/lib/mythoscircle}"
DB="$DATA_DIR/mythoscircle.db"

# Single-instance guard (deferred 1.2, fixed in 1.7): a manual run
# overlapping the cron run must not race on the snapshot dir or the
# retention sweep. The module re-flocks the SAME lockfile for direct
# invocations (flock(2) is per-fd, both interoperate).
LOCK_FILE="$DATA_DIR/backup.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
	echo "another backup is already running -- exiting" >&2
	exit 1
fi

# Nothing to back up until the app has created the database.
if [ ! -f "$DB" ]; then
	echo "no database at $DB yet -- skipping"
	exit 0
fi

# Resolve the backend: deployed /opt/mythoscircle/{deploy,backend}, or a
# checkout's repo/{deploy,backend} — dirname(dirname(script))/backend is
# both. Prefer the venv, fall back to system python3 (the module is
# stdlib-only).
BACKEND="$(dirname "$(dirname "$(realpath "$0")")")/backend"
if [ -x "$BACKEND/.venv/bin/python" ]; then
	PYTHON="$BACKEND/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON="python3"
else
	echo "no python for the backup module (expected $BACKEND/.venv/bin/python)" >&2
	exit 1
fi

cd "$BACKEND"
MYTHOSCIRCLE_DATA_DIR="$DATA_DIR" "$PYTHON" -m app.core.backup