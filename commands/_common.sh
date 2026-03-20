#!/usr/bin/env bash
# Shared helpers for the commands/ scripts. Sourced, not executed.

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"  # always commands/_common.sh
BASE_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
WORKTREE_ROOT="$BASE_DIR/.worktrees"
PROJECT="${COMPOSE_PROJECT_NAME:-mbta}"
CONTAINER="mbta-monitor"

# Compose v2 ("docker compose") if present, else v1 ("docker-compose").
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

# Print the container id, or empty. $1: "running" (default) or "any".
container_id() {
  if [ "${1:-running}" = "any" ]; then
    docker ps -aq --filter "name=^/${CONTAINER}$"
  else
    docker ps -q --filter "name=^/${CONTAINER}$"
  fi
}
