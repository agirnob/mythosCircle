#!/usr/bin/env bash
# Nightly snapshot of world state + media (see backup.cron for the schedule).
#
# WAL-safe: uses sqlite3 .backup (online, consistent snapshot) rather than
# copying the live file. No secrets involved (AD-22).
set -euo pipefail

DATA_DIR="${MYTHOSCIRCLE_DATA_DIR:-/var/lib/mythoscircle}"
DB="$DATA_DIR/mythoscircle.db"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$DATA_DIR/backups/$STAMP"

# Single-instance guard (deferred 1.2, fixed in 1.7): a manual run
# overlapping the cron run must not race on the snapshot dir or the
# retention sweep.
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

mkdir -p "$DEST"
sqlite3 "file:$DB?mode=ro" ".backup '$DEST/mythoscircle.db'"
if [ -d "$DATA_DIR/media" ]; then
	tar -czf "$DEST/media.tar.gz" -C "$DATA_DIR" media
fi
chmod 700 "$DEST"

# Retention: keep the newest 14 snapshots.
ls -1dt "$DATA_DIR"/backups/*/ 2>/dev/null | tail -n +15 | xargs -r rm -rf

echo "mythoscircle backup complete: $DEST"
