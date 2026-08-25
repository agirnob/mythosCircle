#!/usr/bin/env bash
# Restore world state + media from a snapshot.
#
# Usage: sudo ./restore.sh /var/lib/mythoscircle/backups/20260825T013000Z
#
# Stops the service, restores the DB with a WAL-safe .backup (the snapshot
# is read-only, so this is a consistent restore), restores media, restarts.
set -euo pipefail

SNAP="${1:?usage: restore.sh <snapshot-dir>}"
DATA_DIR="${MYTHOSCIRCLE_DATA_DIR:-/var/lib/mythoscircle}"
DB="$DATA_DIR/mythoscircle.db"

[ -f "$SNAP/mythoscircle.db" ] || { echo "no snapshot DB in $SNAP" >&2; exit 1; }

echo "Stopping mythoscircle..."
systemctl stop mythoscircle.service

mkdir -p "$DATA_DIR"
rm -f "$DB"
sqlite3 "file:$SNAP/mythoscircle.db?mode=ro" ".backup '$DB'"

if [ -f "$SNAP/media.tar.gz" ]; then
	rm -rf "$DATA_DIR/media"
	tar -xzf "$SNAP/media.tar.gz" -C "$DATA_DIR"
fi

echo "Starting mythoscircle..."
systemctl start mythoscircle.service
echo "restore complete from $SNAP"
