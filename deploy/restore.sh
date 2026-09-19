#!/usr/bin/env bash
# Restore world state + media from a snapshot — verify-then-apply (6-2).
#
# Usage: sudo ./restore.sh /var/lib/mythoscircle/backups/20260920T030000Z
#
# AR30/NFR5: the snapshot's trust chain is verified FIRST — checksums,
# manifest, and DB integrity — via the python restore module; a corrupt
# snapshot fails loudly with NOTHING applied and the service untouched.
# On a clean snapshot the service stops, the verified snapshot applies
# (atomic DB swap + media directory swap; any apply failure rolls the
# world back byte-identical), the DB + media tree are chowned back to the
# service user (the pre-6-2 script left root-owned files the unit could
# not write), and the service restarts. A failed apply leaves the service
# STOPPED and names the artifact — never a silent half-restore.
#
# Media relocation is honored through env inheritance: any
# MYTHOSCIRCLE_MEDIA_DIR the operator has set flows to the core unaltered
# (only MYTHOSCIRCLE_DATA_DIR is overridden here).
set -euo pipefail

SNAP="${1:?usage: restore.sh <snapshot-dir>}"
DATA_DIR="${MYTHOSCIRCLE_DATA_DIR:-/var/lib/mythoscircle}"
SERVICE_USER="${MYTHOSCIRCLE_SERVICE_USER:-mythoscircle}"

# Resolve the backend: deployed /opt/mythoscircle/{deploy,backend}, or a
# checkout's repo/{deploy,backend} — dirname(dirname(script))/backend is
# both. Prefer the venv, fall back to system python3 (the module is
# stdlib-only, mirroring backup.sh).
BACKEND="$(dirname "$(dirname "$(realpath "$0")")")/backend"
if [ -x "$BACKEND/.venv/bin/python" ]; then
	PYTHON="$BACKEND/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON="python3"
else
	echo "no python for the restore module (expected $BACKEND/.venv/bin/python)" >&2
	exit 1
fi

echo "Verifying snapshot $SNAP..."
if ! (cd "$BACKEND" && MYTHOSCIRCLE_DATA_DIR="$DATA_DIR" "$PYTHON" -m app.core.restore verify "$SNAP"); then
	echo "restore verify FAILED — nothing applied; mythoscircle.service untouched" >&2
	exit 1
fi

if ! systemctl stop mythoscircle.service; then
	echo "cannot stop mythoscircle.service" >&2
	exit 1
fi

if ! (cd "$BACKEND" && MYTHOSCIRCLE_DATA_DIR="$DATA_DIR" "$PYTHON" -m app.core.restore apply "$SNAP"); then
	echo "restore FAILED — mythoscircle.service left STOPPED; nothing restarted" >&2
	exit 1
fi

# The core writes as root (restore.sh is run via sudo); give the DB and
# media tree back to the service user or the unit cannot write them. The
# snapshots under backups/ stay operator-owned (they are the input this
# very chain trusts). The DB's filename comes from the manifest, hence
# the *.db glob (mythoscircle.db on prod, mythos.db on the dev box).
if [ "$(id -u)" -eq 0 ]; then
	chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/media" || {
		echo "chown of the restored media tree failed" >&2
		echo "mythoscircle.service left STOPPED; fix ownership then start it" >&2
		exit 1
	}
	if ! chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"/*.db 2>/dev/null; then
		echo "chown of the restored database failed" >&2
		echo "mythoscircle.service left STOPPED; fix ownership then start it" >&2
		exit 1
	fi
fi

if ! systemctl start mythoscircle.service; then
	echo "restore applied but mythoscircle.service FAILED TO START" >&2
	exit 1
fi
echo "restore complete from $SNAP"
