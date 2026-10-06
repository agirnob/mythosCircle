#!/usr/bin/env bash
# Run by GitHub Actions; reuse the existing Portainer-tracked Compose stack.
set -Eeuo pipefail

STATE_DIR="${MYTHOS_AUTO_STATE_DIR:-$HOME/.local/state/mythoscircle-deploy}"
COMPOSE_FILE="${MYTHOS_AUTO_COMPOSE_FILE:-$HOME/mythos-redeploy.yml}"
API_CONTAINER="${MYTHOS_AUTO_API_CONTAINER:-mythoscircle-api-1}"
WEB_CONTAINER="${MYTHOS_AUTO_WEB_CONTAINER:-mythoscircle-web-1}"
API_HEALTH="${MYTHOS_AUTO_API_HEALTH_URL:-http://127.0.0.1:8001/api/health}"
WEB_HEALTH="${MYTHOS_AUTO_WEB_HEALTH_URL:-http://127.0.0.1:8081/api/health}"
HEALTH_ATTEMPTS="${MYTHOS_AUTO_HEALTH_ATTEMPTS:-45}"
JOB_WAIT_ATTEMPTS="${MYTHOS_AUTO_JOB_WAIT_ATTEMPTS:-60}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$STATE_DIR"
exec 8>"$STATE_DIR/deploy.lock"
flock -n 8 || { echo "Another deployment holds the lock." >&2; exit 1; }
cd "$REPO_DIR"
REVISION="$(git rev-parse HEAD)"
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || exit 1
if [ -f "$STATE_DIR/deployed-revision" ] && [ "$(cat "$STATE_DIR/deployed-revision")" = "$REVISION" ]; then
    echo "Already deployed $REVISION"
    exit 0
fi
if [ -f "$STATE_DIR/failed-revision" ] && [ "$(cat "$STATE_DIR/failed-revision")" = "$REVISION" ]; then
    echo "Revision $REVISION previously failed; awaiting a new commit or manual retry."
    exit 1
fi
test -f "$COMPOSE_FILE"
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
active_jobs() {
    docker exec "$API_CONTAINER" python -c 'import sqlite3; db=sqlite3.connect("file:/data/mythoscircle.db?mode=ro",uri=True); print(db.execute("SELECT count(*) FROM job WHERE state IN (?,?)", ("queued", "running")).fetchone()[0])'
}
healthy() {
    local endpoint="$1"
    for ((attempt=1; attempt<=HEALTH_ATTEMPTS; attempt++)); do
        if curl --fail --silent --show-error --max-time 5 "$endpoint" |
            python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ok" else 1)' 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    return 1
}
for ((attempt=1; attempt<=JOB_WAIT_ATTEMPTS; attempt++)); do
    if [ "$(active_jobs)" = 0 ]; then break; fi
    echo "Waiting for generation jobs before deployment ($attempt/$JOB_WAIT_ATTEMPTS)."
    sleep 10
done
if [ "$(active_jobs)" != 0 ]; then
    echo "Generation jobs are still active; rerun this deployment later." >&2
    exit 1
fi

OLD_API="$(docker inspect -f '{{.Image}}' "$API_CONTAINER")"
OLD_WEB="$(docker inspect -f '{{.Image}}' "$WEB_CONTAINER")"
STARTED=0
DB_CHANGED=0
SNAPSHOT=""
rollback() {
    local failure="$?"
    trap - ERR
    printf '%s\n' "$REVISION" > "$STATE_DIR/failed-revision"
    if [ "$STARTED" = 1 ]; then
        echo "Deployment failed; restoring the previous release." >&2
        compose stop web api || exit 1
        if [ "$DB_CHANGED" = 1 ]; then
            docker run --rm --volumes-from "$API_CONTAINER" --entrypoint .venv/bin/python "$OLD_API" \
                -m app.core.restore apply "$SNAPSHOT" --data-dir /data || exit 1
        fi
        docker tag "$OLD_API" mythoscircle-api:local
        docker tag "$OLD_WEB" mythoscircle-web:local
        compose up -d --no-deps api || exit 1
        healthy "$API_HEALTH" || exit 1
        compose up -d --no-deps web || exit 1
        healthy "$WEB_HEALTH" || exit 1
        echo "Previous release restored; failed revision will not be retried automatically." >&2
    fi
    exit "$failure"
}
trap rollback ERR
if [ "${MYTHOS_AUTO_IMAGES_READY:-0}" != 1 ]; then
    docker build --label "org.opencontainers.image.revision=$REVISION" -f deploy/docker/Dockerfile.api -t "mythoscircle-api:git-$REVISION" .
    docker build --label "org.opencontainers.image.revision=$REVISION" -f deploy/docker/Dockerfile.web -t "mythoscircle-web:git-$REVISION" .
fi
for image in "mythoscircle-api:git-$REVISION" "mythoscircle-web:git-$REVISION"; do
    IMAGE_REVISION="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
    if [ "$IMAGE_REVISION" != "$REVISION" ]; then
        echo "Image $image does not match the checked-out release." >&2
        false
    fi
done
# A job may have arrived during the build. Check again before interrupting service.
if [ "$(active_jobs)" != 0 ]; then
    echo "Generation jobs arrived during the build; deferring deployment."
    exit 1
fi
STARTED=1
compose stop web
# Close the final race with an in-flight request before the web container stopped.
if [ "$(active_jobs)" != 0 ]; then
    compose up -d --no-deps web
    STARTED=0
    echo "Generation jobs became active; restored web service and deferred deployment."
    exit 1
fi
BACKUP_OUTPUT="$(docker exec "$API_CONTAINER" .venv/bin/python -m app.core.backup --data-dir /data --backup-dir /data/backups/autodeploy --retain 14)"
echo "$BACKUP_OUTPUT"
SNAPSHOT="$(printf '%s\n' "$BACKUP_OUTPUT" | sed -n 's/^mythoscircle backup complete: //p')"
[[ "$SNAPSHOT" == /data/backups/autodeploy/* ]]
docker tag "$OLD_API" mythoscircle-api:rollback-auto
docker tag "$OLD_WEB" mythoscircle-web:rollback-auto
docker tag "mythoscircle-api:git-$REVISION" mythoscircle-api:local
docker tag "mythoscircle-web:git-$REVISION" mythoscircle-web:local
DB_CHANGED=1
compose up -d --no-deps api
healthy "$API_HEALTH"
compose up -d --no-deps web
healthy "$WEB_HEALTH"
printf '%s\n' "$REVISION" > "$STATE_DIR/deployed-revision.tmp"
mv "$STATE_DIR/deployed-revision.tmp" "$STATE_DIR/deployed-revision"
rm -f "$STATE_DIR/failed-revision"
trap - ERR
echo "Successfully deployed $REVISION"
