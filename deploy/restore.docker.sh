#!/usr/bin/env bash
# Restore the deployed DOCKER stack from a snapshot — verify-then-apply
# (6-2). Laptop-side counterpart to restore.sh (systemd dev box); ships
# alongside backup.docker.cron, scp'd to the docker host like it.
#
# Usage: ./restore.docker.sh <snapshot-dir> [container]
#
# The snapshot dir is a path INSIDE the api container's volume (e.g.
# /data/backups/20260920T030000Z — snapshots live in /data/backups in
# the same mythos-data volume, matching backup.docker.cron). The api
# container stops, the verified snapshot applies into the volume via an
# ephemeral container sharing it (the Dockerfile has no USER directive,
# so apply writes as root — same owner the api container uses), and the
# api container restarts. A corrupt snapshot fails the verify step with
# the container still RUNNING and nothing applied; a failed apply leaves
# the container stopped, naming the artifact.
#
# The ephemeral container runs the LOCAL image — the stack must have
# been rebuilt/redeployed since this story landed (the image must ship
# app.core.restore; an older image fails the verify loudly with
# ModuleNotFoundError, which is also the "not gate-ready" signal).
# "--entrypoint" is explicit so an image ENTRYPOINT cannot swallow the
# python invocation (docker exec in backup.docker.cron masks this class
# of bug; docker run does not).
set -euo pipefail

SNAP="${1:?usage: restore.docker.sh <snapshot-dir> [container]}"
CONTAINER="${2:-mythoscircle-api-1}"
DATA_DIR="${MYTHOSCIRCLE_DATA_DIR:-/data}"

# The image the stack runs on — needed to spawn the ephemeral apply
# container (docker inspect of a stopped container still works).
IMAGE="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"

echo "Verifying snapshot $SNAP..."
if ! docker run --rm --volumes-from "$CONTAINER" \
	--entrypoint .venv/bin/python "$IMAGE" \
	-m app.core.restore verify "$SNAP" --data-dir "$DATA_DIR"; then
	echo "restore verify FAILED — nothing applied; $CONTAINER still running" >&2
	exit 1
fi

if ! docker stop "$CONTAINER"; then
	echo "cannot stop $CONTAINER" >&2
	exit 1
fi

if ! docker run --rm --volumes-from "$CONTAINER" \
	--entrypoint .venv/bin/python "$IMAGE" \
	-m app.core.restore apply "$SNAP" --data-dir "$DATA_DIR"; then
	echo "restore FAILED — $CONTAINER left STOPPED; nothing restarted" >&2
	exit 1
fi

if ! docker start "$CONTAINER"; then
	echo "restore applied but $CONTAINER FAILED TO START" >&2
	exit 1
fi
echo "restore complete from $SNAP"
