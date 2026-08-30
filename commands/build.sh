#!/usr/bin/env bash
#
# Build and start the MBTA monitor.
#
#   ./commands/build.sh          Build the current checkout (src/).
#   ./commands/build.sh v0.5     Build the tagged version v0.5.
#
# Tagged builds check the tag out into a gitignored git worktree under
# .worktrees/ and build from there, so each version is built with its own
# Dockerfile, docker-compose.yml and requirements.txt. That matters: v0.1
# listens on port 4995 and needs `requests`, which later versions dropped.
#
# Only one version runs at a time -- the compose project and container name
# are fixed, so building a different version replaces the running one.
#
set -euo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/_common.sh"

TAG="${1:-}"

if [ -z "$TAG" ]; then
  CTX="$BASE_DIR"
  export APP_VERSION="dev"
else
  if ! git -C "$BASE_DIR" rev-parse -q --verify "refs/tags/${TAG}^{commit}" >/dev/null 2>&1; then
    echo "ERROR: no such tag: $TAG" >&2
    echo "Available versions:" >&2
    git -C "$BASE_DIR" tag --list 'v*' | sort -V | sed 's/^/  /' >&2
    exit 1
  fi
  CTX="$WORKTREE_ROOT/$TAG"
  export APP_VERSION="$TAG"

  want="$(git -C "$BASE_DIR" rev-parse "refs/tags/${TAG}^{commit}")"
  if [ -e "$CTX/.git" ]; then
    have="$(git -C "$CTX" rev-parse HEAD 2>/dev/null || echo none)"
    [ "$want" = "$have" ] || git -C "$CTX" checkout -q --detach "$want"
  else
    # Self-heal if the worktree dir was deleted out from under git.
    rm -rf "$CTX"
    git -C "$BASE_DIR" worktree prune
    mkdir -p "$WORKTREE_ROOT"
    git -C "$BASE_DIR" worktree add -q --detach "$CTX" "$want"
  fi
fi

# .env is gitignored, so a fresh worktree has none. Symlink rather than copy,
# so rotating the key in the main checkout takes effect everywhere.
if [ "$CTX" != "$BASE_DIR" ] && [ ! -e "$CTX/.env" ]; then
  if [ ! -f "$BASE_DIR/.env" ]; then
    echo "ERROR: $BASE_DIR/.env is missing. Run: cp .env.example .env" >&2
    exit 1
  fi
  ln -s "$BASE_DIR/.env" "$CTX/.env"
fi

# Only one version runs at a time: the container name is fixed, so compose
# fails with an opaque "name is already in use" error if another checkout or
# project already has one up. Catch that here and say what to do about it.
existing="$(container_id any)"
if [ -n "$existing" ]; then
  owner="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$existing" 2>/dev/null || true)"
  if [ -n "$owner" ] && [ "$owner" != "$PROJECT" ]; then
    echo "ERROR: a '${CONTAINER}' container already exists, owned by compose project '${owner}'." >&2
    echo "       Only one version can run at a time. Stop it first:" >&2
    echo "         COMPOSE_PROJECT_NAME=${owner} ./commands/down.sh" >&2
    exit 1
  fi
fi

export HOST_PORT="${HOST_PORT:-5000}"

echo "BASE_DIR    = $BASE_DIR"
echo "context     = $CTX"
echo "APP_VERSION = $APP_VERSION  ->  image mbta-monitor:$APP_VERSION"
echo "host port   = $HOST_PORT"

cd "$CTX"
compose -p "$PROJECT" up -d --build --remove-orphans
docker image prune -f >/dev/null 2>&1 || true

docker ps --filter "name=^/${CONTAINER}$" \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
